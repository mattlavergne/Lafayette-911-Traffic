import gc
import multiprocessing
import os
import re
import sys
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from lafayette911.fetch_incidents import build_session, fetch_traffic_data, geocode_incidents, parse_traffic_data
from lafayette911.map_render import backfill_road_types, create_map_from_csv, create_map_from_db
from lafayette911.state_store import StateStore
from lafayette911.utils import get_rss_bytes, log_event, setup_logging, trim_memory
from lafayette911.weather import fetch_nws_alerts, fetch_weather_snapshot


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


@dataclass
class Config:
    base_dir: str
    csv_path: str
    map_path: str
    datajs_path: str
    db_path: str
    osm_cache_dir: str
    sleep_seconds: int
    fetch_timeout_seconds: int
    google_api_key: str
    mode: str
    render_source: str
    geocode_sleep_seconds: float
    tracemalloc_interval: int
    tracemalloc_top: int
    debug_memory: bool
    gc_collect: bool
    render_only_on_new: bool
    render_in_subprocess: bool
    render_subprocess_timeout_seconds: int
    log_level: str
    weather_enabled: bool
    weather_lat: float
    weather_lon: float
    weather_cache_ttl_seconds: int
    alerts_enabled: bool
    alerts_cache_ttl_seconds: int


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def load_config(base_dir: Optional[str] = None) -> Config:
    base_dir = base_dir or os.getenv("LAF911_BASE_DIR", os.getcwd())
    csv_path = os.getenv("LAF911_CSV_PATH", os.path.join(base_dir, "traffic_incidents.csv"))
    map_path = os.getenv("LAF911_MAP_PATH", os.path.join(base_dir, "traffic_map.html"))
    datajs_path = os.getenv("LAF911_DATAJS_PATH", os.path.join(base_dir, "traffic_data.js"))
    db_path = os.getenv("LAF911_DB_PATH", os.path.join(base_dir, "incident_index.sqlite"))
    osm_cache_dir = os.getenv("LAF911_OSM_CACHE", os.path.join(base_dir, "osm_cache"))

    return Config(
        base_dir=base_dir,
        csv_path=csv_path,
        map_path=map_path,
        datajs_path=datajs_path,
        db_path=db_path,
        osm_cache_dir=osm_cache_dir,
        sleep_seconds=_env_int("LAF911_SLEEP_SECONDS", 300),
        fetch_timeout_seconds=_env_int("LAF911_FETCH_TIMEOUT", 30),
        google_api_key=os.getenv("GOOGLE_API_KEY", ""),
        mode=os.getenv("LAF911_MODE", "all"),
        render_source=os.getenv("LAF911_RENDER_SOURCE", "db"),
        geocode_sleep_seconds=_env_float("LAF911_GEOCODE_SLEEP", 0.0),
        tracemalloc_interval=_env_int("LAF911_TRACEMALLOC_INTERVAL", 0),
        tracemalloc_top=_env_int("LAF911_TRACEMALLOC_TOP", 10),
        debug_memory=_env_bool("LAF911_DEBUG_MEMORY", False),
        gc_collect=_env_bool("LAF911_GC_COLLECT", True),
        render_only_on_new=_env_bool("LAF911_RENDER_ONLY_ON_NEW", True),
        render_in_subprocess=_env_bool("LAF911_RENDER_SUBPROCESS", False),
        render_subprocess_timeout_seconds=_env_int("LAF911_RENDER_SUBPROCESS_TIMEOUT", 600),
        log_level=os.getenv("LAF911_LOG_LEVEL", "INFO"),
        weather_enabled=_env_bool("LAF911_WEATHER_ENABLED", True),
        weather_lat=_env_float("LAF911_WEATHER_LAT", 30.22126),
        weather_lon=_env_float("LAF911_WEATHER_LON", -92.018773),
        weather_cache_ttl_seconds=_env_int("LAF911_WEATHER_CACHE_TTL_SECONDS", 1800),
        alerts_enabled=_env_bool("LAF911_ALERTS_ENABLED", True),
        alerts_cache_ttl_seconds=_env_int("LAF911_ALERTS_CACHE_TTL_SECONDS", 900),
    )


