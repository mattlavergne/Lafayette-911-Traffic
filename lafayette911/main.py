import gc
import multiprocessing
import os
import sys
import time
import tracemalloc
from dataclasses import dataclass
from typing import Optional

from lafayette911.fetch_incidents import build_session, fetch_traffic_data, geocode_incidents, parse_traffic_data
from lafayette911.map_render import create_map_from_csv, create_map_from_db
from lafayette911.state_store import StateStore
from lafayette911.utils import get_rss_bytes, log_event, setup_logging
from lafayette911.weather import fetch_weather_snapshot


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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
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
    )


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
                new_incidents = store.store_new_incidents(new_incidents)
                store.append_to_csv(new_incidents)
                has_new_incidents = bool(new_incidents)

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
