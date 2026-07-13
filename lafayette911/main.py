"""Service entry point: a forever loop of collect → render.

The heavy lifting lives elsewhere — this module only wires it together:

  - :mod:`lafayette911.config`      environment-driven configuration
  - :mod:`lafayette911.collector`   the data-collection core (the product)
  - :mod:`lafayette911.map_render`  turns the collected data into the webpage
  - :mod:`lafayette911.state_store` SQLite + CSV persistence and dedupe

``LAF911_MODE`` selects which halves run: ``all`` (default), ``fetcher``
(collection only) or ``renderer`` (page generation only), so collection can
be deployed as its own process if desired.
"""

import gc
import multiprocessing
import sys
import time
import tracemalloc
from collections import deque
from typing import Optional

from lafayette911.collector import collect_once
from lafayette911.config import Config, load_config
from lafayette911.daily_digest import maybe_send_daily_digest
from lafayette911.fetch_incidents import build_session
from lafayette911.map_render import backfill_road_types, create_map_from_csv, create_map_from_db
from lafayette911.state_store import StateStore
from lafayette911.utils import get_rss_bytes, log_event, setup_logging, trim_memory

# Re-exported for backward compatibility: these helpers lived in this module
# before the collection core was extracted, and external scripts/tests may
# still import them from here.
from lafayette911.collector import (  # noqa: F401
    LAFAYETTE_PARISH_PLACES,
    _filter_geocode_results,
    _geocode_unlocated_incidents,
    _in_lafayette_bounds,
    _record_geocode_outcomes,
)
from lafayette911.enrichment import (  # noqa: F401
    _enrich_incident_time,
    _infer_road_type,
    _is_holiday,
    _is_school_day,
    _parse_reported_dt,
)


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
        create_map_from_csv(
            config.csv_path, config.map_path, config.datajs_path, config.osm_cache_dir,
            db_path=config.db_path,
        )


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


def run_once(config: Config, store: StateStore, session, logger) -> bool:
    """One service cycle: collect (if enabled), then render (if warranted)."""
    has_new_incidents = False

    if config.mode in {"all", "fetcher"}:
        has_new_incidents = collect_once(config, store, session, logger)

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

    return has_new_incidents


def main(base_dir: Optional[str] = None) -> int:
    config = load_config(base_dir)
    logger = setup_logging(config.log_level)
    log_event(logger, "service_start", mode=config.mode, render_source=config.render_source)
    if config.tracemalloc_interval > 0:
        tracemalloc.start()

    store = StateStore(config.db_path, config.csv_path)
    session = build_session()

    # Surface the persistent rolling-24h geocode budget at startup so an
    # immediately "exhausted" budget after a redeploy is explainable from the
    # logs (the counter lives in SQLite and survives restarts by design).
    if config.google_api_key:
        # One-time: import failure history recorded before the address-level
        # negative cache existed, so a large legacy backlog of known-bad
        # addresses never re-burns budget.
        seeded = store.seed_negative_cache_from_history()
        if seeded:
            log_event(logger, "geocode_negative_cache_seeded", addresses=seeded)
        # One-time: incidents that were pinned on geocoder fallback points
        # (city centroid / road midpoints — many DIFFERENT addresses sharing
        # one exact coordinate) get their coordinates stripped and rejoin the
        # queue, where the precision filter re-places or retires them.
        purged = store.purge_shared_fallback_coordinates()
        if purged:
            log_event(
                logger,
                "geocode_fallback_purge",
                shared_points=len(purged),
                incidents_requeued=sum(p[3] for p in purged),
            )
        log_event(
            logger,
            "geocode_budget_status",
            remaining_24h=store.get_remaining_geocode_requests(config.geocode_max_requests_per_24h),
            max_requests_per_24h=config.geocode_max_requests_per_24h,
            queued_unlocated=store.count_unlocated_incidents(),
            skipped_bad_addresses=len(
                store.get_skippable_geocode_failures(
                    max_attempts=config.geocode_failure_max_attempts,
                    retry_after_seconds=int(config.geocode_failure_retry_days * 86400),
                )
            ),
        )

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

    # Daily-digest bookkeeping: process start time and a rolling window of
    # cycle-error timestamps so the email can report real 24h health.
    service_started_epoch = time.time()
    error_epochs: deque = deque()
    last_duration = 0.0
    last_rss = 0

    try:
        while True:
            cycle += 1
            start = time.monotonic()
            try:
                run_once(config, store, session, logger)
                duration = time.monotonic() - start
                rss = get_rss_bytes()
                last_duration = duration
                last_rss = rss or 0
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
                error_epochs.append(time.time())
                log_event(logger, "cycle_error", error=str(exc), cycle=cycle)

            while error_epochs and error_epochs[0] < time.time() - 86400:
                error_epochs.popleft()

            # Once per local day (when enabled + due), email the 24h digest.
            # maybe_send_daily_digest never raises, so a mail hiccup can't
            # take down collection.
            maybe_send_daily_digest(
                config,
                store,
                {
                    "started_at_epoch": service_started_epoch,
                    "cycles_completed": cycle,
                    "errors_24h": len(error_epochs),
                    "last_cycle_seconds": last_duration,
                    "rss_bytes": last_rss,
                },
                logger,
            )

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