_REPORTED_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{2,4})"
    r"(?:"
    r"(?:\s+|\s*[T@-]\s*)"
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
    r"(?:\s*([AaPp][Mm]))?"
    r")?"
)

# Fallback: MM/DD [HH:MM[:SS] [AM/PM]] without year (assumes current year)
_REPORTED_NOYEAR_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})"
    r"(?!\s*/\s*\d)"  # negative lookahead: not followed by /digits
    r"(?:"
    r"(?:\s+|\s*[T@-]\s*)"
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
    r"(?:\s*([AaPp][Mm]))?"
    r")?"
    r"\s*$"
)


def _parse_reported_dt(reported: str) -> Optional[datetime]:
    """Parse a reported timestamp string into a datetime.

    Handles common feed variants such as:
      - MM/DD/YYYY [HH:MM[:SS] [AM/PM]]
      - MM/DD/YY [HH:MM[:SS] [AM/PM]]
      - MM/DD [HH:MM[:SS] [AM/PM]] (assumes current year)
      - ISO-like YYYY-MM-DD[ T]HH:MM[:SS]
    """
    if not reported:
        return None

    s = reported.strip()
    if not s:
        return None

    def _normalize_two_digit_year(yy: int) -> int:
        return 2000 + yy if yy <= 69 else 1900 + yy

    m = _REPORTED_RE.search(s)
    if m:
        try:
            mm, dd = int(m.group(1)), int(m.group(2))
            yy_raw = m.group(3)
            yy = int(yy_raw)
            if len(yy_raw) == 2:
                yy = _normalize_two_digit_year(yy)
            hh = int(m.group(4)) if m.group(4) is not None else 0
            mi = int(m.group(5)) if m.group(5) is not None else 0
            ap = (m.group(7) or "").lower()
            if ap == "pm" and hh < 12:
                hh += 12
            elif ap == "am" and hh == 12:
                hh = 0
            return datetime(yy, mm, dd, hh, mi)
        except (ValueError, TypeError):
            pass

    m2 = _REPORTED_NOYEAR_RE.search(s)
    if m2:
        try:
            mm, dd = int(m2.group(1)), int(m2.group(2))
            yy = datetime.now().year
            hh = int(m2.group(3)) if m2.group(3) is not None else 0
            mi = int(m2.group(4)) if m2.group(4) is not None else 0
            ap = (m2.group(6) or "").lower()
            if ap == "pm" and hh < 12:
                hh += 12
            elif ap == "am" and hh == 12:
                hh = 0
            return datetime(yy, mm, dd, hh, mi)
        except (ValueError, TypeError):
            pass

    for fmt in (
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y %I:%M %p",
        "%m/%d/%y %H:%M",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if "%y" in fmt and "%Y" not in fmt:
                yy = dt.year % 100
                dt = dt.replace(year=_normalize_two_digit_year(yy))
            return dt
        except (ValueError, TypeError):
            continue

    return None


def _is_holiday(dt: Optional[datetime]) -> bool:
    """Return True if dt falls on a major US federal or Louisiana public holiday.

    Covers: New Year's Day, MLK Day (3rd Mon Jan), Presidents Day (3rd Mon Feb),
    Memorial Day (last Mon May), Juneteenth (Jun 19), Independence Day (Jul 4),
    Labor Day (1st Mon Sep), Columbus/Indigenous Peoples Day (2nd Mon Oct),
    Veterans Day (Nov 11), Thanksgiving (4th Thu Nov), Christmas (Dec 25).
    Also marks the day after Christmas and New Year's Eve as observed when they
    fall on a weekday (common Louisiana practice).
    """
    if dt is None:
        return False
    m, d, dow = dt.month, dt.day, dt.weekday()  # 0=Mon

    # Fixed-date holidays (observed Mon if Sun, observed Fri if Sat)
    def _observed(month: int, day: int) -> bool:
        try:
            h = datetime(dt.year, month, day)
            h_dow = h.weekday()  # 0=Mon, 6=Sun
            if h_dow == 6:  # Sunday → observed Monday
                return m == month and d == day or (
                    datetime(dt.year, month, day + 1 if day < 31 else 1).month == m and
                    datetime(dt.year, month, day + 1 if day < 31 else 1).day == d
                )
            if h_dow == 5:  # Saturday → observed Friday
                return m == month and d == day or (
                    datetime(dt.year, month, day - 1).month == m and
                    datetime(dt.year, month, day - 1).day == d
                )
            return m == month and d == day
        except (ValueError, TypeError):
            return False

    # Simpler observed-day check: within ±1 day for the holiday date
    def _near(month: int, day: int) -> bool:
        try:
            h = datetime(dt.year, month, day)
            delta = abs((dt - h).days)
            if delta == 0:
                return True
            h_dow = h.weekday()
            # Sun holiday → Mon observed
            if h_dow == 6 and (dt - h).days == 1:
                return True
            # Sat holiday → Fri observed
            if h_dow == 5 and (h - dt).days == 1:
                return True
            return False
        except (ValueError, TypeError):
            return False

    if _near(1, 1): return True    # New Year's Day
    if _near(6, 19): return True   # Juneteenth
    if _near(7, 4): return True    # Independence Day
    if _near(11, 11): return True  # Veterans Day
    if _near(12, 25): return True  # Christmas

    # Floating Monday holidays
    def _nth_weekday(year: int, month: int, n: int, weekday: int) -> Optional[int]:
        """Return day-of-month for nth occurrence (1-based) of weekday in month."""
        try:
            count = 0
            for day in range(1, 32):
                try:
                    if datetime(year, month, day).weekday() == weekday:
                        count += 1
                        if count == n:
                            return day
                except ValueError:
                    break
        except Exception:
            pass
        return None

    def _last_weekday(year: int, month: int, weekday: int) -> Optional[int]:
        """Return day-of-month for the LAST occurrence of weekday in month."""
        result = None
        try:
            for day in range(1, 32):
                try:
                    if datetime(year, month, day).weekday() == weekday:
                        result = day
                except ValueError:
                    break
        except Exception:
            pass
        return result

    mlk = _nth_weekday(dt.year, 1, 3, 0)       # MLK Day: 3rd Mon Jan
    if mlk and m == 1 and d == mlk: return True

    pres = _nth_weekday(dt.year, 2, 3, 0)      # Presidents Day: 3rd Mon Feb
    if pres and m == 2 and d == pres: return True

    mem = _last_weekday(dt.year, 5, 0)         # Memorial Day: last Mon May
    if mem and m == 5 and d == mem: return True

    labor = _nth_weekday(dt.year, 9, 1, 0)     # Labor Day: 1st Mon Sep
    if labor and m == 9 and d == labor: return True

    columbus = _nth_weekday(dt.year, 10, 2, 0) # Columbus/Indigenous: 2nd Mon Oct
    if columbus and m == 10 and d == columbus: return True

    thanks = _nth_weekday(dt.year, 11, 4, 3)   # Thanksgiving: 4th Thu Nov
    if thanks and m == 11 and d == thanks: return True

    return False


def _infer_road_type(location: str) -> Optional[str]:
    """Infer OSM highway classification from a location/intersection name.

    Returns one of: motorway, trunk, primary, secondary, residential, or None.
    This provides data for the Road Type filter when OSMnx is unavailable.
    """
    if not location:
        return None
    loc = str(location).upper()

    # Interstate highways (I-10, I-49, I-610 etc.)
    if re.search(r'\bI[-\s]?\d{1,3}\b', loc) or re.search(r'\bINTERSTATE\s+\d', loc):
        return "motorway"

    # US highways (US 90, US-190)
    if re.search(r'\bU\.?S\.?\s*[-]?\s*\d{1,3}\b', loc):
        return "trunk"

    # Louisiana state highways (LA 14, LA-182, HWY 90, HWY-14)
    if re.search(r'\bLA\s*[-]?\s*\d{1,3}\b', loc) or re.search(r'\bHWY\s*[-]?\s*\d{1,3}\b', loc):
        return "primary"

    # Known major arterials in Lafayette Parish
    _PRIMARY_KEYWORDS = (
        "AMBASSADOR CAFFERY", "EVANGELINE THRUWAY", "NW EVANGELINE",
        "JOHNSTON ST", "JOHNSTON STREET", "PINHOOK", "KALISTE SALOOM",
        "HUGH WALLIS", "UNIVERSITY AVE", "UNIVERSITY BLVD",
        "CONGRESS ST", "CAMERON ST", "BERTRAND DR", "VEROT SCHOOL",
        "PONT DES MOUTON", "WILLOW ST", "ERASTE LANDRY", "MUDD AVE",
        "CURRY ST", "OAK PARK BLVD", "RIDGE RD",
        "W PINHOOK", "E PINHOOK", "NORTH UNIVERSITY",
        "S COLLEGE RD", "N COLLEGE RD", "CAMELLIA BLVD",
        "DUHON RD", "YOUNGSVILLE HWY", "SURREY ST",
    )
    for kw in _PRIMARY_KEYWORDS:
        if kw in loc:
            return "primary"

    # Boulevards and parkways → secondary
    if re.search(r'\b(BLVD|BOULEVARD|PKWY|PARKWAY|THRUWAY|EXPRESSWAY)\b', loc):
        return "secondary"

    # Local/residential streets (ST, DR, AVE, CT, CIR, LN, PL, WAY, TRL, LOOP)
    # that didn't match any higher-priority pattern above.
    if re.search(r'\b(ST|STREET|DR|DRIVE|AVE|AVENUE|CT|COURT|CIR|CIRCLE|LN|LANE|PL|PLACE|WAY|TRL|TRAIL|LOOP)\b', loc):
        return "residential"

    return None


def _is_school_day(dt: Optional[datetime]) -> bool:
    """
    Heuristic for Lafayette Parish School System (LPSS) school days.
    Returns True on Mon-Fri during the academic year (roughly mid-Aug through May),
    excluding major holiday breaks.
    """
    if dt is None:
        return False
    dow = dt.weekday()  # 0=Monday, 6=Sunday
    if dow >= 5:
        return False
    month = dt.month
    day = dt.day
    # Summer break: June and July
    if month in {6, 7}:
        return False
    # School year starts mid-August
    if month == 8 and day < 15:
        return False
    # Christmas / winter break: Dec 20 – Jan 3
    if month == 12 and day >= 20:
        return False
    if month == 1 and day <= 3:
        return False
    # Thanksgiving week: approximate Wed–Fri around the fourth Thursday of November
    if month == 11 and 21 <= day <= 27:
        try:
            # Find fourth Thursday of November
            first_nov = datetime(dt.year, 11, 1)
            # Thursday is weekday 3
            offset = (3 - first_nov.weekday()) % 7
            fourth_thursday_day = 1 + offset + 21  # 1st Thu + 3 weeks
            thanksgiving = datetime(dt.year, 11, fourth_thursday_day)
            # Flag Wed before through Fri after
            if thanksgiving.day - 1 <= day <= thanksgiving.day + 1:
                return False
        except (ValueError, TypeError):
            pass
    return True


def _enrich_incident_time(inc: dict) -> None:
    """Derive time-context and road-type fields from the incident and attach to the dict."""
    reported = inc.get("reported", "")
    dt = _parse_reported_dt(str(reported))
    if dt is None:
        inc["hour_of_day"] = None
        inc["day_of_week"] = None
        inc["is_weekend"] = None
        inc["is_rush_hour"] = None
        inc["month"] = None
        inc["is_school_day"] = None
        inc["is_holiday"] = None
    else:
        hour = dt.hour
        dow = dt.weekday()  # 0=Mon, 6=Sun
        is_weekend = 1 if dow >= 5 else 0
        # Rush hour: Mon-Fri 7-9 AM or 4-7 PM (Central)
        is_rush = 1 if (dow < 5 and ((7 <= hour < 9) or (16 <= hour < 19))) else 0
        inc["hour_of_day"] = hour
        inc["day_of_week"] = dow
        inc["is_weekend"] = is_weekend
        inc["is_rush_hour"] = is_rush
        inc["month"] = dt.month
        inc["is_school_day"] = 1 if _is_school_day(dt) else 0
        inc["is_holiday"] = 1 if _is_holiday(dt) else 0

    # Road type: for geocoded incidents let OSM assign the type at render time
    # (via _persist_osm_road_types).  Fall back to name inference only for
    # incidents that could not be geocoded and therefore have no coordinates.
    if not inc.get("road_type"):
        has_coords = inc.get("latitude") is not None and inc.get("longitude") is not None
        if not has_coords:
            inc["road_type"] = _infer_road_type(inc.get("location", ""))


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


def _filter_geocode_results(incidents):
    for inc in incidents:
        lat = inc.get("latitude")
        lng = inc.get("longitude")
        if lat is None or lng is None:
            inc.pop("address_components", None)
            continue
        comps = inc.get("address_components") or []
        if not _has_allowed_lafayette_place(comps):
            inc["latitude"] = None
            inc["longitude"] = None
            inc.pop("address_components", None)
            continue
        if not _in_lafayette_bounds(lat, lng):
            inc["latitude"] = None
            inc["longitude"] = None
        inc.pop("address_components", None)
    return incidents


def _maybe_log_tracemalloc(logger, snapshot, prev_snapshot, top_n: int, debug: bool):
    if snapshot is None:
        return None
    stats = snapshot.statistics("lineno")[:top_n]
    if debug:
        top_allocs = [str(stat) for stat in stats]
        log_event(logger, "tracemalloc_top", allocations=top_allocs)
    if prev_snapshot is not None and debug:
        diff = snapshot.compare_to(prev_snapshot, "lineno")[:top_n]
        growth = [str(stat) for stat in diff]
        log_event(logger, "tracemalloc_growth", allocations=growth)
    return snapshot


def _render_map_from_source(config: Config) -> None:
    if config.render_source == "db":
        create_map_from_db(config.db_path, config.map_path, config.datajs_path, config.osm_cache_dir)
    else:
        create_map_from_csv(config.csv_path, config.map_path, config.datajs_path, config.osm_cache_dir)


def _render_map_worker(conn, config: Config) -> None:
    try:
        _render_map_from_source(config)
        conn.send(None)
    except Exception as exc:
        try:
            conn.send(str(exc))
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _render_map_in_subprocess(config: Config, logger) -> None:
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_render_map_worker, args=(child_conn, config))
    proc.start()
    child_conn.close()
    error = None
    try:
        if parent_conn.poll(config.render_subprocess_timeout_seconds):
            error = parent_conn.recv()
        else:
            error = "render_timeout"
    except Exception as exc:
        error = str(exc)
    finally:
        try:
            parent_conn.close()
        except Exception:
            pass
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
    if error:
        log_event(logger, "render_error", reason="subprocess", error=error)
        raise RuntimeError(error)


