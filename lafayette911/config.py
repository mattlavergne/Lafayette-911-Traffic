"""Runtime configuration for the Lafayette 911 traffic service.

Every knob is an environment variable with a sensible default, so a bare
``python lafayette911org.py`` works out of the box and production deployments
tune behaviour through systemd environment files. See README.md for the full
table of variables.
"""

import os
from dataclasses import dataclass
from typing import Optional


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
    geocode_max_requests_per_24h: int
    geocode_retry_unlocated_enabled: bool
    geocode_failure_max_attempts: int
    geocode_failure_retry_days: float
    geocode_retry_batch: int
    geocode_retry_reserve: int
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
        geocode_max_requests_per_24h=_env_int("LAF911_GEOCODE_MAX_REQUESTS_PER_24H", 100),
        geocode_retry_unlocated_enabled=_env_bool("LAF911_GEOCODE_RETRY_UNLOCATED_ENABLED", True),
        geocode_failure_max_attempts=_env_int("LAF911_GEOCODE_FAILURE_MAX_ATTEMPTS", 3),
        geocode_failure_retry_days=_env_float("LAF911_GEOCODE_FAILURE_RETRY_DAYS", 7.0),
        geocode_retry_batch=_env_int("LAF911_GEOCODE_RETRY_BATCH", 25),
        geocode_retry_reserve=_env_int("LAF911_GEOCODE_RETRY_RESERVE", 25),
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
