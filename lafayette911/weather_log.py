"""Hourly log of OBSERVED weather at Lafayette Regional Airport.

Each incident already carries a weather snapshot, but that is a *forecast*
value taken only when something happened — it cannot answer "how many hours
was it raining?", so a fair rainy-vs-dry crash rate was impossible. This
module keeps one row per hour of what the airport's station actually
reported, whether or not anything happened in that hour.

Two sources, both free and keyless:

* **Live:** the National Weather Service observation feed for the station
  (``api.weather.gov/stations/<id>/observations``), polled at most once an
  hour for the last day of reports.
* **History:** the Iowa Environmental Mesonet's archive of the same airport
  observations, used once to backfill every hour since the first incident and
  again automatically for any hours the live poll missed (Pi off, network
  down). ``python -m lafayette911.weather_log --backfill`` runs it by hand.

Storage is tiny: about 8,800 rows a year (~0.4 MB).

Every public function here is fail-safe — weather must never take down
incident collection.
"""

import csv
import io
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import os

NWS_OBS_URL = "https://api.weather.gov/stations/{station}/observations"
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"

LIVE_META_KEY = "weather_log_live_epoch"
BACKFILL_META_KEY = "weather_log_backfill_epoch"
LIVE_EVERY_SECONDS = 3600
HEAL_EVERY_SECONDS = 86400

# Thresholds for the one-letter condition the page receives per hour.
RAIN_IN = 0.01          # measurable rain in the hour
HEAVY_IN = 0.30         # NWS "heavy" is > 0.30 in/h
FOG_MILES = 1.0
_WET_CODES = re.compile(r"RA|DZ|TS|SN|PL|GR|FZ|UP")
_FOG_CODES = re.compile(r"FG")


def station_id() -> str:
    """ICAO id of the observing station (default: Lafayette Regional)."""
    sid = os.getenv("LAF911_WEATHER_STATION", "KLFT").strip().upper()
    return sid if re.fullmatch(r"[A-Z0-9]{3,5}", sid) else "KLFT"