def _geocode_unlocated_incidents(config: Config, store: StateStore, session, logger) -> bool:
    """Re-geocode incidents that were previously stored without coordinates.

    Returns True if any incidents gained coordinates (caller should re-render).
    """
    if not config.google_api_key:
        return False

    unlocated = store.get_unlocated_incidents(limit=100)
    if not unlocated:
        return False

    geocode_incidents(
        session,
        unlocated,
        config.google_api_key,
        sleep_seconds=config.geocode_sleep_seconds,
    )
    _filter_geocode_results(unlocated)
    updated = store.update_incident_coords(unlocated)
    if updated:
        log_event(logger, "unlocated_geocoded", count=updated)
        # Keep CSV in sync: overwrite lat/lon for the rows that got coords.
        store.update_csv_coords(unlocated)
    return updated > 0


def run_once(config: Config, store: StateStore, session, logger) -> bool:
    incidents = []
    new_incidents = []
    has_new_incidents = False

    if config.mode in {"all", "fetcher"}:
        raw = fetch_traffic_data(session, timeout=config.fetch_timeout_seconds, logger=logger)
        incidents = parse_traffic_data(raw)
        if incidents:
            new_incidents = store.filter_new_incidents(incidents)
            if new_incidents:
                geocode_incidents(
                    session,
                    new_incidents,
                    config.google_api_key,
                    sleep_seconds=config.geocode_sleep_seconds,
                )
                _filter_geocode_results(new_incidents)
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

        # Retry geocoding for any incidents already in the DB that still
        # lack coordinates (covers failures from previous cycles).
        if _geocode_unlocated_incidents(config, store, session, logger):
            has_new_incidents = True

    if config.mode in {"all", "renderer"}:
        should_render = True
        if config.render_only_on_new and config.mode != "renderer":
            should_render = has_new_incidents
        if should_render:
            if config.render_in_subprocess:
                _render_map_in_subprocess(config, logger)
            else:
                _render_map_from_source(config)
        else:
            log_event(logger, "render_skipped", reason="no_new_incidents")

    if incidents is not None:
        incidents.clear()
    if new_incidents is not None:
        new_incidents.clear()
    return has_new_incidents


