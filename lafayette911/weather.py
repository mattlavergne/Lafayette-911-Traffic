import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from lafayette911.utils import log_event

NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"
KM_PER_MILE = 1.609344
MM_PER_INCH = 25.4
M_PER_MILE = 1609.344
KNOTS_TO_MPH = 1.150779


@dataclass
class WeatherSnapshot:
    temperature_f: Optional[float]
    precip_prob: Optional[float]
    precip_in: Optional[float]
    wind_speed_mph: Optional[float]
    wind_gust_mph: Optional[float]
    visibility_mi: Optional[float]
    sky_cover_pct: Optional[float]
    observed_at: str
    source: str


class WeatherCache:
    def __init__(self) -> None:
        self.expires_at = 0.0
        self.snapshot: Optional[WeatherSnapshot] = None

    def is_fresh(self) -> bool:
        return bool(self.snapshot) and time.time() < self.expires_at

    def update(self, snapshot: WeatherSnapshot, ttl_seconds: int) -> None:
        self.snapshot = snapshot
        self.expires_at = time.time() + max(ttl_seconds, 0)


_WEATHER_CACHE = WeatherCache()


def _parse_iso_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _duration_to_timedelta(value: str) -> timedelta:
    try:
        if not value.startswith("P"):
            return timedelta(0)
        time_part = value.split("T", 1)[1] if "T" in value else ""
        hours = 0.0
        minutes = 0.0
        seconds = 0.0
        if "H" in time_part:
            hours_part, time_part = time_part.split("H", 1)
            hours = float(hours_part.replace("PT", "").replace("T", "") or 0)
        if "M" in time_part:
            minutes_part, time_part = time_part.split("M", 1)
            minutes = float(minutes_part or 0)
        if "S" in time_part:
            seconds_part = time_part.split("S", 1)[0]
            seconds = float(seconds_part or 0)
        total = hours * 3600 + minutes * 60 + seconds
        return timedelta(seconds=total)
    except Exception:
        return timedelta(0)


def _parse_interval(value: Optional[str]) -> Optional[tuple[datetime, datetime]]:
    if not value:
        return None
    try:
        start_raw, duration_raw = value.split("/")
        start = _parse_iso_ts(start_raw)
        if start is None:
            return None
        end = start + _duration_to_timedelta(duration_raw)
        return (start, end)
    except Exception:
        return None


def _select_grid_value(series, now: datetime):
    if not series:
        return None, ""
    for item in series:
        interval = _parse_interval(item.get("validTime"))
        if not interval:
            continue
        start, end = interval
        if start <= now < end:
            return item.get("value"), start.isoformat()
    for item in series:
        if item.get("value") is not None:
            start_raw = item.get("validTime", "").split("/", 1)[0]
            return item.get("value"), start_raw
    return None, ""


def _get_user_agent() -> str:
    env = os.getenv("LAF911_WEATHER_USER_AGENT", "").strip()
    if env:
        return env
    return "lafayette911/1.0 (weather data fetch)"


def _convert_temperature_to_f(value: Optional[float], unit_code: str) -> Optional[float]:
    if value is None:
        return None
    unit = (unit_code or "").lower()
    if "degc" in unit or "celsius" in unit:
        return (value * 9 / 5) + 32
    if "degf" in unit or "fahrenheit" in unit:
        return value
    return value


def _convert_speed_to_mph(value: Optional[float], unit_code: str) -> Optional[float]:
    if value is None:
        return None
    unit = (unit_code or "").lower()
    if "km_h-1" in unit or "km/h" in unit:
        return value / KM_PER_MILE
    if "kn" in unit or "knot" in unit:
        return value * KNOTS_TO_MPH
    if "mi_h-1" in unit or "mph" in unit:
        return value
    return value


def _convert_distance_to_miles(value: Optional[float], unit_code: str) -> Optional[float]:
    if value is None:
        return None
    unit = (unit_code or "").lower()
    if "wmoUnit:m" in unit or unit.endswith(":m"):
        return value / M_PER_MILE
    if "mi" in unit:
        return value
    if "km" in unit:
        return value / KM_PER_MILE
    return value


def _convert_precip_to_inches(value: Optional[float], unit_code: str) -> Optional[float]:
    if value is None:
        return None
    unit = (unit_code or "").lower()
    if "mm" in unit:
        return value / MM_PER_INCH
    if "inch" in unit or "in" in unit:
        return value
    return value


