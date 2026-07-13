"""The data-collection core: fetch, dedupe, geocode, enrich, persist.

This module is deliberately independent of the map renderer. Its single
entry point, :func:`collect_once`, runs one collection cycle and returns
whether anything new was stored — everything else in the application
(rendering, analytics) is derived from the data this module writes.

Collection guarantees:
  - **No duplicates.** ``StateStore`` keys every incident by a synthesized
    ``incident_number`` and additionally by a whitespace/case-normalized
    content key, so feed formatting jitter cannot re-insert an incident.
  - **Bounded API spend.** Geocoding draws from a persistent rolling-24h
    budget; repeated failures are blacklisted per address; a reserve keeps
    brand-new incidents ahead of backlog retries.
  - **Nothing is lost.** Incidents that cannot be geocoded yet (budget, API
    trouble) are stored without coordinates and drained later by
    :func:`_geocode_unlocated_incidents`.
"""

from typing import Optional

from lafayette911.config import Config
from lafayette911.enrichment import _enrich_incident_time
from lafayette911.fetch_incidents import (
    estimate_needed_geocode_requests,
    fetch_traffic_data,
    geocode_incidents,
    normalize_geocode_key,
    parse_traffic_data,
)
from lafayette911.state_store import StateStore
from lafayette911.utils import log_event
from lafayette911.weather import fetch_nws_alerts, fetch_weather_snapshot


# Geocoded results must resolve to one of these Google address components …
# anything else (a same-named street in another parish) is rejected.
LAFAYETTE_PARISH_PLACES = {
    "Lafayette",
    "Lafayette Parish",
    "Carencro",
    "Broussard",
    "Youngsville",
    "Scott",
    "Duson",
    "Milton",
}

LAF_LAT_MIN = 29.50
LAF_LAT_MAX = 31.00
LAF_LON_MIN = -92.25
LAF_LON_MAX = -91.90


def _in_lafayette_bounds(lat, lng) -> bool:
    try:
        lat = float(lat)
        lng = float(lng)
    except Exception:
        return False
    return (LAF_LAT_MIN <= lat <= LAF_LAT_MAX) and (LAF_LON_MIN <= lng <= LAF_LON_MAX)


def _has_allowed_lafayette_place(address_components) -> bool:
    if not address_components:
        return False
    allowed = {name.strip().casefold() for name in LAFAYETTE_PARISH_PLACES}
    for comp in address_components:
        long_name = (comp.get("long_name") or "").strip().casefold()
        if long_name in allowed:
            return True
    return False


# Google result types that mean "this is a real, specific place" regardless
# of location_type (schools/businesses report GEOMETRIC_CENTER but are
# precise enough to pin).
_PRECISE_RESULT_TYPES = {
    "street_address", "premise", "subpremise", "intersection", "street_number",
    "establishment", "point_of_interest", "school", "park", "church",
    "hospital", "shopping_mall", "university", "airport", "transit_station",
}
# Result types that mean Google gave up and matched an AREA: the locality
# centroid (downtown Lafayette — the Rue Vermilion / S Buchanan pile-up), a
# zip code, a neighborhood… Accepting these pins every unresolvable address
# onto the same handful of shared points.
_AREA_RESULT_TYPES = {
    "locality", "postal_code", "neighborhood", "sublocality",
    "sublocality_level_1", "administrative_area_level_1",
    "administrative_area_level_2", "country",
}


def _acceptable_geocode_precision(location_type: str, result_types) -> bool:
    """True when a Google result is precise enough to place a map point.

    Rejections flow through the ordinary failure path: the incident stays in
    the "locating…" queue, the address accrues failure attempts, and after
    the limit it retires to unmappable — honest, instead of a wrong dot at
    the city centroid or a road midpoint.
    """
    types = set(result_types or [])
    if types & _PRECISE_RESULT_TYPES:
        return True
    lt = str(location_type or "").upper()
    if lt in ("ROOFTOP", "RANGE_INTERPOLATED"):
        return True
    if types & _AREA_RESULT_TYPES:
        return False          # city / zip / neighborhood centroid
    if lt == "APPROXIMATE":
        return False
    if "route" in types:
        return False          # midpoint of a whole road, not an address
    return True