def main(base_dir: Optional[str] = None) -> int:
    config = load_config(base_dir)
    logger = setup_logging(config.log_level)
    log_event(logger, "service_start", mode=config.mode, render_source=config.render_source)
    if config.tracemalloc_interval > 0:
        tracemalloc.start()

    store = StateStore(config.db_path, config.csv_path)
    session = build_session()

    # Backfill OSM road types for historical incidents that were stored before
    # the OSM write-back was introduced.  Runs once at startup; safe no-op if
    # osmnx is unavailable or the DB has no geocoded incidents without road types.
    # When the backfill changes anything, immediately regenerate the map so the
    # road-type filter reflects the new values without waiting for new incidents.
    if config.mode in {"all", "renderer"}:
        try:
            updated = backfill_road_types(config.db_path, config.osm_cache_dir)
            if updated:
                log_event(logger, "road_type_backfill", updated=updated)
                _render_map_from_source(config)
        except Exception:
            pass

    cycle = 0
    prev_snapshot = None

    try:
        while True:
            cycle += 1
            start = time.monotonic()
            try:
                run_once(config, store, session, logger)
                duration = time.monotonic() - start
                rss = get_rss_bytes()
                log_event(
                    logger,
                    "cycle_complete",
                    cycle=cycle,
                    duration_sec=round(duration, 2),
                    rss_bytes=rss,
                )

                if config.tracemalloc_interval and cycle % config.tracemalloc_interval == 0:
                    snapshot = tracemalloc.take_snapshot()
                    prev_snapshot = _maybe_log_tracemalloc(
                        logger, snapshot, prev_snapshot, config.tracemalloc_top, config.debug_memory
                    )

                if config.gc_collect:
                    gc.collect()
                    trim_memory()

            except Exception as exc:
                log_event(logger, "cycle_error", error=str(exc), cycle=cycle)

            time.sleep(config.sleep_seconds)

    except KeyboardInterrupt:
        log_event(logger, "service_stop", reason="keyboard_interrupt")
        return 0
    except Exception as exc:
        log_event(logger, "service_stop", reason="fatal", error=str(exc))
        return 1
    finally:
        try:
            session.close()
        except Exception:
            pass
        store.close()


if __name__ == "__main__":
    sys.exit(main())