def fetch_weather_snapshot(
    session,
    lat: float,
    lon: float,
    timeout: int,
    cache_ttl_seconds: int,
    logger=None,
) -> Optional[WeatherSnapshot]:
    if _WEATHER_CACHE.is_fresh():
        return _WEATHER_CACHE.snapshot

    headers = {"User-Agent": _get_user_agent()}
    points_url = NWS_POINTS_URL.format(lat=lat, lon=lon)

    response = None
    try:
        response = session.get(points_url, headers=headers, timeout=timeout)
        if response.status_code != 200:
            return None
        data = response.json()
    except Exception as exc:
        if logger is not None:
            log_event(logger, "weather_fetch_error", error=str(exc))
        return None
    finally:
        if response is not None:
            response.close()

    grid_url = data.get("properties", {}).get("forecastGridData") if isinstance(data, dict) else None
    if not grid_url:
        return None

    response = None
    try:
        response = session.get(grid_url, headers=headers, timeout=timeout)
        if response.status_code != 200:
            return None
        forecast = response.json()
    except Exception as exc:
        if logger is not None:
            log_event(logger, "weather_fetch_error", error=str(exc))
        return None
    finally:
        if response is not None:
            response.close()

    props = forecast.get("properties") if isinstance(forecast, dict) else None
    if not props:
        return None

    now = datetime.now(timezone.utc)

    temp_series = (props.get("temperature") or {}).get("values", [])
    temp_value, observed_at = _select_grid_value(temp_series, now)
    temp = _convert_temperature_to_f(temp_value, (props.get("temperature") or {}).get("uom", ""))

    pop_series = (props.get("probabilityOfPrecipitation") or {}).get("values", [])
    pop_value, _ = _select_grid_value(pop_series, now)
    try:
        pop_value = float(pop_value) if pop_value is not None else None
    except Exception:
        pop_value = None

    precip_series = (props.get("quantitativePrecipitation") or {}).get("values", [])
    precip_value, _ = _select_grid_value(precip_series, now)
    precip_in = _convert_precip_to_inches(precip_value, (props.get("quantitativePrecipitation") or {}).get("uom", ""))

    wind_series = (props.get("windSpeed") or {}).get("values", [])
    wind_value, _ = _select_grid_value(wind_series, now)
    wind_speed_mph = _convert_speed_to_mph(wind_value, (props.get("windSpeed") or {}).get("uom", ""))

    gust_series = (props.get("windGust") or {}).get("values", [])
    gust_value, _ = _select_grid_value(gust_series, now)
    wind_gust_mph = _convert_speed_to_mph(gust_value, (props.get("windGust") or {}).get("uom", ""))

    vis_series = (props.get("visibility") or {}).get("values", [])
    vis_value, _ = _select_grid_value(vis_series, now)
    visibility_mi = _convert_distance_to_miles(vis_value, (props.get("visibility") or {}).get("uom", ""))

    sky_series = (props.get("skyCover") or {}).get("values", [])
    sky_value, _ = _select_grid_value(sky_series, now)
    try:
        sky_cover_pct = float(sky_value) if sky_value is not None else None
    except Exception:
        sky_cover_pct = None

    snapshot = WeatherSnapshot(
        temperature_f=temp,
        precip_prob=pop_value,
        precip_in=precip_in,
        wind_speed_mph=wind_speed_mph,
        wind_gust_mph=wind_gust_mph,
        visibility_mi=visibility_mi,
        sky_cover_pct=sky_cover_pct,
        observed_at=str(observed_at),
        source="NWS Gridpoint Forecast",
    )
    _WEATHER_CACHE.update(snapshot, cache_ttl_seconds)
    return snapshot


# ---------------------------------------------------------------------------
# NWS Active Alerts
# ---------------------------------------------------------------------------

NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"
LAFAYETTE_ZONE = "LAZ034"


@dataclass
class NWSAlertSnapshot:
    flash_flood_warning: bool
    severe_thunderstorm_warning: bool
    tornado_watch: bool
    active_alert_count: int
    observed_at: str


class NWSAlertCache:
    def __init__(self) -> None:
        self.expires_at = 0.0
        self.snapshot: Optional[NWSAlertSnapshot] = None

    def is_fresh(self) -> bool:
        return bool(self.snapshot) and time.time() < self.expires_at

    def update(self, snapshot: NWSAlertSnapshot, ttl_seconds: int) -> None:
        self.snapshot = snapshot
        self.expires_at = time.time() + max(ttl_seconds, 0)


_ALERTS_CACHE = NWSAlertCache()


def fetch_nws_alerts(
    session,
    timeout: int,
    cache_ttl_seconds: int,
    logger=None,
) -> Optional[NWSAlertSnapshot]:
    """Fetch active NWS alerts for Lafayette Parish (zone LAZ034)."""
    if _ALERTS_CACHE.is_fresh():
        return _ALERTS_CACHE.snapshot

    headers = {"User-Agent": _get_user_agent()}
    params = {"zone": LAFAYETTE_ZONE}
    response = None
    try:
        response = session.get(NWS_ALERTS_URL, params=params, headers=headers, timeout=timeout)
        if response.status_code != 200:
            return None
        data = response.json()
    except Exception as exc:
        if logger is not None:
            log_event(logger, "nws_alerts_fetch_error", error=str(exc))
        return None
    finally:
        if response is not None:
            response.close()

    features = data.get("features") or []
    flash_flood = False
    severe_thunderstorm = False
    tornado_watch = False

    for feat in features:
        props = feat.get("properties") or {}
        event = (props.get("event") or "").upper()
        if "FLASH FLOOD" in event and "WARNING" in event:
            flash_flood = True
        if "SEVERE THUNDERSTORM" in event and "WARNING" in event:
            severe_thunderstorm = True
        if "TORNADO" in event and "WATCH" in event:
            tornado_watch = True

    snapshot = NWSAlertSnapshot(
        flash_flood_warning=flash_flood,
        severe_thunderstorm_warning=severe_thunderstorm,
        tornado_watch=tornado_watch,
        active_alert_count=len(features),
        observed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _ALERTS_CACHE.update(snapshot, cache_ttl_seconds)
    return snapshot
