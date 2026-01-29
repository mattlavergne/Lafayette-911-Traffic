import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from lafayette911.utils import log_event

NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"


@dataclass
class WeatherSnapshot:
    temperature_f: Optional[float]
    precip_prob: Optional[float]
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


def _select_period(periods, now: datetime):
    if not periods:
        return None
    for period in periods:
        start = _parse_iso_ts(period.get("startTime"))
        end = _parse_iso_ts(period.get("endTime"))
        if start and end and start <= now < end:
            return period
    return periods[0]


def _get_user_agent() -> str:
    env = os.getenv("LAF911_WEATHER_USER_AGENT", "").strip()
    if env:
        return env
    return "lafayette911/1.0 (weather data fetch)"


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

    forecast_url = (
        data.get("properties", {}).get("forecastHourly")
        if isinstance(data, dict)
        else None
    )
    if not forecast_url:
        return None

    response = None
    try:
        response = session.get(forecast_url, headers=headers, timeout=timeout)
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

    periods = forecast.get("properties", {}).get("periods") if isinstance(forecast, dict) else None
    if not periods:
        return None

    now = datetime.now(timezone.utc)
    period = _select_period(periods, now)
    if not period:
        return None

    temperature = period.get("temperature")
    temp = None
    try:
        temp = float(temperature) if temperature is not None else None
    except Exception:
        temp = None

    pop_value = None
    pop = period.get("probabilityOfPrecipitation", {}) if isinstance(period, dict) else {}
    try:
        pop_value = pop.get("value") if isinstance(pop, dict) else None
        pop_value = float(pop_value) if pop_value is not None else None
    except Exception:
        pop_value = None

    observed_at = period.get("startTime") or ""
    snapshot = WeatherSnapshot(
        temperature_f=temp,
        precip_prob=pop_value,
        observed_at=str(observed_at),
        source="NWS Hourly Forecast",
    )
    _WEATHER_CACHE.update(snapshot, cache_ttl_seconds)
    return snapshot