def ensure_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weather_hourly (
            hour_local TEXT PRIMARY KEY,   -- 'YYYY-MM-DDTHH', local wall clock
            temp_f REAL,
            precip_in REAL,
            visibility_mi REAL,
            wind_mph REAL,
            wx TEXT,                       -- METAR present-weather codes
            source TEXT
        ) WITHOUT ROWID
        """
    )


def _hour_key(local_dt: datetime) -> str:
    return local_dt.strftime("%Y-%m-%dT%H")


def _f(value) -> Optional[float]:
    try:
        if value is None or value == "" or value == "M":
            return None
        if value == "T":            # trace of precipitation
            return 0.001
        return float(value)
    except (TypeError, ValueError):
        return None


def merge_hours(rows: Iterable[Dict]) -> Dict[str, Dict]:
    """Fold several reports within one hour into one row per hour.

    Airports file a routine report near :53 plus specials when conditions
    change. The hour keeps the largest precipitation total (the routine
    report carries the hour's accumulation), the worst visibility, the
    strongest wind, the last temperature and every weather code seen.
    """
    out: Dict[str, Dict] = {}
    for row in rows:
        key = row.get("hour_local")
        if not key:
            continue
        cur = out.get(key)
        if cur is None:
            cur = out[key] = {"hour_local": key, "temp_f": None, "precip_in": None,
                              "visibility_mi": None, "wind_mph": None, "wx": "", "source": row.get("source", "")}
        if row.get("temp_f") is not None:
            cur["temp_f"] = row["temp_f"]
        for name, pick in (("precip_in", max), ("wind_mph", max), ("visibility_mi", min)):
            value = row.get(name)
            if value is not None:
                cur[name] = value if cur[name] is None else pick(cur[name], value)
        codes = set(filter(None, (cur["wx"] + " " + str(row.get("wx") or "")).split()))
        cur["wx"] = " ".join(sorted(codes))
    return out


def store_hours(conn, hours: Dict[str, Dict], replace: bool = False) -> int:
    """Insert merged hours. Existing rows are kept unless ``replace`` — the
    archive's quality-controlled values are preferred over the live feed's."""
    ensure_schema(conn)
    sql = ("INSERT OR %s INTO weather_hourly (hour_local, temp_f, precip_in, visibility_mi, wind_mph, wx, source)"
           " VALUES (?, ?, ?, ?, ?, ?, ?)" % ("REPLACE" if replace else "IGNORE"))
    before = conn.total_changes
    conn.executemany(sql, [
        (h["hour_local"], h["temp_f"], h["precip_in"], h["visibility_mi"], h["wind_mph"], h["wx"], h["source"])
        for h in hours.values()
    ])
    conn.commit()
    return conn.total_changes - before


# ── history: Iowa Environmental Mesonet ──────────────────────────────────
def parse_iem_csv(text: str) -> List[Dict]:
    """Rows from the IEM ASOS download (requested with tz=<local zone>, so
    ``valid`` is already local wall-clock time)."""
    rows = []
    for rec in csv.DictReader(io.StringIO(text)):
        try:
            local = datetime.strptime(rec["valid"], "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            continue
        knots = _f(rec.get("sknt"))
        wx = rec.get("wxcodes") or ""
        rows.append({
            "hour_local": _hour_key(local),
            "temp_f": _f(rec.get("tmpf")),
            "precip_in": _f(rec.get("p01i")),
            "visibility_mi": _f(rec.get("vsby")),
            "wind_mph": None if knots is None else round(knots * 1.150779, 1),
            "wx": "" if wx == "M" else wx,
            "source": "IEM ASOS",
        })
    return rows


def fetch_iem(session, start: datetime, end: datetime, timeout: int = 120) -> List[Dict]:
    """Airport observations for local dates ``start``..``end`` inclusive."""
    sid = station_id()
    last = end + timedelta(days=1)
    params = [
        ("station", sid[1:] if len(sid) == 4 and sid.startswith("K") else sid),
        ("data", "tmpf"), ("data", "p01i"), ("data", "vsby"), ("data", "sknt"), ("data", "wxcodes"),
        ("year1", start.year), ("month1", start.month), ("day1", start.day),
        ("year2", last.year), ("month2", last.month), ("day2", last.day),
        ("tz", os.getenv("LAF911_WEATHER_TZ", "America/Chicago")),
        ("format", "onlycomma"), ("latlon", "no"), ("missing", "M"), ("trace", "T"),
        ("report_type", "3"), ("report_type", "4"),
    ]
    resp = session.get(IEM_URL, params=params, timeout=timeout)
    try:
        if resp.status_code != 200:
            raise RuntimeError("IEM answered HTTP %s" % resp.status_code)
        return parse_iem_csv(resp.text)
    finally:
        resp.close()


def _first_incident_date(conn) -> Optional[datetime]:
    """Earliest report date in the archive (the feed's MM/DD/YYYY text)."""
    best = None
    for (reported,) in conn.execute("SELECT reported FROM incidents"):
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", str(reported or ""))
        if not m:
            continue
        try:
            dt = datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        if best is None or dt < best:
            best = dt
    return best


def backfill(conn, session, start: Optional[datetime] = None, end: Optional[datetime] = None,
             chunk_days: int = 120) -> int:
    """Fill the log from the archive for ``start``..``end`` (default: first
    incident .. today). Returns hours written. Raises on network trouble —
    callers that must not fail use :func:`maybe_update_weather_log`."""
    ensure_schema(conn)
    end = end or datetime.now()
    start = start or _first_incident_date(conn) or (end - timedelta(days=7))
    written = 0
    cur = start
    while cur <= end:
        stop = min(end, cur + timedelta(days=chunk_days - 1))
        written += store_hours(conn, merge_hours(fetch_iem(session, cur, stop)), replace=True)
        cur = stop + timedelta(days=1)
    return written


# ── live: National Weather Service ────────────────────────────────────────
def parse_nws_observations(body: Dict) -> List[Dict]:
    rows = []
    for feat in (body or {}).get("features", []):
        props = feat.get("properties") or {}
        try:
            stamp = datetime.fromisoformat(str(props.get("timestamp")).replace("Z", "+00:00"))
        except ValueError:
            continue
        local = stamp.astimezone().replace(tzinfo=None)

        def val(name):
            return _f((props.get(name) or {}).get("value"))

        temp_c, precip_mm = val("temperature"), val("precipitationLastHour")
        vis_m, wind_kmh = val("visibility"), val("windSpeed")
        codes = " ".join(str(w.get("rawString") or "") for w in (props.get("presentWeather") or [])).strip()
        if precip_mm is None and not _WET_CODES.search(codes):
            precip_mm = 0.0        # the feed omits the field when nothing fell
        rows.append({
            "hour_local": _hour_key(local),
            "temp_f": None if temp_c is None else round(temp_c * 9 / 5 + 32, 1),
            "precip_in": None if precip_mm is None else round(precip_mm / 25.4, 3),
            "visibility_mi": None if vis_m is None else round(vis_m / 1609.344, 2),
            "wind_mph": None if wind_kmh is None else round(wind_kmh / 1.609344, 1),
            "wx": codes,
            "source": "NWS",
        })
    return rows


def fetch_nws_recent(session, timeout: int = 30) -> List[Dict]:
    from lafayette911.weather import _get_user_agent

    resp = session.get(
        NWS_OBS_URL.format(station=station_id()), params={"limit": 48}, timeout=timeout,
        headers={"User-Agent": _get_user_agent(), "Accept": "application/geo+json"},
    )
    try:
        if resp.status_code != 200:
            raise RuntimeError("NWS answered HTTP %s" % resp.status_code)
        return parse_nws_observations(resp.json())
    finally:
        resp.close()


def maybe_update_weather_log(store, session, logger=None) -> int:
    """Called every service cycle; does real work at most once an hour.

    Hourly: pull the station's recent reports (covers a day, so a few missed
    polls heal themselves). Daily: ask the archive for the last few days — or,
    the first time, for everything since the first incident — which fills any
    longer hole. Never raises; returns hours written.
    """
    from lafayette911.utils import log_event

    if os.getenv("LAF911_WEATHER_LOG", "true").strip().lower() in {"0", "false", "no", "off"}:
        return 0
    written = 0
    now = time.time()
    try:
        ensure_schema(store.conn)
        if now - float(store._meta_get(LIVE_META_KEY) or 0) >= LIVE_EVERY_SECONDS:
            store._meta_set(LIVE_META_KEY, str(int(now)))     # stamp first: one try per hour
            written += store_hours(store.conn, merge_hours(fetch_nws_recent(session)))
    except Exception as exc:
        if logger is not None:
            log_event(logger, "weather_log_error", source="nws", error=str(exc)[:200])
    try:
        last = store._meta_get(BACKFILL_META_KEY)
        if last is None or now - float(last) >= HEAL_EVERY_SECONDS:
            store._meta_set(BACKFILL_META_KEY, str(int(now)))
            start = None if last is None else datetime.now() - timedelta(days=4)
            filled = backfill(store.conn, session, start=start)
            written += filled
            if logger is not None and (last is None or filled):
                log_event(logger, "weather_log_backfill", hours=filled, first_run=last is None)
    except Exception as exc:
        if logger is not None:
            log_event(logger, "weather_log_error", source="iem", error=str(exc)[:200])
    return written


# ── export for the page ───────────────────────────────────────────────────
def condition_code(precip_in, visibility_mi, wx) -> str:
    """One letter per hour: h heavy rain, r rain, f fog, d dry, - unknown."""
    codes = str(wx or "")
    if precip_in is not None and precip_in >= HEAVY_IN:
        return "h"
    if (precip_in is not None and precip_in >= RAIN_IN) or _WET_CODES.search(codes):
        return "r"
    if (visibility_mi is not None and visibility_mi < FOG_MILES) or _FOG_CODES.search(codes):
        return "f"
    if precip_in is None and visibility_mi is None:
        return "-"
    return "d"


def export_hours(db_path: str) -> Optional[Dict]:
    """``{"start": "YYYY-MM-DDTHH", "codes": "ddrrh-…"}`` — one character per
    local hour from the first logged hour to the last, or None when empty."""
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        try:
            rows = conn.execute(
                "SELECT hour_local, precip_in, visibility_mi, wx FROM weather_hourly ORDER BY hour_local"
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return None
    if not rows:
        return None
    first = datetime.strptime(rows[0][0], "%Y-%m-%dT%H")
    last = datetime.strptime(rows[-1][0], "%Y-%m-%dT%H")
    span = int((last - first).total_seconds() // 3600) + 1
    codes = ["-"] * span
    for key, precip, vis, wx in rows:
        idx = int((datetime.strptime(key, "%Y-%m-%dT%H") - first).total_seconds() // 3600)
        if 0 <= idx < span:
            codes[idx] = condition_code(precip, vis, wx)
    return {"start": rows[0][0], "codes": "".join(codes)}


if __name__ == "__main__":
    import argparse

    from lafayette911.config import load_config
    from lafayette911.fetch_incidents import build_session
    from lafayette911.state_store import StateStore

    parser = argparse.ArgumentParser(description="Hourly observed-weather log")
    parser.add_argument("--backfill", action="store_true",
                        help="fill every hour since the first incident from the IEM archive")
    parser.add_argument("--status", action="store_true", help="show what is logged")
    args = parser.parse_args()
    cfg = load_config()
    store = StateStore(cfg.db_path, cfg.csv_path)
    try:
        ensure_schema(store.conn)
        if args.backfill:
            print("wrote %d hours" % backfill(store.conn, build_session()))
        n, lo, hi = store.conn.execute(
            "SELECT COUNT(1), MIN(hour_local), MAX(hour_local) FROM weather_hourly").fetchone()
        wet = store.conn.execute(
            "SELECT COUNT(1) FROM weather_hourly WHERE precip_in >= ?", (RAIN_IN,)).fetchone()[0]
        print("%d hours logged (%s .. %s), %d with measurable rain" % (n, lo, hi, wet))
    finally:
        store.close()