def _filter_geocode_results(incidents, location_cache: Optional[dict] = None):
    def _reject_fresh_result(inc):
        # geocode_incidents caches fresh Google results before this validation
        # runs; drop rejected coords from the cycle cache so later incidents at
        # the same address don't silently reuse them.
        if location_cache is not None:
            location_cache.pop(inc.get("location", ""), None)

    def _strip_geo_meta(inc):
        inc.pop("address_components", None)
        inc.pop("geo_location_type", None)
        inc.pop("geo_types", None)

    for inc in incidents:
        lat = inc.get("latitude")
        lng = inc.get("longitude")
        if lat is None or lng is None:
            _strip_geo_meta(inc)
            continue
        # Coordinates that came from the location cache have no
        # 'address_components' key — only Google-geocoded results include that
        # field.  Cache entries have already been validated when originally
        # geocoded, so only re-apply the bounding-box sanity check.
        if "address_components" not in inc:
            if not _in_lafayette_bounds(lat, lng):
                inc["latitude"] = None
                inc["longitude"] = None
            continue
        comps = inc.get("address_components") or []
        if not _has_allowed_lafayette_place(comps):
            inc["latitude"] = None
            inc["longitude"] = None
            _strip_geo_meta(inc)
            _reject_fresh_result(inc)
            continue
        if not _acceptable_geocode_precision(inc.get("geo_location_type"), inc.get("geo_types")):
            inc["latitude"] = None
            inc["longitude"] = None
            _strip_geo_meta(inc)
            _reject_fresh_result(inc)
            continue
        if not _in_lafayette_bounds(lat, lng):
            inc["latitude"] = None
            inc["longitude"] = None
            _reject_fresh_result(inc)
        _strip_geo_meta(inc)
    return incidents


def _record_geocode_outcomes(store: StateStore, incidents, attempted_keys: set) -> None:
    """Persist per-address geocode outcomes into the negative cache.

    Addresses that were actually sent to Google this batch and still ended up
    without coordinates (no result, or rejected by the parish validator) get a
    failure recorded; addresses that resolved have their failure history
    cleared. Addresses never attempted (cache hits, budget exhausted, negative
    cache) are left untouched.
    """
    if not attempted_keys:
        return
    succeeded = set()
    for inc in incidents:
        if inc.get("latitude") is not None and inc.get("longitude") is not None:
            succeeded.add(normalize_geocode_key(inc.get("location", "")))
    failed = attempted_keys - succeeded
    if failed:
        store.record_geocode_failures(sorted(failed))
    resolved = attempted_keys & succeeded
    if resolved:
        store.clear_geocode_failures(sorted(resolved))


