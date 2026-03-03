import time
from typing import Dict, List, Optional

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


def geocode_incidents(
    session: requests.Session,
    incidents: List[Dict[str, str]],
    api_key: str,
    sleep_seconds: float = 0.0,
    location_cache: Optional[Dict[str, tuple]] = None,
) -> List[Dict[str, str]]:
    """Geocode a list of incidents, updating each dict with latitude/longitude.

    *location_cache* (if provided) maps exact location strings to (lat, lon)
    tuples already seen in the DB.  Cache hits are applied without an API call
    and the cache is updated with any new results so subsequent incidents at
    the same address within the same batch are also free.
    """
    if not incidents:
        return []

    for incident in incidents:
        if incident.get("latitude") and incident.get("longitude"):
            continue

        loc = incident.get("location", "")

        # Serve from address cache when possible — no API call needed.
        if location_cache is not None and loc in location_cache:
            lat, lon = location_cache[loc]
            incident["latitude"] = lat
            incident["longitude"] = lon
            continue

        if not api_key:
            continue

        address = f"{loc}, {DEFAULT_FULL_CITY}"
        result = geocode_with_google(session, address, api_key)
        if not result:
            continue

        incident["latitude"] = result["lat"]
        incident["longitude"] = result["lng"]
        incident["address_components"] = result.get("address_components", [])

        # Populate cache so later incidents at the same address are free.
        if location_cache is not None:
            location_cache[loc] = (result["lat"], result["lng"])

        if sleep_seconds:
            time.sleep(sleep_seconds)

    return incidents
