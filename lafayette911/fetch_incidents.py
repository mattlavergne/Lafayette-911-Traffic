import re
import time
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from lafayette911.utils import log_event

TRAFFIC_FEED_URL = "https://lafayette911.org/wp-json/traffic-feed/v1/data"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
DEFAULT_FULL_CITY = "Lafayette, Louisiana, USA"


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=4)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def fetch_traffic_data(
    session: requests.Session,
    timeout: int = 30,
    logger=None,
) -> Optional[Dict]:
    response = None
    try:
        response = session.get(TRAFFIC_FEED_URL, timeout=timeout)
        if response.status_code != 200:
            return None
        return response.json()
    except requests.RequestException as exc:
        if logger is not None:
            log_event(logger, "traffic_fetch_error", error=str(exc))
        return None
    finally:
        if response is not None:
            response.close()


def parse_traffic_data(json_data: Optional[Dict]) -> List[Dict[str, str]]:
    if not (json_data and json_data.get("success")):
        return []

    html_data = json_data.get("data", "")
    soup = BeautifulSoup(html_data, "html.parser")
    table_rows = soup.select("table tr")[1:]

    incidents: List[Dict[str, str]] = []
    for row in table_rows:
        cols = row.find_all("td")
        if len(cols) != 4:
            continue

        raw_location = cols[0].get_text("\n", strip=False)
        location = raw_location.strip()
        cause = cols[1].get_text(" ", strip=True)
        reported = cols[2].get_text(" ", strip=True)
        assisting = cols[3].get_text(" ", strip=True)

        incident_number = (
            f"{location.replace(' ', '_')}\n"
            f"________________________LAF_PARISH,_LA_{cause.replace(' ', '_')}_{reported.replace(' ', '_')}"
        )

        incidents.append(
            {
                "location": location,
                "cause": cause,
                "reported": reported,
                "assisting": assisting,
                "incident_number": incident_number,
            }
        )

    return incidents


def geocode_with_google(
    session: requests.Session,
    address: str,
    api_key: str,
    timeout: int = 30,
) -> Optional[Dict[str, float]]:
    if not api_key:
        return None

    params = {"address": address, "key": api_key}
    response = None
    try:
        response = session.get(GOOGLE_GEOCODE_URL, params=params, timeout=timeout)
        if response.status_code != 200:
            return None

        geocode_data = response.json()
        if geocode_data.get("status") != "OK" or not geocode_data.get("results"):
            return None

        result = geocode_data["results"][0]
        loc = result.get("geometry", {}).get("location", {})
        lat = loc.get("lat")
        lng = loc.get("lng")
        if lat is None or lng is None:
            return None

        return {"lat": float(lat), "lng": float(lng), "address_components": result.get("address_components", [])}
    except requests.RequestException:
        return None
    finally:
        if response is not None:
            response.close()


def normalize_geocode_key(location: str) -> str:
    """Normalize an address for cache lookup: case-insensitive, collapsed whitespace.

    Feed formatting jitter ("W Congress St" vs "W  CONGRESS ST") would otherwise
    force a fresh Google API call for an address we have already geocoded.
    Exact-string matches remain the primary cache key; this is a fallback only.
    """
    return re.sub(r"\s+", " ", str(location or "").strip()).upper()


def _build_normalized_cache_index(
    location_cache: Optional[Dict[str, tuple]],
) -> Dict[str, Tuple[float, float]]:
    index: Dict[str, Tuple[float, float]] = {}
    if not location_cache:
        return index
    for loc, coords in location_cache.items():
        key = normalize_geocode_key(loc)
        if key and key not in index:
            index[key] = coords
    return index


def _cache_lookup(
    loc: str,
    location_cache: Optional[Dict[str, tuple]],
    normalized_index: Dict[str, Tuple[float, float]],
) -> Optional[Tuple[float, float]]:
    if location_cache is not None and loc in location_cache:
        return location_cache[loc]
    return normalized_index.get(normalize_geocode_key(loc))


def geocode_incidents(
    session: requests.Session,
    incidents: List[Dict[str, str]],
    api_key: str,
    sleep_seconds: float = 0.0,
    location_cache: Optional[Dict[str, tuple]] = None,
    api_call_limit: Optional[int] = None,
    skip_keys: Optional[set] = None,
    attempted_keys: Optional[set] = None,
) -> List[Dict[str, str]]:
    """Geocode a list of incidents, updating each dict with latitude/longitude.

    *location_cache* (if provided) maps exact location strings to (lat, lon)
    tuples already seen in the DB.  Cache hits are applied without an API call
    and the cache is updated with any new results so subsequent incidents at
    the same address within the same batch are also free.  A normalized
    (case/whitespace-insensitive) index over the same cache catches feed
    formatting jitter, and addresses that fail to geocode are remembered for
    the rest of the batch so duplicates never burn a second API call.

    *skip_keys* is a persistent negative cache: normalized address keys that
    have repeatedly failed before and must not consume API quota again.
    *attempted_keys* (if provided) collects the normalized keys this call
    actually sent to Google, so the caller can persist new failures.
    """
    if not incidents:
        return []

    normalized_index = _build_normalized_cache_index(location_cache)
    failed_keys: set = set()
    calls_made = 0
    for incident in incidents:
        if incident.get("latitude") and incident.get("longitude"):
            continue

        loc = incident.get("location", "")

        # Serve from address cache when possible — no API call needed.
        cached = _cache_lookup(loc, location_cache, normalized_index)
        if cached is not None:
            incident["latitude"] = cached[0]
            incident["longitude"] = cached[1]
            continue

        if not api_key:
            continue

        # Skip addresses known to be unresolvable (persistent negative cache)
        # or that already failed earlier in this batch.
        norm_key = normalize_geocode_key(loc)
        if skip_keys is not None and norm_key in skip_keys:
            continue
        if norm_key in failed_keys:
            continue

        if api_call_limit is not None and calls_made >= api_call_limit:
            continue

        address = f"{loc}, {DEFAULT_FULL_CITY}"
        result = geocode_with_google(session, address, api_key)
        calls_made += 1
        if attempted_keys is not None:
            attempted_keys.add(norm_key)
        if not result:
            failed_keys.add(norm_key)
            continue

        incident["latitude"] = result["lat"]
        incident["longitude"] = result["lng"]
        incident["address_components"] = result.get("address_components", [])

        # Populate both cache views so later incidents at the same (or a
        # differently formatted) address are free.
        coords = (result["lat"], result["lng"])
        if location_cache is not None:
            location_cache[loc] = coords
        normalized_index[norm_key] = coords

        if sleep_seconds:
            time.sleep(sleep_seconds)

    return incidents


def estimate_needed_geocode_requests(
    incidents: List[Dict[str, str]],
    location_cache: Optional[Dict[str, tuple]] = None,
    skip_keys: Optional[set] = None,
) -> int:
    """Estimate how many Google API calls would be needed for this batch."""
    if not incidents:
        return 0
    normalized_index = _build_normalized_cache_index(location_cache)
    seen = set()
    needed = 0
    for incident in incidents:
        if incident.get("latitude") and incident.get("longitude"):
            continue
        loc = incident.get("location", "")
        if not loc:
            continue
        if _cache_lookup(loc, location_cache, normalized_index) is not None:
            continue
        key = normalize_geocode_key(loc)
        if key in seen:
            continue
        if skip_keys is not None and key in skip_keys:
            continue
        seen.add(key)
        needed += 1
    return needed