def _geocode_unlocated_incidents(
    config: Config,
    store: StateStore,
    session,
    logger,
    location_cache: dict,
    skip_keys: set,
) -> bool:
    """Drain the queue of incidents stored without coordinates, fail-safe.

    Guarantees:
      - addresses in the persistent negative cache are never sent to Google,
        and their queued incidents are retired so they don't clog the batch;
      - a budget reserve is kept for brand-new incidents, so the backlog only
        spends leftover budget and simply waits when there is none;
      - an incident's retry lifetime (geocode_attempts) is consumed only by
        real API attempts — budget starvation never retires anything.

    Returns True if any incidents gained coordinates (caller should re-render).
    """
    if not config.google_api_key:
        return False

    unlocated = store.get_unlocated_incidents(limit=config.geocode_retry_batch)
    if not unlocated:
        return False

    # Incidents at blacklisted addresses can never geocode; retire them from
    # the queue so they stop occupying batch slots and blocking the incidents
    # behind them. Their addresses stay in the negative cache and get another
    # chance when the blacklist entry expires.
    blacklisted = [
        inc for inc in unlocated
        if normalize_geocode_key(inc.get("location", "")) in skip_keys
    ]
    if blacklisted:
        store.retire_unlocated_incidents(
            [inc["incident_number"] for inc in blacklisted],
            attempts=config.geocode_failure_max_attempts,
        )
        log_event(logger, "geocode_retry_retired", count=len(blacklisted), reason="blacklisted_address")

    needed = estimate_needed_geocode_requests(unlocated, location_cache, skip_keys=skip_keys)

    # The backlog only spends what the day's budget can spare: a reserve is
    # kept for brand-new incidents so a large historical queue can never
    # starve live traffic. Queued incidents simply wait for headroom.
    remaining = store.get_remaining_geocode_requests(config.geocode_max_requests_per_24h)
    spendable = max(0, remaining - max(0, config.geocode_retry_reserve))
    requested = min(needed, spendable)
    if needed > 0 and requested <= 0:
        log_event(
            logger,
            "geocode_retry_deferred",
            queued=store.count_unlocated_incidents(),
            needed=needed,
            remaining_24h=remaining,
            reserve=config.geocode_retry_reserve,
        )
        # Nothing is attempted and no attempt counters move: the queue is
        # intact and will drain when the rolling window frees budget.
        return False

    allowed = store.reserve_geocode_requests(
        requested=requested,
        max_requests_per_24h=config.geocode_max_requests_per_24h,
    )
    # requested == 0 with needed == 0 still proceeds: cache hits are free.

    attempted_keys: set = set()
    geocode_incidents(
        session,
        unlocated,
        config.google_api_key,
        sleep_seconds=config.geocode_sleep_seconds,
        location_cache=location_cache,
        api_call_limit=allowed,
        skip_keys=skip_keys,
        attempted_keys=attempted_keys,
    )
    _filter_geocode_results(unlocated, location_cache)
    _record_geocode_outcomes(store, unlocated, attempted_keys)
    updated = store.update_incident_coords(unlocated)
    if updated:
        log_event(logger, "unlocated_geocoded", count=updated)
        store.update_csv_coords(unlocated)

    # Count an attempt ONLY for incidents whose address was actually sent to
    # Google and still failed. Incidents the budget never reached keep their
    # full retry lifetime — they are queued, not punished.
    still_missing = [
        inc["incident_number"]
        for inc in unlocated
        if (inc.get("latitude") is None or inc.get("longitude") is None)
        and normalize_geocode_key(inc.get("location", "")) in attempted_keys
    ]
    store.increment_geocode_attempts(still_missing)

    if attempted_keys or updated:
        log_event(
            logger,
            "geocode_retry_pass",
            batch=len(unlocated),
            attempted=len(attempted_keys),
            located=updated,
            queued=store.count_unlocated_incidents(),
        )

    return updated > 0


def collect_once(config: Config, store: StateStore, session, logger) -> bool:
    """Run one collection cycle. Returns True if new incidents were stored
    or previously-unlocated incidents gained coordinates (i.e. the map
    should be re-rendered)."""
    incidents = []
    new_incidents = []
    has_new_incidents = False

    # Build address→coords cache once per cycle from all geocoded DB rows.
    # Every geocoding call in this cycle shares the same cache object so
    # each unique location string hits the Google API at most once,
    # regardless of how many incidents share that address.
    location_cache: dict = store.get_location_coords_map()

    # Persistent negative cache: addresses that repeatedly failed to
    # geocode are skipped entirely so recurring unresolvable streets can't
    # drain the daily API budget every time they reappear in the feed.
    skip_keys: set = store.get_skippable_geocode_failures(
        max_attempts=config.geocode_failure_max_attempts,
        retry_after_seconds=int(config.geocode_failure_retry_days * 86400),
    )

    raw = fetch_traffic_data(session, timeout=config.fetch_timeout_seconds, logger=logger)
    incidents = parse_traffic_data(raw)
    if incidents:
        new_incidents = store.filter_new_incidents(incidents)
        if new_incidents:
            allowed_new = None
            if config.google_api_key:
                needed_new = estimate_needed_geocode_requests(
                    new_incidents, location_cache, skip_keys=skip_keys
                )
                allowed_new = store.reserve_geocode_requests(
                    requested=needed_new,
                    max_requests_per_24h=config.geocode_max_requests_per_24h,
                )
                if needed_new > 0:
                    if allowed_new <= 0:
                        log_event(
                            logger,
                            "geocode_budget_exhausted",
                            target="new_incidents",
                            requested=needed_new,
                            max_requests_per_24h=config.geocode_max_requests_per_24h,
                        )
                    else:
                        log_event(
                            logger,
                            "geocode_reserved",
                            requested=needed_new,
                            approved=allowed_new,
                            remaining_24h=store.get_remaining_geocode_requests(
                                config.geocode_max_requests_per_24h
                            ),
                        )
            attempted_keys: set = set()
            geocode_incidents(
                session,
                new_incidents,
                config.google_api_key,
                sleep_seconds=config.geocode_sleep_seconds,
                location_cache=location_cache,
                api_call_limit=allowed_new,
                skip_keys=skip_keys,
                attempted_keys=attempted_keys,
            )
            _filter_geocode_results(new_incidents, location_cache)
            _record_geocode_outcomes(store, new_incidents, attempted_keys)
            if config.weather_enabled:
                snapshot = fetch_weather_snapshot(
                    session,
                    config.weather_lat,
                    config.weather_lon,
                    timeout=config.fetch_timeout_seconds,
                    cache_ttl_seconds=config.weather_cache_ttl_seconds,
                    logger=logger,
                )
                if snapshot is not None:
                    for inc in new_incidents:
                        inc["weather_temp_f"] = snapshot.temperature_f
                        inc["weather_precip_prob"] = snapshot.precip_prob
                        inc["weather_precip_in"] = snapshot.precip_in
                        inc["weather_wind_speed_mph"] = snapshot.wind_speed_mph
                        inc["weather_wind_gust_mph"] = snapshot.wind_gust_mph
                        inc["weather_visibility_mi"] = snapshot.visibility_mi
                        inc["weather_sky_cover_pct"] = snapshot.sky_cover_pct
                        inc["weather_observed_at"] = snapshot.observed_at
                        inc["weather_source"] = snapshot.source

            # Time-context enrichment
            for inc in new_incidents:
                _enrich_incident_time(inc)

            # NWS active alerts enrichment
            alert_snapshot = None
            if config.alerts_enabled:
                alert_snapshot = fetch_nws_alerts(
                    session,
                    timeout=config.fetch_timeout_seconds,
                    cache_ttl_seconds=config.alerts_cache_ttl_seconds,
                    logger=logger,
                )
            for inc in new_incidents:
                if alert_snapshot is not None:
                    inc["nws_flash_flood_warning"] = 1 if alert_snapshot.flash_flood_warning else 0
                    inc["nws_severe_thunderstorm_warning"] = 1 if alert_snapshot.severe_thunderstorm_warning else 0
                    inc["nws_tornado_watch"] = 1 if alert_snapshot.tornado_watch else 0
                    inc["nws_active_alert_count"] = alert_snapshot.active_alert_count
                else:
                    inc["nws_flash_flood_warning"] = None
                    inc["nws_severe_thunderstorm_warning"] = None
                    inc["nws_tornado_watch"] = None
                    inc["nws_active_alert_count"] = None

            new_incidents = store.store_new_incidents(new_incidents)
            store.append_to_csv(new_incidents)
            has_new_incidents = bool(new_incidents)

    # Retry geocoding for incidents already in the DB that still lack
    # coordinates (e.g. they arrived while the daily budget was exhausted),
    # up to the per-incident attempt limit. Enabled by default: the
    # per-address negative cache, the 3-attempt cap, and the budget
    # reservation make it safe. Opt out with:
    #   LAF911_GEOCODE_RETRY_UNLOCATED_ENABLED=false
    if config.geocode_retry_unlocated_enabled:
        if _geocode_unlocated_incidents(config, store, session, logger, location_cache, skip_keys):
            has_new_incidents = True


    # Release references promptly: this loop runs forever on small hardware.
    if incidents is not None:
        incidents.clear()
    if new_incidents is not None:
        new_incidents.clear()
    return has_new_incidents
