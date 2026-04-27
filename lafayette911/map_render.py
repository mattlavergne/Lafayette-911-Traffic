import hashlib
import importlib
import importlib.util
import json
import math
import multiprocessing
import os
import re
import sqlite3
import tempfile
import time
from datetime import datetime
from typing import Dict, Iterable, List, Tuple

import folium
import pandas as pd

from lafayette911.utils import atomic_write_text


LAF_LAT_MIN = 29.50
LAF_LAT_MAX = 31.00
LAF_LON_MIN = -92.25
LAF_LON_MAX = -91.90

OSM_PAD_DEG = 0.02
OSM_INTERSECTION_MIN_STREETS = 3


def _osm_cache_ttl_seconds() -> int:
    try:
        return max(int(os.getenv("LAF911_OSM_CACHE_TTL_SECONDS", "0")), 0)
    except Exception:
        return 0


def _osm_intersections_subprocess_enabled() -> bool:
    val = os.getenv("LAF911_OSM_INTERSECTION_SUBPROCESS")
    if val is None:
        return False
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _cache_is_fresh(path: str, ttl_seconds: int) -> bool:
    if ttl_seconds <= 0:
        return False
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        return False
    return (time.time() - mtime) <= ttl_seconds


def _write_text_if_changed(path: str, text: str) -> bool:
    data = text.encode("utf-8")
    try:
        if os.path.exists(path):
            with open(path, "rb") as handle:
                old = handle.read()
            if old == data:
                return False
        atomic_write_text(path, text)
        return True
    except Exception:
        atomic_write_text(path, text)
        return True


def _build_incidents_script(incidents, osm_intersections, hot_spots=None) -> str:
    s = "window.INCIDENTS_DATA=" + json.dumps(incidents, ensure_ascii=False, separators=(",", ":"))
    s += ";\nwindow.OSM_INTERSECTIONS_DATA=" + json.dumps(
        osm_intersections, ensure_ascii=False, separators=(",", ":")
    )
    s += ";\nwindow.HOT_SPOTS_DATA=" + json.dumps(
        hot_spots or [], ensure_ascii=False, separators=(",", ":")
    )
    s += ";"
    return s


def _write_jsonjs_if_changed(path: str, incidents, osm_intersections, hot_spots=None) -> bool:
    return _write_text_if_changed(path, _build_incidents_script(incidents, osm_intersections, hot_spots))


def _stream_jsonjs_header(handle) -> None:
    handle.write("window.INCIDENTS_DATA=[")


def _stream_jsonjs_incident(handle, incident, first: bool) -> bool:
    if not first:
        handle.write(",")
    handle.write(json.dumps(incident, ensure_ascii=False, separators=(",", ":")))
    return False


def _stream_jsonjs_footer(handle, osm_intersections, hot_spots=None, unlocated_count: int = 0) -> None:
    handle.write("];\nwindow.OSM_INTERSECTIONS_DATA=")
    handle.write(json.dumps(osm_intersections, ensure_ascii=False, separators=(",", ":")))
    handle.write(";\nwindow.HOT_SPOTS_DATA=")
    handle.write(json.dumps(hot_spots or [], ensure_ascii=False, separators=(",", ":")))
    handle.write(f";\nwindow.INCIDENTS_UNLOCATED_COUNT={int(unlocated_count)};")


def _ensure_world_readable(path: str) -> None:
    try:
        os.chmod(path, 0o644)
    except Exception:
        return


def _ensure_world_readable_dir(path: str) -> None:
    try:
        os.chmod(path, 0o755)
    except Exception:
        return


def _find_lat_lon_columns(df):
    cols = {c.strip().lower(): c for c in df.columns}
    lat_candidates = ["latitude", "lat", "y"]
    lon_candidates = ["longitude", "lon", "lng", "long", "x"]
    lat_col = next((cols.get(k) for k in lat_candidates if k in cols), None)
    lon_col = next((cols.get(k) for k in lon_candidates if k in cols), None)
    return lat_col, lon_col


def _compute_center(df, lat_col, lon_col):
    try:
        return float(df[lat_col].mean()), float(df[lon_col].mean())
    except Exception:
        return 30.2241, -92.0198


def _sanitize_points(df, lat_col, lon_col):
    d = df.copy()
    d[lat_col] = pd.to_numeric(d[lat_col], errors="coerce")
    d[lon_col] = pd.to_numeric(d[lon_col], errors="coerce")
    d = d.dropna(subset=[lat_col, lon_col]).copy()
    return d


def _lafayette_only(df, lat_col, lon_col):
    d = df.copy()
    d = d[
        (d[lat_col] >= LAF_LAT_MIN)
        & (d[lat_col] <= LAF_LAT_MAX)
        & (d[lon_col] >= LAF_LON_MIN)
        & (d[lon_col] <= LAF_LON_MAX)
    ].copy()
    return d


def _bbox_from_points(df, lat_col, lon_col, pad_deg=OSM_PAD_DEG):
    d = _sanitize_points(df, lat_col, lon_col)
    d = _lafayette_only(d, lat_col, lon_col)

    if d.empty:
        return (LAF_LAT_MIN, LAF_LAT_MAX, LAF_LON_MIN, LAF_LON_MAX)

    lat_min = float(d[lat_col].min()) - pad_deg
    lat_max = float(d[lat_col].max()) + pad_deg
    lon_min = float(d[lon_col].min()) - pad_deg
    lon_max = float(d[lon_col].max()) + pad_deg

    lat_min = max(lat_min, LAF_LAT_MIN - 0.10)
    lat_max = min(lat_max, LAF_LAT_MAX + 0.10)
    lon_min = max(lon_min, LAF_LON_MIN - 0.10)
    lon_max = min(lon_max, LAF_LON_MAX + 0.10)

    return (lat_min, lat_max, lon_min, lon_max)


def _hash_bbox(bbox):
    s = f"{bbox[0]:.5f}_{bbox[1]:.5f}_{bbox[2]:.5f}_{bbox[3]:.5f}"
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _osm_cache_paths(osm_cache_dir: str, bbox_id, n_points):
    graphml_path = os.path.join(osm_cache_dir, f"drive_{bbox_id}.graphml")
    cache_json = os.path.join(osm_cache_dir, f"osmctx_{bbox_id}_{n_points}.json")
    return graphml_path, cache_json


def _compute_osm_context_for_bbox(bbox, incidents_latlng, osm_cache_dir: str):
    if not importlib.util.find_spec("osmnx"):
        return {}, [], []

    ox = importlib.import_module("osmnx")

    if not incidents_latlng:
        return {}, [], []

    os.makedirs(osm_cache_dir, exist_ok=True)

    south, north, west, east = bbox
    bbox_id = _hash_bbox((south, north, west, east))

    n_points = len(incidents_latlng)
    graphml_path, cache_json = _osm_cache_paths(osm_cache_dir, bbox_id, n_points)

    if os.path.exists(cache_json):
        try:
            with open(cache_json, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
            node_info = cached.get("node_info", {}) or {}
            point_nodes = cached.get("point_nodes", []) or []
            overall_counts = cached.get("overall_counts", []) or []
            if len(point_nodes) == n_points:
                return node_info, point_nodes, overall_counts
        except Exception:
            pass

    try:
        if os.path.exists(graphml_path):
            G = ox.load_graphml(graphml_path)
        else:
            G = ox.graph_from_bbox(bbox=(west, south, east, north), network_type="drive", simplify=True)
            ox.save_graphml(G, graphml_path)
    except Exception:
        return {}, [""] * n_points, []

    try:
        sc = ox.stats.count_streets_per_node(G)
        for n, v in sc.items():
            try:
                G.nodes[n]["street_count"] = int(v)
            except Exception:
                pass
    except Exception:
        pass

    def street_count_or_degree(n):
        try:
            v = G.nodes[n].get("street_count", None)
            if v is not None:
                return int(v)
        except Exception:
            pass
        try:
            return int(G.degree(n))
        except Exception:
            return 0

    intersection_nodes = set()
    for n in G.nodes:
        if street_count_or_degree(n) >= OSM_INTERSECTION_MIN_STREETS:
            intersection_nodes.add(n)

    point_nodes = [""] * n_points
    if not intersection_nodes:
        return {}, point_nodes, []

    idxs = []
    xs = []
    ys = []

    for i, (lat, lng) in enumerate(incidents_latlng):
        if lat is None or lng is None:
            continue
        if not _in_lafayette_bounds(lat, lng):
            continue
        idxs.append(i)
        xs.append(float(lng))
        ys.append(float(lat))

    if not idxs:
        return {}, point_nodes, []

    try:
        nearest_edges = ox.distance.nearest_edges(G, X=xs, Y=ys)
    except Exception:
        return {}, point_nodes, []

    counts: Dict[str, int] = {}

    for j, e in enumerate(nearest_edges):
        i = idxs[j]
        try:
            u = e[0]
            v = e[1]
        except Exception:
            continue

        su = street_count_or_degree(u)
        sv = street_count_or_degree(v)
        chosen = u if su >= sv else v

        if chosen not in intersection_nodes:
            other = v if chosen == u else u
            if other in intersection_nodes:
                chosen = other
            else:
                continue

        node_id = str(chosen)
        point_nodes[i] = node_id
        counts[node_id] = counts.get(node_id, 0) + 1

    node_info: Dict[str, List[float]] = {}
    overall_counts: List[List] = []
    for node_id, cnt in counts.items():
        try:
            n = int(node_id)
        except Exception:
            continue
        try:
            node = G.nodes[n]
            lat = float(node.get("y"))
            lng = float(node.get("x"))
            node_info[str(node_id)] = [round(lat, 6), round(lng, 6)]
            overall_counts.append([round(lat, 6), round(lng, 6), int(cnt), str(node_id)])
        except Exception:
            continue

    overall_counts.sort(key=lambda x: x[2], reverse=True)

    try:
        payload = {
            "node_info": node_info,
            "point_nodes": point_nodes,
            "overall_counts": overall_counts,
            "bbox": {"south": south, "north": north, "west": west, "east": east},
            "min_streets": OSM_INTERSECTION_MIN_STREETS,
        }
        with open(cache_json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        pass

    return node_info, point_nodes, overall_counts


def _compute_osm_context_for_incidents(
    df_all,
    lat_col,
    lon_col,
    incidents_latlng,
    osm_cache_dir: str,
    allow_subprocess: bool = True,
):
    if not incidents_latlng:
        return {}, [], []
    bbox = _bbox_from_points(df_all, lat_col, lon_col, pad_deg=OSM_PAD_DEG)
    if allow_subprocess and _osm_intersections_subprocess_enabled():
        return _compute_osm_context_in_subprocess(
            bbox,
            incidents_latlng,
            osm_cache_dir,
        )
    return _compute_osm_context_for_bbox(bbox, incidents_latlng, osm_cache_dir)


def _compute_osm_context_worker(conn, bbox, incidents_latlng, osm_cache_dir: str) -> None:
    try:
        result = _compute_osm_context_for_bbox(bbox, incidents_latlng, osm_cache_dir)
        conn.send(result)
    except Exception:
        try:
            conn.send(({}, [], []))
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _compute_osm_context_in_subprocess(bbox, incidents_latlng, osm_cache_dir: str):
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_compute_osm_context_worker,
        args=(child_conn, bbox, incidents_latlng, osm_cache_dir),
    )
    proc.start()
    child_conn.close()
    try:
        if parent_conn.poll(600):
            return parent_conn.recv()
    except Exception:
        return {}, [], []
    finally:
        try:
            parent_conn.close()
        except Exception:
            pass
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
    return {}, [], []


def _infer_road_type(location: str):
    """Infer OSM highway classification from a location string.

    Used as a fallback when OSMnx is unavailable so that every incident gets
    a road type for the map filter.  Mirrors the logic in main._infer_road_type.
    Returns one of: motorway, trunk, primary, secondary, residential, or None.
    """
    if not location:
        return None
    loc = str(location).upper()
    if re.search(r'\bI[-\s]?\d{1,3}\b', loc) or re.search(r'\bINTERSTATE\s+\d', loc):
        return "motorway"
    if re.search(r'\bU\.?S\.?\s*(?:HWY\s*)?[-]?\s*\d{1,3}\b', loc):
        return "trunk"
    if re.search(r'\bLA\s*[-]?\s*\d{1,3}\b', loc) or re.search(r'\bHWY\s*[-]?\s*\d{1,3}\b', loc):
        return "primary"
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
    if re.search(r'\b(BLVD|BOULEVARD|PKWY|PARKWAY|THRUWAY|EXPRESSWAY)\b', loc):
        return "secondary"
    if re.search(r'\b(ST|STREET|DR|DRIVE|AVE|AVENUE|CT|COURT|CIR|CIRCLE|LN|LANE|PL|PLACE|WAY|TRL|TRAIL|LOOP)\b', loc):
        return "residential"
    return None


# OSM highway values that map cleanly to the JS filter categories.
# Values NOT in this set (e.g. "road", "path", "track", "pedestrian") are
# unusual and should fall back to name-based inference so they appear under
# the correct filter option rather than being invisible.
_FILTERABLE_HIGHWAY_TYPES: frozenset = frozenset({
    "motorway", "motorway_link",
    "trunk", "trunk_link",
    "primary", "primary_link",
    "secondary", "secondary_link",
    "tertiary", "tertiary_link",
    "residential", "living_street",
    "service", "unclassified",
})


def _resolve_highway_type(raw_hw: str, loc: str) -> str:
    """Return a filter-compatible highway type string.

    Prefers *raw_hw* (OSMnx / DB value) when it is a recognised OSM category.
    For unusual values ("road", "path", "track", …) falls back to name
    inference so the incident still appears in the appropriate filter bucket.
    """
    if raw_hw and raw_hw.lower() in _FILTERABLE_HIGHWAY_TYPES:
        return raw_hw
    # Unusual or missing value — derive from location name.
    return _infer_road_type(loc) or raw_hw or None


def _precompute_osm_road_types(db_path: str, osm_cache_dir: str) -> dict:
    """
    Return {(round(lat,6), round(lon,6)): highway_type_str} for every geocoded
    incident in the DB.  Uses the already-cached GraphML file; returns {} if
    osmnx is not installed or any error occurs.
    """
    if not importlib.util.find_spec("osmnx"):
        return {}

    ox = importlib.import_module("osmnx")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT latitude, longitude FROM incidents WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    pts = []
    for lat, lon in rows:
        lat = _safe_float(lat)
        lon = _safe_float(lon)
        if lat is None or lon is None:
            continue
        if not _in_lafayette_bounds(lat, lon):
            continue
        pts.append((round(lat, 6), round(lon, 6)))

    if not pts:
        return {}

    unique_pts = list({p for p in pts})
    lat_min = min(p[0] for p in unique_pts)
    lat_max = max(p[0] for p in unique_pts)
    lon_min = min(p[1] for p in unique_pts)
    lon_max = max(p[1] for p in unique_pts)

    bbox = _compute_bbox_from_points(lat_min, lat_max, lon_min, lon_max)
    south, north, west, east = bbox
    bbox_id = _hash_bbox((south, north, west, east))

    os.makedirs(osm_cache_dir, exist_ok=True)
    graphml_path = os.path.join(osm_cache_dir, f"drive_{bbox_id}.graphml")

    try:
        if os.path.exists(graphml_path):
            G = ox.load_graphml(graphml_path)
        else:
            G = ox.graph_from_bbox(bbox=(west, south, east, north), network_type="drive", simplify=True)
            ox.save_graphml(G, graphml_path)
    except Exception:
        return {}

    xs = [float(p[1]) for p in unique_pts]
    ys = [float(p[0]) for p in unique_pts]

    result: dict = {}
    try:
        nearest_edges = ox.distance.nearest_edges(G, X=xs, Y=ys)
        for i, edge in enumerate(nearest_edges):
            try:
                u, v = edge[0], edge[1]
                k = edge[2] if len(edge) > 2 else 0
                edge_data = G.edges[u, v, k]
                highway = edge_data.get("highway", None)
                if isinstance(highway, list):
                    highway = highway[0] if highway else None
                highway = str(highway) if highway else None
            except Exception:
                highway = None
            result[unique_pts[i]] = highway
    except Exception:
        pass

    return result


def _persist_osm_road_types(db_path: str, osm_road_types: dict) -> int:
    """Write OSM-computed road types back to the incidents DB.

    OSM is the authoritative source, so this overwrites any previously
    name-inferred road_type values for geocoded incidents.  Rows are only
    updated when the stored value actually differs from the OSM value, so
    repeated calls are efficient.  Returns the number of rows changed.
    """
    if not osm_road_types:
        return 0
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT incident_number, latitude, longitude, road_type FROM incidents "
                "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
            ).fetchall()
            updates = []
            for incident_number, lat, lon, current_rt in rows:
                lat_f = _safe_float(lat)
                lon_f = _safe_float(lon)
                if lat_f is None or lon_f is None:
                    continue
                key = (round(lat_f, 6), round(lon_f, 6))
                rt = osm_road_types.get(key)
                if rt and rt != (current_rt or ""):
                    updates.append((rt, incident_number))
            if updates:
                conn.executemany(
                    "UPDATE incidents SET road_type = ? WHERE incident_number = ?",
                    updates,
                )
                conn.commit()
            return len(updates)
        finally:
            conn.close()
    except Exception:
        return 0


def _compute_hot_spots_from_db(db_path: str, top_n: int = 100, min_count: int = 2) -> List[List]:
    """
    Compute recency-weighted hot spots from the incident DB.

    Groups incidents by a ~100 m grid (3 decimal-place rounding), then scores
    each location as sum(exp(-days_since / 30)) so that recent incidents count
    more.  Returns a list of [lat, lng, count, hot_score, label] sorted by
    hot_score descending.
    """
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT latitude, longitude, location, reported FROM incidents "
            "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        )
        now = datetime.now()
        spots: Dict[tuple, dict] = {}
        for lat, lon, location, reported in cursor:
            lat = _safe_float(lat)
            lon = _safe_float(lon)
            if lat is None or lon is None:
                continue
            if not _in_lafayette_bounds(lat, lon):
                continue
            key = (round(lat, 3), round(lon, 3))
            if key not in spots:
                spots[key] = {
                    "lat": round(lat, 3),
                    "lon": round(lon, 3),
                    "count": 0,
                    "hot_score": 0.0,
                    "label": str(location or "").strip(),
                }
            spots[key]["count"] += 1
            reported_dt = _parse_reported(reported)
            if reported_dt is not None:
                try:
                    days_old = max(0.0, (now - reported_dt.replace(tzinfo=None)).total_seconds() / 86400.0)
                    weight = math.exp(-days_old / 30.0)
                except Exception:
                    weight = 0.1
            else:
                weight = 0.1
            spots[key]["hot_score"] += weight

        sorted_spots = sorted(
            [s for s in spots.values() if s["count"] >= min_count],
            key=lambda x: x["hot_score"],
            reverse=True,
        )[:top_n]

        return [
            [s["lat"], s["lon"], s["count"], round(s["hot_score"], 3), s["label"]]
            for s in sorted_spots
        ]
    finally:
        conn.close()


def _normalize_location(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().upper())


def _collapse_traffic_control(df: pd.DataFrame, lat_col: str, lon_col: str) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    d = df.copy()

    for c in ["location", "cause", "reported", "assisting", "incident_number"]:
        if c not in d.columns:
            d[c] = ""

    d["__cause_norm"] = d["cause"].astype(str).str.strip().str.upper()
    d["__loc_norm"] = d["location"].apply(_normalize_location)

    tc = d[d["__cause_norm"] == "TRAFFIC CONTROL"].copy()
    non = d[d["__cause_norm"] != "TRAFFIC CONTROL"].copy()

    non = non.drop(columns=["__cause_norm", "__loc_norm"], errors="ignore")

    if tc.empty:
        return non

    tc["__reported_dt"] = pd.to_datetime(tc["reported"], errors="coerce")

    agg = (
        tc.groupby("__loc_norm", dropna=False)
        .agg(
            location=("location", "first"),
            cause=("cause", "first"),
            assisting=("assisting", "first"),
            reported_min=("__reported_dt", "min"),
            reported_max=("__reported_dt", "max"),
            latitude=(lat_col, "mean"),
            longitude=(lon_col, "mean"),
            tc_total_count=("incident_number", "count"),
        )
        .reset_index(drop=True)
    )

    def _fmt_dt(x):
        if pd.isna(x):
            return ""
        try:
            return x.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(x)

    agg["tc_first_reported"] = agg["reported_min"].apply(_fmt_dt)
    agg["tc_last_reported"] = agg["reported_max"].apply(_fmt_dt)

    agg["reported"] = agg.apply(
        lambda r: (
            "TRAFFIC CONTROL (aggregated)"
            + (f" | first: {r['tc_first_reported']}" if r["tc_first_reported"] else "")
            + (f" | last: {r['tc_last_reported']}" if r["tc_last_reported"] else "")
        ),
        axis=1,
    )

    agg["incident_number"] = agg["location"].astype(str).apply(lambda x: f"TC_AGG::{_normalize_location(x)}")

    for c in ["tc_total_count", "tc_first_reported", "tc_last_reported"]:
        if c not in non.columns:
            non[c] = ""

    tc_rows = pd.DataFrame(columns=non.columns)
    for c in tc_rows.columns:
        if c in agg.columns:
            tc_rows[c] = agg[c]
        else:
            tc_rows[c] = ""

    if "tc_total_count" in tc_rows.columns:
        tc_rows["tc_total_count"] = pd.to_numeric(tc_rows["tc_total_count"], errors="coerce").fillna(1).astype(int)

    out = pd.concat([non, tc_rows], ignore_index=True, sort=False)
    return out


def _safe_float(value):
    try:
        if value is None or value == "":
            return None
        number = float(value)
        if math.isnan(number):
            return None
        return number
    except Exception:
        return None


def _safe_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.lower() in {"nan", "none", "null", "undefined"}:
        return ""
    return text


def _parse_reported(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        ts = pd.to_datetime(value, errors="coerce")
        if pd.isna(ts):
            return None
        if hasattr(ts, "to_pydatetime"):
            return ts.to_pydatetime()
        return ts
    except Exception:
        return None


def _format_reported(dt: datetime) -> str:
    if dt is None:
        return ""
    try:
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt)


def _compute_bbox_from_points(lat_min, lat_max, lon_min, lon_max, pad_deg=OSM_PAD_DEG):
    if lat_min is None or lat_max is None or lon_min is None or lon_max is None:
        return (LAF_LAT_MIN, LAF_LAT_MAX, LAF_LON_MIN, LAF_LON_MAX)

    south = float(lat_min) - pad_deg
    north = float(lat_max) + pad_deg
    west = float(lon_min) - pad_deg
    east = float(lon_max) + pad_deg

    south = max(south, LAF_LAT_MIN - 0.10)
    north = min(north, LAF_LAT_MAX + 0.10)
    west = max(west, LAF_LON_MIN - 0.10)
    east = min(east, LAF_LON_MAX + 0.10)

    return (south, north, west, east)


def _stream_osm_intersections_worker(
    conn,
    db_path: str,
    bbox,
    total_points: int,
    osm_cache_dir: str,
    tc_points: List[Tuple[float, float]],
    chunk_size: int,
) -> None:
    try:
        result = _stream_osm_intersections(
            db_path,
            bbox,
            total_points,
            osm_cache_dir,
            tc_points,
            chunk_size=chunk_size,
            allow_subprocess=False,
        )
        conn.send(result)
    except Exception:
        try:
            conn.send([])
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _stream_osm_intersections_in_subprocess(
    db_path: str,
    bbox,
    total_points: int,
    osm_cache_dir: str,
    tc_points: List[Tuple[float, float]],
    chunk_size: int,
) -> List[List]:
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_stream_osm_intersections_worker,
        args=(child_conn, db_path, bbox, total_points, osm_cache_dir, tc_points, chunk_size),
    )
    proc.start()
    child_conn.close()
    try:
        if parent_conn.poll(600):
            return parent_conn.recv()
    except Exception:
        return []
    finally:
        try:
            parent_conn.close()
        except Exception:
            pass
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
    return []


def _stream_osm_intersections(
    db_path: str,
    bbox,
    total_points: int,
    osm_cache_dir: str,
    tc_points: List[Tuple[float, float]],
    chunk_size: int = 800,
    allow_subprocess: bool = True,
):
    if allow_subprocess and _osm_intersections_subprocess_enabled():
        return _stream_osm_intersections_in_subprocess(
            db_path, bbox, total_points, osm_cache_dir, tc_points, chunk_size
        )
    if not importlib.util.find_spec("osmnx"):
        return []

    ox = importlib.import_module("osmnx")
    os.makedirs(osm_cache_dir, exist_ok=True)

    south, north, west, east = bbox
    bbox_id = _hash_bbox((south, north, west, east))
    graphml_path, cache_json = _osm_cache_paths(osm_cache_dir, bbox_id, total_points)

    if os.path.exists(cache_json):
        try:
            with open(cache_json, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
            overall_counts = cached.get("overall_counts", []) or []
            ttl_seconds = _osm_cache_ttl_seconds()
            if ttl_seconds and _cache_is_fresh(cache_json, ttl_seconds):
                return overall_counts
            if len(cached.get("point_nodes", [])) == total_points or cached.get("point_count") == total_points:
                return overall_counts
        except Exception:
            pass

    try:
        if os.path.exists(graphml_path):
            G = ox.load_graphml(graphml_path)
        else:
            G = ox.graph_from_bbox(bbox=(west, south, east, north), network_type="drive", simplify=True)
            ox.save_graphml(G, graphml_path)
    except Exception:
        return []

    try:
        sc = ox.stats.count_streets_per_node(G)
        for n, v in sc.items():
            try:
                G.nodes[n]["street_count"] = int(v)
            except Exception:
                pass
    except Exception:
        pass

    def street_count_or_degree(n):
        try:
            v = G.nodes[n].get("street_count", None)
            if v is not None:
                return int(v)
        except Exception:
            pass
        try:
            return int(G.degree(n))
        except Exception:
            return 0

    intersection_nodes = set()
    for n in G.nodes:
        if street_count_or_degree(n) >= OSM_INTERSECTION_MIN_STREETS:
            intersection_nodes.add(n)

    if not intersection_nodes:
        return []

    counts: Dict[str, int] = {}

    def _process_points(xs, ys):
        if not xs:
            return
        try:
            nearest_edges = ox.distance.nearest_edges(G, X=xs, Y=ys)
        except Exception:
            return
        for e in nearest_edges:
            try:
                u = e[0]
                v = e[1]
            except Exception:
                continue
            su = street_count_or_degree(u)
            sv = street_count_or_degree(v)
            chosen = u if su >= sv else v
            if chosen not in intersection_nodes:
                other = v if chosen == u else u
                if other in intersection_nodes:
                    chosen = other
                else:
                    continue
            node_id = str(chosen)
            counts[node_id] = counts.get(node_id, 0) + 1

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT latitude, longitude, cause
            FROM incidents
            """
        )
        xs: List[float] = []
        ys: List[float] = []
        for lat, lon, cause in cursor:
            lat = _safe_float(lat)
            lon = _safe_float(lon)
            if lat is None or lon is None:
                continue
            if not _in_lafayette_bounds(lat, lon):
                continue
            if str(cause or "").strip().upper() == "TRAFFIC CONTROL":
                continue
            xs.append(float(lon))
            ys.append(float(lat))
            if len(xs) >= chunk_size:
                _process_points(xs, ys)
                xs = []
                ys = []
        if xs:
            _process_points(xs, ys)
    finally:
        conn.close()

    if tc_points:
        xs = [float(lon) for _, lon in tc_points]
        ys = [float(lat) for lat, _ in tc_points]
        _process_points(xs, ys)

    overall_counts: List[List] = []
    for node_id, cnt in counts.items():
        try:
            n = int(node_id)
        except Exception:
            continue
        try:
            node = G.nodes[n]
            lat = float(node.get("y"))
            lng = float(node.get("x"))
            overall_counts.append([round(lat, 6), round(lng, 6), int(cnt), str(node_id)])
        except Exception:
            continue

    overall_counts.sort(key=lambda x: x[2], reverse=True)

    try:
        payload = {
            "overall_counts": overall_counts,
            "bbox": {"south": south, "north": north, "west": west, "east": east},
            "min_streets": OSM_INTERSECTION_MIN_STREETS,
            "point_count": total_points,
        }
        with open(cache_json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        pass

    return overall_counts


def _write_streaming_datajs(
    db_path: str, output_datajs: str, osm_cache_dir: str
) -> Tuple[float, float, List[List]]:
    # Pre-compute OSM road types per incident coordinate (optional, requires osmnx)
    osm_road_types: dict = {}
    try:
        osm_road_types = _precompute_osm_road_types(db_path, osm_cache_dir)
    except Exception:
        pass

    # Persist OSM road types back to DB so the road-type filter works for all
    # incidents going forward, including a backfill of historical rows.
    if osm_road_types:
        try:
            _persist_osm_road_types(db_path, osm_road_types)
        except Exception:
            pass

    # Pre-compute recency-weighted hot spots
    hot_spots: List[List] = []
    try:
        hot_spots = _compute_hot_spots_from_db(db_path)
    except Exception:
        pass

    conn = sqlite3.connect(db_path)
    tc_groups: Dict[str, Dict[str, object]] = {}

    lat_min = None
    lat_max = None
    lon_min = None
    lon_max = None
    center_lat_sum = 0.0
    center_lon_sum = 0.0
    center_count = 0
    non_tc_count = 0
    unlocated_count = 0

    map_dir = os.path.dirname(output_datajs) or "."
    os.makedirs(map_dir, exist_ok=True)
    tmp_dir = os.path.join(map_dir, ".tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    osm_intersections: List[List] = []
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=tmp_dir, delete=False) as handle:
            tmp_path = handle.name
            _stream_jsonjs_header(handle)
            first = True

            cursor = conn.execute(
                """
                SELECT location, cause, reported, assisting, incident_number, latitude, longitude,
                       weather_temp_f, weather_precip_prob, weather_precip_in,
                       weather_wind_speed_mph, weather_wind_gust_mph, weather_visibility_mi,
                       weather_sky_cover_pct, weather_observed_at, weather_source,
                       hour_of_day, day_of_week, is_school_day,
                       nws_flash_flood_warning, nws_severe_thunderstorm_warning, nws_tornado_watch,
                       road_type, created_at
                FROM incidents
                """
            )
            for (
                location,
                cause,
                reported,
                assisting,
                incident_number,
                lat,
                lon,
                weather_temp_f,
                weather_precip_prob,
                weather_precip_in,
                weather_wind_speed_mph,
                weather_wind_gust_mph,
                weather_visibility_mi,
                weather_sky_cover_pct,
                weather_observed_at,
                weather_source,
                hour_of_day,
                day_of_week,
                is_school_day,
                nws_flood,
                nws_storm,
                nws_tornado,
                db_road_type,
                created_at,
            ) in cursor:
                lat = _safe_float(lat)
                lon = _safe_float(lon)
                if lat is None or lon is None:
                    unlocated_count += 1
                    continue

                cause_str = str(cause or "").strip()
                cause_norm = cause_str.upper()

                if cause_norm == "TRAFFIC CONTROL":
                    key = _normalize_location(location)
                    entry = tc_groups.get(key)
                    if entry is None:
                        entry = {
                            "location": location or "",
                            "assisting": assisting or "",
                            "lat_sum": 0.0,
                            "lon_sum": 0.0,
                            "count": 0,
                            "reported_min": None,
                            "reported_max": None,
                        }
                        tc_groups[key] = entry
                    entry["lat_sum"] = float(entry["lat_sum"]) + float(lat)
                    entry["lon_sum"] = float(entry["lon_sum"]) + float(lon)
                    entry["count"] = int(entry["count"]) + 1
                    reported_dt = _parse_reported(reported)
                    if reported_dt is not None:
                        rmin = entry.get("reported_min")
                        rmax = entry.get("reported_max")
                        if rmin is None or reported_dt < rmin:
                            entry["reported_min"] = reported_dt
                        if rmax is None or reported_dt > rmax:
                            entry["reported_max"] = reported_dt
                    continue

                if not _in_lafayette_bounds(lat, lon):
                    continue

                loc = str(location or "").strip()
                assist = str(assisting or "").strip()
                reported_str = str(reported or "").strip()
                lat_r = round(float(lat), 6)
                lon_r = round(float(lon), 6)
                # Prefer OSM-computed road type, then DB-stored value.
                # _resolve_highway_type normalises unusual OSMnx values
                # ("road", "path", "track", …) to filter-compatible categories
                # via name inference so they always appear in the correct bucket.
                raw_hw = (
                    osm_road_types.get((lat_r, lon_r))
                    or _safe_text(db_road_type)
                    or None
                )
                highway_type = _resolve_highway_type(raw_hw or "", loc)
                incident = [
                    lat_r,
                    lon_r,
                    reported_str,
                    loc,
                    cause_str,
                    assist,
                    1.0,
                    1,
                    _safe_float(weather_temp_f),
                    _safe_float(weather_precip_prob),
                    _safe_float(weather_precip_in),
                    _safe_float(weather_wind_speed_mph),
                    _safe_float(weather_wind_gust_mph),
                    _safe_float(weather_visibility_mi),
                    _safe_float(weather_sky_cover_pct),
                    _safe_text(weather_observed_at),
                    _safe_text(weather_source),
                    # Enrichment fields (indices 17-22)
                    hour_of_day,
                    day_of_week,
                    is_school_day,
                    nws_flood,
                    nws_storm,
                    nws_tornado,
                    # OSM / inferred road classification (index 23)
                    highway_type,
                    # created_at ISO timestamp (index 24)
                    _safe_text(created_at),
                ]
                first = _stream_jsonjs_incident(handle, incident, first)
                non_tc_count += 1

                center_lat_sum += float(lat)
                center_lon_sum += float(lon)
                center_count += 1

                lat_min = float(lat) if lat_min is None else min(lat_min, float(lat))
                lat_max = float(lat) if lat_max is None else max(lat_max, float(lat))
                lon_min = float(lon) if lon_min is None else min(lon_min, float(lon))
                lon_max = float(lon) if lon_max is None else max(lon_max, float(lon))

            tc_points: List[Tuple[float, float]] = []
            tc_count = 0
            for entry in tc_groups.values():
                count = int(entry["count"]) if entry["count"] else 0
                if count == 0:
                    continue
                lat = float(entry["lat_sum"]) / count
                lon = float(entry["lon_sum"]) / count
                if not _in_lafayette_bounds(lat, lon):
                    continue
                reported_first = _format_reported(entry.get("reported_min"))
                reported_last = _format_reported(entry.get("reported_max"))
                reported_parts = "TRAFFIC CONTROL (aggregated)"
                if reported_first:
                    reported_parts += f" | first: {reported_first}"
                if reported_last:
                    reported_parts += f" | last: {reported_last}"
                incident = [
                    round(float(lat), 6),
                    round(float(lon), 6),
                    reported_parts,
                    str(entry.get("location") or "").strip(),
                    "TRAFFIC CONTROL",
                    str(entry.get("assisting") or "").strip(),
                    1.0,
                    int(count),
                    None,   # weather_temp_f
                    None,   # weather_precip_prob
                    None,   # weather_precip_in
                    None,   # weather_wind_speed_mph
                    None,   # weather_wind_gust_mph
                    None,   # weather_visibility_mi
                    None,   # weather_sky_cover_pct
                    "",     # weather_observed_at
                    "",     # weather_source
                    None,   # hour_of_day
                    None,   # day_of_week
                    None,   # is_school_day
                    None,   # nws_flood
                    None,   # nws_storm
                    None,   # nws_tornado
                    None,   # highway_type
                    "",     # created_at
                ]
                first = _stream_jsonjs_incident(handle, incident, first)
                tc_count += 1
                tc_points.append((lat, lon))

                center_lat_sum += float(lat)
                center_lon_sum += float(lon)
                center_count += 1

                lat_min = float(lat) if lat_min is None else min(lat_min, float(lat))
                lat_max = float(lat) if lat_max is None else max(lat_max, float(lat))
                lon_min = float(lon) if lon_min is None else min(lon_min, float(lon))
                lon_max = float(lon) if lon_max is None else max(lon_max, float(lon))

            total_points = non_tc_count + tc_count
            bbox = _compute_bbox_from_points(lat_min, lat_max, lon_min, lon_max)
            osm_intersections = _stream_osm_intersections(
                db_path, bbox, total_points, osm_cache_dir, tc_points
            )
            _stream_jsonjs_footer(handle, osm_intersections, hot_spots, unlocated_count)
    finally:
        conn.close()

    os.replace(tmp_path, output_datajs)
    _ensure_world_readable(output_datajs)

    if center_count == 0:
        return (30.2241, -92.0198, [])

    center_lat = center_lat_sum / center_count
    center_lon = center_lon_sum / center_count
    return (center_lat, center_lon, osm_intersections)


def _write_map_html(center_lat: float, center_lng: float, output_map: str, output_datajs: str) -> None:
    map_dir = os.path.dirname(output_map) or "."
    datajs_dir = os.path.dirname(output_datajs) or "."
    _ensure_world_readable_dir(map_dir)
    _ensure_world_readable_dir(datajs_dir)
    if os.path.abspath(map_dir) != os.path.abspath(datajs_dir):
        map_datajs_path = os.path.join(map_dir, os.path.basename(output_datajs))
        try:
            with open(output_datajs, "r", encoding="utf-8") as handle:
                datajs_text = handle.read()
            _write_text_if_changed(map_datajs_path, datajs_text)
            _ensure_world_readable(map_datajs_path)
        except Exception:
            pass

    base_map = folium.Map(location=[center_lat, center_lng], zoom_start=12, control_scale=True)

    html = base_map.get_root().render()

    m = re.search(r"(?:var|let|const)\s+(map_[a-zA-Z0-9_]+)\s*=\s*L\.map\(", html)
    if not m:
        return
    map_var = m.group(1)

    rel_datajs = os.path.relpath(output_datajs, os.path.dirname(output_map) or ".").replace(os.sep, "/")
    if os.path.abspath(map_dir) != os.path.abspath(datajs_dir):
        rel_datajs = os.path.basename(output_datajs)

    html = html.replace(
        "</head>",
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />\n'
        f'<script src="{rel_datajs}"></script>\n'
        f'<script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>\n'
        f"</head>",
    )

    _current_year = datetime.now().year
    year_options = '<option value="">--</option>' + "".join(
        f'<option value="{y}">{y}</option>'
        for y in range(_current_year - 2, _current_year + 1)
    )

    inject = f"""
<style>
  :root {{
    --panel-bg: rgba(255,255,255,0.92);
    --panel-border: rgba(0,0,0,0.12);
    --shadow: 0 10px 30px rgba(0,0,0,0.18);
    --radius: 14px;
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}

  #controlPanel {{
    position: absolute;
    top: 12px;
    left: 12px;
    z-index: 9999999;
    background: var(--panel-bg);
    border: 1px solid var(--panel-border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    font-family: var(--font);
    font-size: 13px;
    width: min(380px, calc(100vw - 24px));
    max-height: 82vh;
    overflow: hidden;
    box-sizing: border-box;
    backdrop-filter: blur(10px);
  }}

  #panelHeader {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    border-bottom: 1px solid rgba(0,0,0,0.08);
  }}

  #panelTitle {{
    font-weight: 700;
    font-size: 14px;
  }}

  #panelSubtitle {{
    font-size: 12px;
    color: rgba(0,0,0,0.62);
    margin-top: 2px;
  }}

  #panelCountBar {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    padding: 8px 12px 6px 12px;
    border-bottom: 1px solid rgba(0,0,0,0.06);
  }}

  #panelQuickFilters {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 0 12px 8px 12px;
    border-bottom: 1px solid rgba(0,0,0,0.06);
  }}

  @keyframes pill-pop {{
    0% {{ transform: scale(1); }}
    40% {{ transform: scale(1.12); }}
    100% {{ transform: scale(1); }}
  }}

  .pill {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(0,0,0,0.05);
    font-size: 12px;
    white-space: nowrap;
    transition: background 0.2s ease;
  }}

  .pill.updated {{
    animation: pill-pop 0.25s ease;
  }}

  #panelBody {{
    padding: 10px 12px 12px 12px;
    overflow-y: auto;
    max-height: calc(82vh - 140px);
    transition: max-height 0.28s ease, opacity 0.2s ease, transform 0.28s ease;
  }}

  #weatherWidget {{
    position: absolute;
    top: 12px;
    right: 12px;
    z-index: 9999998;
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid var(--panel-border);
    border-radius: 12px;
    box-shadow: var(--shadow);
    font-family: var(--font);
    font-size: 12px;
    padding: 10px 12px;
    max-width: 240px;
    backdrop-filter: blur(10px);
    pointer-events: none;
  }}

  #weatherWidget .weather-title {{
    font-weight: 700;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: rgba(0,0,0,0.55);
    margin-bottom: 6px;
  }}

  #weatherWidgetBody {{
    transition: opacity 0.35s ease;
  }}

  #weatherWidgetBody.updating {{
    opacity: 0.5;
  }}

  .section {{
    padding: 6px 0;
    border-bottom: 1px solid rgba(0,0,0,0.06);
  }}

  .section:last-child {{
    border-bottom: none;
  }}

  .section-title {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: rgba(0,0,0,0.5);
    font-weight: 700;
    margin-bottom: 6px;
  }}

  .row {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 8px;
  }}

  .row-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px;
  }}

  .row-checks {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 8px;
  }}

  .row-note {{
    font-size: 12px;
    color: rgba(0,0,0,0.55);
    margin-top: -2px;
  }}

  /* ── Road name search ──────────────────────────────────── */
  .road-search-wrap {{
    position: relative;
    display: flex;
    align-items: center;
  }}

  .road-search-icon {{
    position: absolute;
    left: 10px;
    top: 50%;
    transform: translateY(-50%);
    pointer-events: none;
    color: rgba(0,0,0,0.38);
    display: flex;
    align-items: center;
    line-height: 0;
  }}

  #roadSearch {{
    width: 100%;
    box-sizing: border-box;
    padding: 8px 30px 8px 34px;
    border-radius: 10px;
    border: 1px solid rgba(0,0,0,0.14);
    background: rgba(255,255,255,0.95);
    font-family: var(--font);
    font-size: 13px;
    outline: none;
    transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
    color: inherit;
  }}

  #roadSearch::placeholder {{
    color: rgba(0,0,0,0.35);
  }}

  #roadSearch:hover:not(:focus) {{
    border-color: rgba(0,0,0,0.28);
    background: #fff;
  }}

  #roadSearch:focus {{
    border-color: #2b6cb0;
    box-shadow: 0 0 0 3px rgba(43,108,176,0.15);
    background: #fff;
  }}

  #roadSearch.has-value {{
    border-color: #2b6cb0;
    background: #fff;
  }}

  .road-search-clear {{
    position: absolute;
    right: 7px;
    top: 50%;
    transform: translateY(-50%);
    background: rgba(0,0,0,0.10);
    border: none;
    border-radius: 50%;
    width: 18px;
    height: 18px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    font-size: 11px;
    color: rgba(0,0,0,0.45);
    line-height: 1;
    padding: 0;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.15s ease, background 0.15s ease, color 0.15s ease;
    font-family: var(--font);
  }}

  .road-search-clear.visible {{
    opacity: 1;
    pointer-events: auto;
  }}

  .road-search-clear:hover {{
    background: rgba(0,0,0,0.18);
    color: rgba(0,0,0,0.65);
  }}

  .road-search-hint {{
    font-size: 11px;
    color: rgba(0,0,0,0.40);
    margin-top: 4px;
    display: none;
  }}

  .road-search-wrap:focus-within ~ .road-search-hint,
  .road-search-wrap.has-value ~ .road-search-hint {{
    display: block;
  }}

  .muted {{
    color: rgba(0,0,0,0.55);
    font-size: 12px;
  }}

  .weather-summary {{
    display: grid;
    gap: 4px;
  }}

  .weather-summary .weather-main {{
    font-weight: 600;
  }}

  .weather-summary .weather-meta {{
    font-size: 12px;
    color: rgba(0,0,0,0.55);
  }}

  .row label {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-right: 6px;
    white-space: normal;
    min-width: 0;
    flex: 1 1 140px;
  }}

  select, button {{
    font-family: var(--font);
    font-size: 13px;
  }}

  select {{
    padding: 6px 8px;
    border-radius: 10px;
    border: 1px solid rgba(0,0,0,0.14);
    background: rgba(255,255,255,0.95);
    outline: none;
    width: 100%;
    box-sizing: border-box;
    cursor: pointer;
    transition: border-color 0.15s ease, background 0.15s ease;
    -webkit-appearance: none;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23999'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 8px center;
    padding-right: 26px;
  }}

  select:hover:not(:disabled) {{
    border-color: rgba(0,0,0,0.3);
    background-color: #fff;
  }}

  select:focus {{
    border-color: #2b6cb0;
    box-shadow: 0 0 0 3px rgba(43,108,176,0.15);
  }}

  input[type="range"] {{
    width: 100%;
  }}

  .range-field {{
    background: rgba(0,0,0,0.03);
    border-radius: 12px;
    padding: 10px 12px;
    display: grid;
    gap: 6px;
  }}

  .range-label {{
    font-weight: 600;
    font-size: 12px;
  }}

  .range-values {{
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: rgba(0,0,0,0.6);
  }}

  .range-inputs {{
    display: grid;
    gap: 6px;
  }}

  input[type="checkbox"] {{
    width: 17px;
    height: 17px;
    min-width: 17px;
  }}

  .row-checks label {{
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 5px 8px;
    border-radius: 8px;
    background: rgba(0,0,0,0.03);
    cursor: pointer;
    transition: background 0.12s ease;
  }}

  .row-checks label:hover {{
    background: rgba(43,108,176,0.08);
    color: #2b6cb0;
  }}

  select:disabled {{
    background: #f2f2f2;
    background-image: none;
    color: rgba(0,0,0,0.38);
    cursor: not-allowed;
  }}

  input[type="checkbox"] {{
    cursor: pointer;
    accent-color: #2b6cb0;
    transition: opacity 0.15s ease;
  }}

  .row label, .row-checks label {{
    cursor: pointer;
    transition: color 0.12s ease;
  }}

  .row label:hover, .row-checks label:hover {{
    color: #2b6cb0;
  }}

  button {{
    padding: 8px 10px;
    border-radius: 12px;
    border: 1px solid rgba(0,0,0,0.14);
    background: rgba(255,255,255,0.95);
    cursor: pointer;
    transition: background 0.12s ease, border-color 0.12s ease, transform 0.1s ease, box-shadow 0.12s ease;
    -webkit-tap-highlight-color: transparent;
  }}

  button, select, input[type="checkbox"], #roadSearch, .road-search-clear {{
    touch-action: manipulation;
  }}

  button:hover {{
    background: #fff;
    border-color: rgba(0,0,0,0.25);
    box-shadow: 0 1px 4px rgba(0,0,0,0.10);
  }}

  button:active {{
    transform: scale(0.97);
    box-shadow: none;
  }}

  .incident-nav {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 6px;
  }}

  .incident-nav button {{
    padding: 4px 8px;
    border-radius: 8px;
    border: 1px solid rgba(0,0,0,0.2);
    background: rgba(255,255,255,0.95);
    line-height: 1;
  }}

  .incident-nav button:disabled {{
    opacity: 0.4;
    cursor: not-allowed;
  }}

  .incident-count {{
    font-size: 12px;
    font-weight: 600;
  }}

  .incident-count-marker {{
    border-radius: 999px;
    background: rgba(212, 230, 255, 0.9);
    color: #1a365d;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    border: 2px solid #2b6cb0;
    box-shadow: 0 1px 4px rgba(0,0,0,0.18);
  }}

  #controlPanel {{
    transition: max-height 0.28s ease, transform 0.28s ease;
  }}

  #controlPanel.collapsed {{
    max-height: 160px;
  }}

  #controlPanel.collapsed #panelBody {{
    max-height: 0;
    opacity: 0;
    transform: translateY(-4px);
    padding-top: 0;
    padding-bottom: 0;
    pointer-events: none;
  }}

  @media (max-width: 768px) {{
    #weatherWidget {{
      top: 12px;
      right: 12px;
      left: auto;
      width: min(70vw, 260px);
      max-width: 260px;
      border-radius: 14px;
      pointer-events: none;
      font-size: 13px;
    }}
  }}

  @media (max-width: 520px) {{
    #controlPanel {{
      left: 0;
      top: auto;
      bottom: 0;
      width: 100%;
      height: 100dvh;
      max-height: 100dvh;
      border-radius: 18px 18px 0 0;
      box-shadow: 0 -10px 30px rgba(0,0,0,0.22);
      display: flex;
      flex-direction: column;
      padding-bottom: max(0px, env(safe-area-inset-bottom));
    }}

    #panelBody {{
      max-height: none;
      height: auto;
      flex: 1 1 auto;
      overflow-y: auto;
      -webkit-overflow-scrolling: touch;
      padding: 10px 14px calc(32px + env(safe-area-inset-bottom)) 14px;
    }}

    #mobileHandle {{
      display: block;
      width: 48px;
      height: 5px;
      border-radius: 999px;
      background: rgba(0,0,0,0.2);
      margin: 10px auto 2px auto;
    }}

    #panelHeader {{
      padding: 6px 14px 8px 14px;
      flex-direction: column;
      align-items: flex-start;
      gap: 8px;
    }}

    #panelTitle {{
      font-size: 16px;
    }}

    #panelSubtitle {{
      font-size: 13px;
    }}

    #panelActions {{
      width: 100%;
      display: flex;
      gap: 10px;
    }}

    #panelActions button {{
      flex: 1;
      padding: 14px 12px;
      font-size: 16px;
      border-radius: 14px;
      font-weight: 600;
      min-height: 48px;
    }}

    select {{
      padding: 13px 14px;
      padding-right: 32px;
      font-size: 16px;
      border-radius: 12px;
      min-height: 48px;
    }}

    input[type="checkbox"] {{
      width: 22px;
      height: 22px;
      min-width: 22px;
    }}

    .row label {{
      font-size: 15px;
      gap: 10px;
      padding: 4px 0;
      min-height: 44px;
      align-items: center;
      flex: 1 1 140px;
    }}

    .row-checks label {{
      font-size: 15px;
      gap: 10px;
      padding: 6px 8px;
      min-height: 44px;
      align-items: center;
      background: rgba(0,0,0,0.03);
      border-radius: 10px;
    }}

    .pill {{
      font-size: 14px;
      padding: 8px 14px;
    }}

    #panelCountBar {{
      padding: 8px 14px 6px 14px;
      gap: 10px;
    }}

    #panelQuickFilters {{
      padding: 0 14px 8px 14px;
    }}

    #panelQuickFilters label {{
      font-size: 15px;
      gap: 10px;
      min-height: 44px;
      align-items: center;
    }}

    #panelQuickFilters input[type="checkbox"] {{
      width: 22px;
      height: 22px;
    }}

    #roadSearch {{
      font-size: 16px;  /* prevents iOS zoom on focus */
      padding: 11px 34px 11px 38px;
      min-height: 48px;
    }}

    .road-search-icon {{
      left: 12px;
    }}

    .road-search-clear {{
      right: 10px;
      width: 22px;
      height: 22px;
      font-size: 12px;
    }}

    .section {{
      padding: 8px 0;
    }}

    .section-title {{
      font-size: 12px;
      margin-bottom: 10px;
    }}

    .row {{
      gap: 8px;
      margin-bottom: 10px;
    }}

    .row-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}

    .row-checks {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }}

    .row-note {{
      font-size: 13px;
    }}

    #controlPanel.collapsed {{
      max-height: 130px;
    }}

    .leaflet-control-zoom a,
    .leaflet-touch .leaflet-bar a {{
      width: 44px;
      height: 44px;
      line-height: 44px;
      font-size: 20px;
    }}

    .leaflet-control-layers-toggle {{
      width: 44px;
      height: 44px;
      background-size: 24px 24px;
    }}
  }}

  @media (max-width: 340px) {{
    .row-grid {{
      grid-template-columns: 1fr;
    }}

    .row-checks {{
      grid-template-columns: 1fr;
    }}

    #panelActions button {{
      font-size: 14px;
    }}
  }}

  #mobileHandle {{
    display: none;
  }}

  /* ── Normalized Rates panel ─────────────────────────────────────── */
  .rates-group {{
    margin-bottom: 10px;
  }}
  .rates-group-title {{
    font-size: 11px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 5px;
  }}
  .rates-span-note {{
    font-weight: 400;
    font-size: 10px;
    color: var(--text-muted);
    text-transform: none;
    letter-spacing: 0;
  }}
  .rates-row {{
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 3px;
  }}
  .rates-label {{
    flex: 0 0 90px;
    font-size: 11px;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .rates-bar-wrap {{
    flex: 1;
    background: rgba(0,0,0,0.10);
    border-radius: 3px;
    height: 7px;
    overflow: hidden;
    min-width: 0;
  }}
  .rates-bar-fill {{
    height: 7px;
    border-radius: 3px;
    transition: width 0.35s ease;
    min-width: 2px;
  }}
  .rates-val {{
    flex: 0 0 auto;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    min-width: 60px;
    text-align: right;
  }}
  .rates-n {{
    color: var(--text-muted);
    font-size: 10px;
  }}
  .rates-ratio {{
    font-size: 11px;
    font-style: italic;
    color: var(--text-muted);
    margin-top: 2px;
    margin-bottom: 4px;
    line-height: 1.4;
  }}
  .rates-subtitle {{
    font-size: 11px;
    color: var(--text-muted);
    margin-bottom: 8px;
  }}
  .rates-no-data {{
    font-size: 12px;
    color: var(--text-muted);
    font-style: italic;
  }}

  .insight-list {{
    margin: 0;
    padding-left: 18px;
    display: grid;
    gap: 6px;
    color: var(--text);
    font-size: 12px;
    line-height: 1.35;
  }}

  .insight-list li strong {{
    font-weight: 700;
  }}
</style>

<div id="controlPanel">
  <div id="mobileHandle"></div>

  <div id="panelHeader">
    <div>
      <div id="panelTitle">Traffic Incidents</div>
      <div id="panelSubtitle">Lafayette bounds enforced</div>
    </div>
    <div id="panelActions">
      <button id="panelToggleBtn" type="button">Filters</button>
      <button id="clearBtn" type="button">Clear</button>
    </div>
  </div>

  <div id="panelCountBar">
    <div class="pill"><span>Total:</span><span id="countTotal">0</span></div>
    <div class="pill"><span>Filtered:</span><span id="countFiltered">0</span></div>
    <div class="pill"><span>In view:</span><span id="countInView">0</span></div>
  </div>
  <div id="panelQuickFilters">
    <label><input type="checkbox" id="chkInViewOnly"> In view only</label>
  </div>

  <div id="panelBody">

    <div class="section">
      <div class="section-title">Road Search</div>
      <div class="road-search-wrap">
        <span class="road-search-icon" aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="5.5" cy="5.5" r="4" stroke="currentColor" stroke-width="1.5"/>
            <line x1="8.6" y1="8.6" x2="12.5" y2="12.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
        </span>
        <input type="text" id="roadSearch" placeholder="Search by road name…" autocomplete="off" spellcheck="false" aria-label="Filter by road name">
        <button class="road-search-clear" id="roadSearchClear" aria-label="Clear road search">&#x2715;</button>
      </div>
      <div class="road-search-hint">Typos OK &mdash; starts filtering after the first letter</div>
    </div>

    <div class="section">
      <div class="section-title">Incident Type</div>
      <div class="row row-grid">
        <label>Group:
          <select id="causeGroupSelect">
            <option value="__ALL__">All groups</option>
          </select>
        </label>

        <label>Type:
          <select id="causeSelect">
            <option value="__ALL__">All types</option>
          </select>
        </label>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Date &amp; Time</div>
      <div class="row row-checks">
        <label><input type="checkbox" id="chkTodayOnly"> Today only</label>
        <label><input type="checkbox" id="chkRushHour"> Rush hour only</label>
        <label><input type="checkbox" id="chkSchoolDay"> School days only</label>
      </div>

      <div class="row row-grid">
        <label>Month:
          <select id="monthSelect">
            <option value="">All months</option>
            <option value="01">Jan</option><option value="02">Feb</option><option value="03">Mar</option>
            <option value="04">Apr</option><option value="05">May</option><option value="06">Jun</option>
            <option value="07">Jul</option><option value="08">Aug</option><option value="09">Sep</option>
            <option value="10">Oct</option><option value="11">Nov</option><option value="12">Dec</option>
          </select>
        </label>

        <label>Day:
          <select id="daySelect">
            <option value="">All days</option>
            {''.join([f'<option value="{str(i).zfill(2)}">{i}</option>' for i in range(1, 32)])}
          </select>
        </label>

        <label>Year:
          <select id="yearSelect">
            {year_options}
          </select>
        </label>
      </div>

      <div class="row row-grid">
        <label>Day of week:
          <select id="dowSelect">
            <option value="all">All days</option>
            <option value="1">Monday</option>
            <option value="2">Tuesday</option>
            <option value="3">Wednesday</option>
            <option value="4">Thursday</option>
            <option value="5">Friday</option>
            <option value="6">Saturday</option>
            <option value="0">Sunday</option>
          </select>
        </label>

        <label>Day type:
          <select id="dayTypeSelect">
            <option value="all">All</option>
            <option value="weekday">Weekdays</option>
            <option value="weekend">Weekends</option>
          </select>
        </label>
      </div>

      <div class="row row-grid">
        <label>Time of day:
          <select id="timeBlockSelect">
            <option value="all">All hours</option>
            <option value="morning">Morning (6–10 am)</option>
            <option value="midday">Midday (10 am–3 pm)</option>
            <option value="evening">Evening (3–7 pm)</option>
            <option value="night">Night (7 pm–12 am)</option>
            <option value="latenight">Late night (12–6 am)</option>
          </select>
        </label>

        <label>Road type:
          <select id="roadTypeSelect">
            <option value="any">Any road</option>
            <option value="motorway">Interstate / motorway</option>
            <option value="trunk">US highway (trunk)</option>
            <option value="primary">Primary arterial</option>
            <option value="secondary">Secondary / tertiary</option>
            <option value="residential">Residential / local / unclassified</option>
          </select>
        </label>
      </div>
    </div>

    <div class="section">
      <div class="section-title">NWS Active Alerts</div>
      <div class="row row-checks">
        <label><input type="checkbox" id="chkFloodWarning"> Flash flood warning</label>
        <label><input type="checkbox" id="chkThunderstormWarning"> Severe storm warning</label>
        <label><input type="checkbox" id="chkTornadoWatch"> Tornado watch</label>
      </div>
      <div class="row row-note">Show only incidents recorded during active NWS alerts for Lafayette Parish.</div>
    </div>


    <div class="section">
      <div class="section-title">Weather (when available)</div>
      <div class="row row-checks">
        <label><input type="checkbox" id="chkWeatherOnly"> Weather data only</label>
      </div>
      <div class="row row-grid">
        <label>Temperature:
          <select id="tempBand">
            <option value="any">Any</option>
            <option value="cold">Cold (≤50°F)</option>
            <option value="mild">Mild (50-70°F)</option>
            <option value="warm">Warm (70-85°F)</option>
            <option value="hot">Hot (≥85°F)</option>
          </select>
        </label>

        <label>Precip chance:
          <select id="precipBand">
            <option value="any">Any</option>
            <option value="low">Low (&lt;20%)</option>
            <option value="med">Medium (20-60%)</option>
            <option value="high">High (≥60%)</option>
          </select>
        </label>
      </div>
      <div class="row row-grid">
        <label>Wind:
          <select id="windBand">
            <option value="any">Any</option>
            <option value="calm">Calm (&lt;10 mph)</option>
            <option value="breezy">Breezy (10-20 mph)</option>
            <option value="windy">Windy (≥20 mph)</option>
          </select>
        </label>

        <label>Visibility:
          <select id="visBand">
            <option value="any">Any</option>
            <option value="low">Low (&lt;3 mi)</option>
            <option value="hazy">Hazy (3-10 mi)</option>
            <option value="clear">Clear (≥10 mi)</option>
          </select>
        </label>
      </div>
      <div class="row row-grid">
        <label>Liquid precip:
          <select id="precipAmountBand">
            <option value="any">Any</option>
            <option value="none">None (0 in)</option>
            <option value="light">Light (≤0.10 in)</option>
            <option value="moderate">Moderate (0.10-0.50 in)</option>
            <option value="heavy">Heavy (≥0.50 in)</option>
          </select>
        </label>

        <label>Cloud cover:
          <select id="cloudBand">
            <option value="any">Any</option>
            <option value="clear">Mostly clear (&lt;25%)</option>
            <option value="partly">Partly cloudy (25-70%)</option>
            <option value="overcast">Overcast (≥70%)</option>
          </select>
        </label>
      </div>
      <div class="row row-note">Weather filters ignore incidents without weather unless you choose weather-only or a specific condition.</div>
    </div>

    <div class="section">
      <div class="section-title">Normalized Rates</div>
      <div id="ratesContent"><span class="rates-no-data">Waiting for data&hellip;</span></div>
    </div>

    <div class="section">
      <div class="section-title">Smart Insights</div>
      <div id="insightsContent"><span class="rates-no-data">Waiting for data&hellip;</span></div>
    </div>

    <div class="section">
      <div class="section-title">Current weather</div>
      <div class="row row-note" id="currentWeather">Loading latest snapshot...</div>
    </div>

    <div class="section">
      <div class="section-title">Layers</div>
      <div class="row row-checks">
        <label><input type="checkbox" id="chkPoints" checked> Points</label>
        <label><input type="checkbox" id="chkHeat"> Heat</label>
        <label><input type="checkbox" id="chkIntersections"> Rounded</label>
        <label><input type="checkbox" id="chkOsmIntersections"> OSM</label>
        <label><input type="checkbox" id="chkMicro"> Micro</label>
        <label><input type="checkbox" id="chkRings"> Rings</label>
        <label><input type="checkbox" id="chkHotSpots"> Hot Spots</label>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Precision</div>
      <div class="row row-grid">
        <label>Top N:
          <select id="topNSelect">
            <option value="5">5</option>
            <option value="10" selected>10</option>
            <option value="20">20</option>
            <option value="50">50</option>
          </select>
        </label>

        <label>Rounded precision:
          <select id="precIntersections">
            <option value="3" selected>~100m</option>
            <option value="4">~10m</option>
          </select>
        </label>

        <label>Micro precision:
          <select id="precMicro">
            <option value="4" selected>~10m</option>
            <option value="5">~1m</option>
          </select>
        </label>
      </div>
    </div>

  </div>
</div>

<div id="weatherWidget" aria-live="polite">
  <div class="weather-title">Current weather</div>
  <div id="weatherWidgetBody">Loading latest snapshot...</div>
</div>

<div id="unlocatedBanner" style="display:none;position:fixed;bottom:12px;left:50%;transform:translateX(-50%);
     background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:8px 16px;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:13px;
     z-index:9999998;box-shadow:0 2px 8px rgba(0,0,0,0.15);max-width:90vw;text-align:center;">
  ⚠ <span id="unlocatedCount"></span> incident(s) could not be placed on the map (geocoding pending).
  They will appear automatically once coordinates are resolved.
</div>

<script>
(function() {{
  const INCIDENTS = window.INCIDENTS_DATA || [];
  const OSM_INTERSECTIONS = window.OSM_INTERSECTIONS_DATA || [];
  const HOT_SPOTS = window.HOT_SPOTS_DATA || [];
  const renderer = L.canvas({{ padding: 0.5 }});
  const isCoarsePointer = window.matchMedia ? window.matchMedia("(pointer: coarse)").matches : false;
  const isTouch = (L && L.Browser && L.Browser.touch) || isCoarsePointer;

  const els = {{
    panel: document.getElementById("controlPanel"),
    toggleBtn: document.getElementById("panelToggleBtn"),
    clearBtn: document.getElementById("clearBtn"),

    countTotal: document.getElementById("countTotal"),
    countFiltered: document.getElementById("countFiltered"),
    countInView: document.getElementById("countInView"),

    causeSelect: document.getElementById("causeSelect"),
    causeGroupSelect: document.getElementById("causeGroupSelect"),
    chkInViewOnly: document.getElementById("chkInViewOnly"),

    monthSelect: document.getElementById("monthSelect"),
    daySelect: document.getElementById("daySelect"),
    yearSelect: document.getElementById("yearSelect"),
    chkTodayOnly: document.getElementById("chkTodayOnly"),
    dayTypeSelect: document.getElementById("dayTypeSelect"),
    timeBlockSelect: document.getElementById("timeBlockSelect"),

    chkWeatherOnly: document.getElementById("chkWeatherOnly"),
    tempBand: document.getElementById("tempBand"),
    precipBand: document.getElementById("precipBand"),
    precipAmountBand: document.getElementById("precipAmountBand"),
    windBand: document.getElementById("windBand"),
    visBand: document.getElementById("visBand"),
    cloudBand: document.getElementById("cloudBand"),
    currentWeather: document.getElementById("currentWeather"),
    weatherWidgetBody: document.getElementById("weatherWidgetBody"),

    roadSearch: document.getElementById("roadSearch"),
    roadSearchClear: document.getElementById("roadSearchClear"),

    chkRushHour: document.getElementById("chkRushHour"),
    chkSchoolDay: document.getElementById("chkSchoolDay"),
    dowSelect: document.getElementById("dowSelect"),
    roadTypeSelect: document.getElementById("roadTypeSelect"),

    chkFloodWarning: document.getElementById("chkFloodWarning"),
    chkThunderstormWarning: document.getElementById("chkThunderstormWarning"),
    chkTornadoWatch: document.getElementById("chkTornadoWatch"),

    chkPoints: document.getElementById("chkPoints"),
    chkHeat: document.getElementById("chkHeat"),
    chkIntersections: document.getElementById("chkIntersections"),
    chkOsmIntersections: document.getElementById("chkOsmIntersections"),
    chkMicro: document.getElementById("chkMicro"),
    chkRings: document.getElementById("chkRings"),
    chkHotSpots: document.getElementById("chkHotSpots"),

    topNSelect: document.getElementById("topNSelect"),
    precIntersections: document.getElementById("precIntersections"),
    precMicro: document.getElementById("precMicro"),

  }};

  const IDX_LAT = 0;
  const IDX_LNG = 1;
  const IDX_REPORTED = 2;
  const IDX_LOCATION = 3;
  const IDX_CAUSE = 4;
  const IDX_ASSIST = 5;
  const IDX_WEIGHT = 6;
  const IDX_COUNT = 7;
  const IDX_TEMP_F = 8;
  const IDX_PRECIP_PROB = 9;
  const IDX_PRECIP_IN = 10;
  const IDX_WIND_SPEED = 11;
  const IDX_WIND_GUST = 12;
  const IDX_VISIBILITY = 13;
  const IDX_SKY_COVER = 14;
  const IDX_WEATHER_AT = 15;
  const IDX_WEATHER_SOURCE = 16;
  const IDX_HOUR = 17;
  const IDX_DOW = 18;
  const IDX_SCHOOL_DAY = 19;
  const IDX_NWS_FLOOD = 20;
  const IDX_NWS_STORM = 21;
  const IDX_NWS_TORNADO = 22;
  const IDX_HIGHWAY = 23;
  const IDX_CREATED_AT = 24;

  function esc(s) {{
    return String(s || "").replace(/[&<>"']/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[c]));
  }}

  function normalizeText(value) {{
    if (value == null) return "";
    const text = String(value || "").trim();
    if (!text) return "";
    const lower = text.toLowerCase();
    if (lower === "nan" || lower === "none" || lower === "null" || lower === "undefined") return "";
    return text;
  }}

  function formatCentralTime(value) {{
    const text = normalizeText(value);
    if (!text) return "";
    const dt = new Date(text);
    if (Number.isNaN(dt.getTime())) return text;
    try {{
      const formatter = new Intl.DateTimeFormat("en-US", {{
        timeZone: "America/Chicago",
        month: "short",
        day: "2-digit",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit"
      }});
      return formatter.format(dt) + " CT";
    }} catch (e) {{
      return text;
    }}
  }}

  function popupHtml(row) {{
    const occurrences = (row.length > IDX_COUNT && row[IDX_COUNT] != null) ? parseInt(row[IDX_COUNT], 10) : 1;
    const occLine = (occurrences && occurrences > 1) ? ("<br>Occurrences: " + occurrences) : "";
    const temp = (row.length > IDX_TEMP_F) ? row[IDX_TEMP_F] : null;
    const pop = (row.length > IDX_PRECIP_PROB) ? row[IDX_PRECIP_PROB] : null;
    const precipIn = (row.length > IDX_PRECIP_IN) ? row[IDX_PRECIP_IN] : null;
    const windSpeed = (row.length > IDX_WIND_SPEED) ? row[IDX_WIND_SPEED] : null;
    const windGust = (row.length > IDX_WIND_GUST) ? row[IDX_WIND_GUST] : null;
    const visibility = (row.length > IDX_VISIBILITY) ? row[IDX_VISIBILITY] : null;
    const skyCover = (row.length > IDX_SKY_COVER) ? row[IDX_SKY_COVER] : null;
    const wAt = formatCentralTime((row.length > IDX_WEATHER_AT) ? row[IDX_WEATHER_AT] : "");
    const wSource = normalizeText((row.length > IDX_WEATHER_SOURCE) ? row[IDX_WEATHER_SOURCE] : "");
    const tempText = (temp !== null && temp !== "" && !isNaN(temp)) ? (parseFloat(temp).toFixed(0) + "°F") : null;
    const popText = (pop !== null && pop !== "" && !isNaN(pop)) ? (parseFloat(pop).toFixed(0) + "% precip") : null;
    const precipText = (precipIn !== null && precipIn !== "" && !isNaN(precipIn)) ? (parseFloat(precipIn).toFixed(2) + " in precip") : null;
    const windText = (windSpeed !== null && windSpeed !== "" && !isNaN(windSpeed)) ? (parseFloat(windSpeed).toFixed(0) + " mph wind") : null;
    const gustText = (windGust !== null && windGust !== "" && !isNaN(windGust)) ? ("gust " + parseFloat(windGust).toFixed(0) + " mph") : null;
    const visText = (visibility !== null && visibility !== "" && !isNaN(visibility)) ? (parseFloat(visibility).toFixed(1) + " mi vis") : null;
    const skyText = (skyCover !== null && skyCover !== "" && !isNaN(skyCover)) ? (parseFloat(skyCover).toFixed(0) + "% clouds") : null;
    const weatherParts = [];
    if (tempText) weatherParts.push(tempText);
    if (popText) weatherParts.push(popText);
    if (precipText) weatherParts.push(precipText);
    if (windText) weatherParts.push(windText);
    if (gustText) weatherParts.push(gustText);
    if (visText) weatherParts.push(visText);
    if (skyText) weatherParts.push(skyText);
    const weatherLine = weatherParts.length
      ? ("<br>Weather: " + weatherParts.join(" · ") + (wAt ? (" <span class='muted'>@" + esc(wAt) + "</span>") : ""))
      : "";
    const weatherSource = wSource ? ("<br><span class='muted'>Source: " + esc(wSource) + "</span>") : "";
    const hw = (row.length > IDX_HIGHWAY && row[IDX_HIGHWAY]) ? row[IDX_HIGHWAY] : null;
    const hwLine = hw ? ("<br>Road type: " + esc(hw)) : "";
    const hourVal = (row.length > IDX_HOUR && row[IDX_HOUR] != null) ? row[IDX_HOUR] : null;
    const dowVal = (row.length > IDX_DOW && row[IDX_DOW] != null) ? row[IDX_DOW] : null;
    const dowNames = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
    const contextParts = [];
    if (dowVal !== null) contextParts.push(dowNames[dowVal] || "");
    if (hourVal !== null) contextParts.push(String(hourVal).padStart(2,"0") + ":xx");
    const schoolDayVal = (row.length > IDX_SCHOOL_DAY) ? row[IDX_SCHOOL_DAY] : null;
    if (schoolDayVal === 1) contextParts.push("school day");
    const ctxLine = contextParts.length ? ("<br>Context: " + esc(contextParts.join(" · "))) : "";
    const alertParts = [];
    if (row.length > IDX_NWS_FLOOD && (row[IDX_NWS_FLOOD] === 1 || row[IDX_NWS_FLOOD] === true)) alertParts.push("Flash Flood Warning");
    if (row.length > IDX_NWS_STORM && (row[IDX_NWS_STORM] === 1 || row[IDX_NWS_STORM] === true)) alertParts.push("Severe Thunderstorm Warning");
    if (row.length > IDX_NWS_TORNADO && (row[IDX_NWS_TORNADO] === 1 || row[IDX_NWS_TORNADO] === true)) alertParts.push("Tornado Watch");
    const alertLine = alertParts.length ? ("<br><b>NWS Active: " + esc(alertParts.join(", ")) + "</b>") : "";
    return "Location: " + esc(row[IDX_LOCATION]) +
           "<br>Type: " + esc(row[IDX_CAUSE]) +
           "<br>Reported: " + esc(row[IDX_REPORTED]) +
           "<br>Assisting: " + esc(row[IDX_ASSIST]) +
           occLine +
           hwLine +
           ctxLine +
           alertLine +
           weatherLine +
           weatherSource;
  }}

  function buildWeatherSummary(row) {{
    if (!row) return {{ hasData: false, html: "", popup: "" }};
    const temp = weatherNumber(row, IDX_TEMP_F);
    const pop = weatherNumber(row, IDX_PRECIP_PROB);
    const precipIn = weatherNumber(row, IDX_PRECIP_IN);
    const windSpeed = weatherNumber(row, IDX_WIND_SPEED);
    const windGust = weatherNumber(row, IDX_WIND_GUST);
    const visibility = weatherNumber(row, IDX_VISIBILITY);
    const skyCover = weatherNumber(row, IDX_SKY_COVER);
    const wAt = formatCentralTime((row.length > IDX_WEATHER_AT) ? row[IDX_WEATHER_AT] : "");
    const wSource = normalizeText((row.length > IDX_WEATHER_SOURCE) ? row[IDX_WEATHER_SOURCE] : "");
    const parts = [];
    if (temp != null) parts.push(temp.toFixed(0) + "°F");
    if (pop != null) parts.push(pop.toFixed(0) + "% precip");
    if (precipIn != null) parts.push(precipIn.toFixed(2) + " in precip");
    if (windSpeed != null) parts.push(windSpeed.toFixed(0) + " mph wind");
    if (windGust != null) parts.push("gust " + windGust.toFixed(0) + " mph");
    if (visibility != null) parts.push(visibility.toFixed(1) + " mi vis");
    if (skyCover != null) parts.push(skyCover.toFixed(0) + "% clouds");
    const hasData = parts.length > 0;
    const mainLine = hasData ? parts.join(" · ") : "";
    const meta = [];
    if (wAt) meta.push("Observed " + esc(wAt));
    if (wSource) meta.push("Source: " + esc(wSource));
    const metaLine = meta.length ? ("<div class='weather-meta'>" + meta.join(" · ") + "</div>") : "";
    const html = hasData
      ? ("<div class='weather-summary'><div class='weather-main'>" + esc(mainLine) + "</div>" + metaLine + "</div>")
      : "No weather snapshot available.";
    const popup = hasData
      ? ("<div class='weather-summary'><div class='weather-main'>Current weather</div><div>" + esc(mainLine) + "</div>" + metaLine + "</div>")
      : "Current weather unavailable.";
    return {{ hasData, html, popup }};
  }}

  function createIncidentPopup(rows) {{
    let idx = 0;
    const container = document.createElement("div");
    if (typeof L !== "undefined" && L.DomEvent) {{
      L.DomEvent.disableClickPropagation(container);
    }}

    function render() {{
      const row = rows[idx];
      const hasNav = rows.length > 1;
      container.innerHTML =
        (hasNav
          ? (
            "<div class='incident-nav'>" +
              "<button class='incident-prev' type='button' aria-label='Previous incident'>&larr;</button>" +
              "<div class='incident-count'>Incident " + (idx + 1) + " of " + rows.length + "</div>" +
              "<button class='incident-next' type='button' aria-label='Next incident'>&rarr;</button>" +
            "</div>"
          )
          : ""
        ) +
        "<div class='incident-details'>" + popupHtml(row) + "</div>";

      if (hasNav) {{
        const prevBtn = container.querySelector(".incident-prev");
        const nextBtn = container.querySelector(".incident-next");
        prevBtn.disabled = idx <= 0;
        nextBtn.disabled = idx >= rows.length - 1;

        prevBtn.addEventListener("click", function(e) {{
          e.preventDefault();
          e.stopPropagation();
          if (idx > 0) {{
            idx -= 1;
            render();
          }}
        }});

        nextBtn.addEventListener("click", function(e) {{
          e.preventDefault();
          e.stopPropagation();
          if (idx < rows.length - 1) {{
            idx += 1;
            render();
          }}
        }});
      }}
    }}

    render();
    return container;
  }}

  function parseReported(reported) {{
    if (!reported) return null;
    const s = String(reported).trim();
    if (!s) return null;

    function normalizeYY(yyRaw) {{
      if (!yyRaw) return null;
      if (yyRaw.length === 2) {{
        const yyNum = parseInt(yyRaw, 10);
        return String(yyNum <= 69 ? (2000 + yyNum) : (1900 + yyNum));
      }}
      return yyRaw;
    }}

    function buildValidatedDate(yy, mm, dd, hh, mi) {{
      const y = parseInt(yy, 10);
      const m = parseInt(mm, 10);
      const d = parseInt(dd, 10);
      const h = (hh == null ? 0 : parseInt(hh, 10));
      const n = (mi == null ? 0 : parseInt(mi, 10));
      if (!Number.isFinite(y) || !Number.isFinite(m) || !Number.isFinite(d) || !Number.isFinite(h) || !Number.isFinite(n)) return null;
      const dt = new Date(y, m - 1, d, h, n, 0, 0);
      if (isNaN(dt.getTime())) return null;
      if (dt.getFullYear() !== y || dt.getMonth() !== (m - 1) || dt.getDate() !== d) return null;
      return dt;
    }}

    // MM/DD/YYYY or MM/DD/YY with optional time seconds and AM/PM.
    const m1 = s.match(/(\\d{{1,2}})\\/(\\d{{1,2}})\\/(\\d{{2,4}})(?:(?:\\s+|\\s*[T@-]\\s*)(\\d{{1,2}}):(\\d{{2}})(?::\\d{{2}})?(?:\\s*([AaPp][Mm]))?)?/);
    if (m1) {{
      const mm = m1[1].padStart(2, "0");
      const dd = m1[2].padStart(2, "0");
      const yy = normalizeYY(m1[3]);
      let hh = m1[4] != null ? parseInt(m1[4], 10) : 0;
      const mi = m1[5] != null ? parseInt(m1[5], 10) : 0;
      const ap = m1[6] ? String(m1[6]).toLowerCase() : null;
      if (ap === "pm" && hh < 12) hh += 12;
      if (ap === "am" && hh === 12) hh = 0;
      if (hh < 0 || hh > 23) return null;
      const dt = buildValidatedDate(yy, mm, dd, hh, mi);
      if (!dt) return null;
      return {{ mm, dd, yy, hh, mi, dt }};
    }}

    // ISO-like fallback: YYYY-MM-DD[T ]HH:MM[:SS]
    const m2 = s.match(/(\d{{4}})-(\d{{2}})-(\d{{2}})(?:[T ](\d{{2}}):(\d{{2}})(?::\d{{2}})?)?/);
    if (m2) {{
      const yy = m2[1];
      const mm = m2[2];
      const dd = m2[3];
      const hh = m2[4] != null ? parseInt(m2[4], 10) : 0;
      const mi = m2[5] != null ? parseInt(m2[5], 10) : 0;
      const dt = buildValidatedDate(yy, mm, dd, hh, mi);
      if (!dt) return null;
      return {{ mm, dd, yy, hh, mi, dt }};
    }}

    // MM/DD without year (assume current year), optional time.
    const m3 = s.match(/^(\\d{{1,2}})\\/(\\d{{1,2}})(?!\\/)(?:(?:\\s+|\\s*[T@-]\\s*)(\\d{{1,2}}):(\\d{{2}})(?::\\d{{2}})?(?:\\s*([AaPp][Mm]))?)?\\s*$/);
    if (m3) {{
      const now = new Date();
      const mm = m3[1].padStart(2, "0");
      const dd = m3[2].padStart(2, "0");
      const yy = String(now.getFullYear());
      let hh = m3[3] != null ? parseInt(m3[3], 10) : 0;
      const mi = m3[4] != null ? parseInt(m3[4], 10) : 0;
      const ap = m3[5] ? String(m3[5]).toLowerCase() : null;
      if (ap === "pm" && hh < 12) hh += 12;
      if (ap === "am" && hh === 12) hh = 0;
      if (hh < 0 || hh > 23) return null;
      const dt = buildValidatedDate(yy, mm, dd, hh, mi);
      if (!dt) return null;
      return {{ mm, dd, yy, hh, mi, dt }};
    }}

    return null;
  }}

  function dayTypeOf(dt) {{
    const d = dt.getDay();
    return (d === 0 || d === 6) ? "weekend" : "weekday";
  }}

  function timeBlockOf(hh) {{
    if (hh == null) return "unknown";
    if (hh >= 6 && hh < 10) return "morning";
    if (hh >= 10 && hh < 15) return "midday";
    if (hh >= 15 && hh < 19) return "evening";
    if (hh >= 19 && hh <= 23) return "night";
    if (hh >= 0 && hh < 6) return "latenight";
    return "unknown";
  }}

  function groupByRounded(points, decimals) {{
    const m = new Map();
    for (const row of points) {{
      const key = row[0].toFixed(decimals) + "," + row[1].toFixed(decimals);
      const v = m.get(key) || {{
        key,
        lat: parseFloat(row[0].toFixed(decimals)),
        lng: parseFloat(row[1].toFixed(decimals)),
        count: 0,
        sample: row
      }};
      v.count += 1;
      m.set(key, v);
    }}
    return Array.from(m.values()).sort((a, b) => b.count - a.count);
  }}

  function groupByExactLocation(points) {{
    const m = new Map();
    for (const row of points) {{
      const lat = row[0];
      const lng = row[1];
      const key = lat.toFixed(6) + "," + lng.toFixed(6);
      const v = m.get(key) || {{
        key,
        lat,
        lng,
        rows: []
      }};
      v.rows.push(row);
      m.set(key, v);
    }}
    return Array.from(m.values());
  }}

  function getCenterFromData() {{
    if (INCIDENTS.length === 0) return [30.2241, -92.0198];
    let sLat = 0, sLng = 0, n = 0;
    for (const r of INCIDENTS) {{
      sLat += r[0];
      sLng += r[1];
      n += 1;
    }}
    return [sLat / n, sLng / n];
  }}

  function buildCauseDropdown() {{
    if (!els.causeSelect) return;
    const set = new Set();
    for (const r of INCIDENTS) {{
      const c = String(r[IDX_CAUSE] || "").trim();
      if (c) set.add(c);
    }}
    const causes = Array.from(set.values()).sort((a,b) => a.localeCompare(b));
    while (els.causeSelect.options.length > 1) {{
      els.causeSelect.remove(1);
    }}
    for (const c of causes) {{
      const opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      els.causeSelect.appendChild(opt);
    }}
  }}

  const CAUSE_GROUPS = [
    {{ id: "fire", label: "Fire", keywords: ["FIRE"] }},
    {{ id: "accident", label: "Accident", keywords: ["ACCIDENT", "CRASH", "COLLISION", "WRECK", "MVA", "MVC"] }}
  ];

  function buildCauseGroupDropdown() {{
    if (!els.causeGroupSelect) return;
    while (els.causeGroupSelect.options.length > 1) {{
      els.causeGroupSelect.remove(1);
    }}
    for (const g of CAUSE_GROUPS) {{
      const opt = document.createElement("option");
      opt.value = g.id;
      opt.textContent = g.label;
      els.causeGroupSelect.appendChild(opt);
    }}
  }}

  function normalizeCause(cause) {{
    return String(cause || "").trim().toUpperCase();
  }}

  function matchesCause(row, selected) {{
    if (!selected || selected === "__ALL__") return true;
    return String(row[IDX_CAUSE] || "").trim() === selected;
  }}

  function matchesCauseGroup(row, selectedGroup) {{
    if (!selectedGroup || selectedGroup === "__ALL__") return true;
    const causeNorm = normalizeCause(row[IDX_CAUSE]);
    const group = CAUSE_GROUPS.find((g) => g.id === selectedGroup);
    if (!group) return true;
    return group.keywords.some((kw) => causeNorm.includes(kw));
  }}

  function matchesDateFilter(row, f) {{
    if (!f.todayOnly && !f.mm && !f.dd && !f.yy) return true;
    const pr = parseReported(row[IDX_REPORTED]);
    if (f.todayOnly) {{
      const now = new Date();
      const ty = now.getFullYear(), tm = now.getMonth(), td = now.getDate();
      const todayMM = String(tm + 1).padStart(2, "0");
      const todayDD = String(td).padStart(2, "0");
      const todayYY = String(ty);
      // Check reported date first
      if (pr && pr.yy === todayYY && pr.mm === todayMM && pr.dd === todayDD) return true;
      // Fallback: check created_at (when incident entered our system)
      const createdRaw = (row.length > IDX_CREATED_AT) ? row[IDX_CREATED_AT] : null;
      if (createdRaw) {{
        const cDt = new Date(createdRaw);
        if (!isNaN(cDt.getTime())) {{
          if (cDt.getFullYear() === ty && cDt.getMonth() === tm && cDt.getDate() === td) return true;
        }}
      }}
      return false;
    }}
    if (!pr) return false;
    if (f.yy && pr.yy !== f.yy) return false;
    if (f.mm && pr.mm !== f.mm) return false;
    if (f.dd && pr.dd !== f.dd) return false;
    return true;
  }}

  function matchesDayType(row, dayType) {{
    if (dayType === "all") return true;
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr || !pr.dt) return false;
    return dayTypeOf(pr.dt) === dayType;
  }}

  function matchesTimeBlock(row, block) {{
    if (block === "all") return true;
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr) return false;
    const tb = timeBlockOf(pr.hh);
    if (tb === "unknown") return false;
    return tb === block;
  }}

  function weatherNumber(row, idx) {{
    if (!row || row.length <= idx) return null;
    const val = parseFloat(row[idx]);
    return Number.isFinite(val) ? val : null;
  }}

  function hasWeatherData(row) {{
    return (
      weatherNumber(row, IDX_TEMP_F) != null ||
      weatherNumber(row, IDX_PRECIP_PROB) != null ||
      weatherNumber(row, IDX_PRECIP_IN) != null ||
      weatherNumber(row, IDX_WIND_SPEED) != null ||
      weatherNumber(row, IDX_VISIBILITY) != null ||
      weatherNumber(row, IDX_SKY_COVER) != null
    );
  }}

  function latestWeatherRow() {{
    let best = null;
    let bestTs = null;
    for (const row of INCIDENTS) {{
      if (!hasWeatherData(row)) continue;
      const raw = normalizeText(row.length > IDX_WEATHER_AT ? row[IDX_WEATHER_AT] : "");
      const dt = raw ? new Date(raw) : null;
      const ts = dt && !Number.isNaN(dt.getTime()) ? dt.getTime() : null;
      if (!best) {{
        best = row;
        bestTs = ts;
        continue;
      }}
      if (ts != null && (bestTs == null || ts > bestTs)) {{
        best = row;
        bestTs = ts;
      }}
    }}
    return best;
  }}

  function matchesTempBand(temp, band) {{
    if (band === "any") return true;
    if (temp == null) return false;
    if (band === "cold") return temp <= 50;
    if (band === "mild") return temp > 50 && temp < 70;
    if (band === "warm") return temp >= 70 && temp < 85;
    if (band === "hot") return temp >= 85;
    return true;
  }}

  function matchesPrecipBand(pop, band) {{
    if (band === "any") return true;
    if (pop == null) return false;
    if (band === "low") return pop < 20;
    if (band === "med") return pop >= 20 && pop < 60;
    if (band === "high") return pop >= 60;
    return true;
  }}

  function matchesPrecipAmountBand(precipIn, band) {{
    if (band === "any") return true;
    if (precipIn == null) return false;
    if (band === "none") return precipIn <= 0.01;
    if (band === "light") return precipIn > 0.01 && precipIn <= 0.10;
    if (band === "moderate") return precipIn > 0.10 && precipIn < 0.50;
    if (band === "heavy") return precipIn >= 0.50;
    return true;
  }}

  function matchesWindBand(wind, band) {{
    if (band === "any") return true;
    if (wind == null) return false;
    if (band === "calm") return wind < 10;
    if (band === "breezy") return wind >= 10 && wind < 20;
    if (band === "windy") return wind >= 20;
    return true;
  }}

  function matchesVisibilityBand(vis, band) {{
    if (band === "any") return true;
    if (vis == null) return false;
    if (band === "low") return vis < 3;
    if (band === "hazy") return vis >= 3 && vis < 10;
    if (band === "clear") return vis >= 10;
    return true;
  }}

  function matchesCloudBand(cloud, band) {{
    if (band === "any") return true;
    if (cloud == null) return false;
    if (band === "clear") return cloud < 25;
    if (band === "partly") return cloud >= 25 && cloud < 70;
    if (band === "overcast") return cloud >= 70;
    return true;
  }}

  function isRushHourFromParsed(pr) {{
    if (!pr || pr.hh == null) return false;
    const dow = pr.dt ? pr.dt.getDay() : -1;  // 0=Sun, 6=Sat in JS
    if (dow === 0 || dow === 6) return false;  // weekend
    const h = pr.hh;
    return (h >= 7 && h < 9) || (h >= 16 && h < 19);
  }}

  function matchesRushHour(row, checked) {{
    if (!checked) return true;
    // Prefer stored hour_of_day/day_of_week if available
    const storedHour = (row.length > IDX_HOUR && row[IDX_HOUR] != null) ? row[IDX_HOUR] : null;
    const storedDow = (row.length > IDX_DOW && row[IDX_DOW] != null) ? row[IDX_DOW] : null;
    if (storedHour !== null && storedDow !== null) {{
      // Stored as Python weekday: 0=Mon, 6=Sun
      if (storedDow >= 5) return false;
      return (storedHour >= 7 && storedHour < 9) || (storedHour >= 16 && storedHour < 19);
    }}
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr) return false;
    return isRushHourFromParsed(pr);
  }}

  function isSchoolDayJS(dt) {{
    // Mirrors Python _is_school_day — LPSS heuristic
    if (!dt) return false;
    const dow = dt.getDay(); // 0=Sun, 6=Sat
    if (dow === 0 || dow === 6) return false;
    const month = dt.getMonth() + 1; // 1-12
    const day = dt.getDate();
    if (month === 6 || month === 7) return false;         // summer break
    if (month === 8 && day < 15) return false;            // starts mid-Aug
    if (month === 12 && day >= 20) return false;          // winter break
    if (month === 1 && day <= 3) return false;
    if (month === 11 && day >= 21 && day <= 27) {{
      try {{
        const nov1 = new Date(dt.getFullYear(), 10, 1); // Nov 1
        const thu = (4 - nov1.getDay() + 7) % 7;        // days to 1st Thu
        const thanksgiving = 1 + thu + 21;               // 4th Thursday
        if (day >= thanksgiving - 1 && day <= thanksgiving + 1) return false;
      }} catch (e) {{}}
    }}
    return true;
  }}

  function matchesSchoolDay(row, checked) {{
    if (!checked) return true;
    const val = (row.length > IDX_SCHOOL_DAY) ? row[IDX_SCHOOL_DAY] : null;
    // Use stored value when available
    if (val === 1 || val === true) return true;
    if (val === 0 || val === false) return false;
    // val is null — fall back to computing from the reported timestamp
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr || !pr.dt) return false;
    return isSchoolDayJS(pr.dt);
  }}

  function matchesDow(row, dowValue) {{
    if (!dowValue || dowValue === "all") return true;
    const target = parseInt(dowValue, 10);
    const storedDow = (row.length > IDX_DOW && row[IDX_DOW] != null) ? row[IDX_DOW] : null;
    if (storedDow !== null) {{
      // Stored as Python weekday (0=Mon, 6=Sun); JS getDay() is 0=Sun,1=Mon,...,6=Sat
      // DOW select values use JS convention: 0=Sun,1=Mon,...,6=Sat
      // Convert Python dow to JS: jsDow = (pyDow + 1) % 7
      const jsDow = (storedDow + 1) % 7;
      return jsDow === target;
    }}
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr || !pr.dt) return false;
    return pr.dt.getDay() === target;
  }}

  // ── Fuzzy road name search ─────────────────────────────────────────────────
  // Space-optimized Levenshtein distance (two-row DP).
  function levenshtein(a, b) {{
    if (a === b) return 0;
    const m = a.length, n = b.length;
    if (m === 0) return n;
    if (n === 0) return m;
    let r0 = Array.from({{length: n + 1}}, (_, i) => i);
    let r1 = new Array(n + 1);
    for (let i = 1; i <= m; i++) {{
      r1[0] = i;
      for (let j = 1; j <= n; j++) {{
        r1[j] = a[i-1] === b[j-1] ? r0[j-1] : 1 + Math.min(r0[j], r1[j-1], r0[j-1]);
      }}
      [r0, r1] = [r1, r0];
    }}
    return r0[n];
  }}

  // Check if a single query word fuzzy-matches anywhere in the location string.
  // Strategy: substring first (zero cost), then word-level edit distance.
  function fuzzyWordMatch(word, loc) {{
    if (loc.includes(word)) return true;
    if (word.length <= 2) return false;  // short words must be exact
    const maxDist = word.length <= 4 ? 1 : 2;
    const tokens = loc.split(/[\s\-\/,&.]+/);
    for (const tok of tokens) {{
      if (tok.length === 0) continue;
      if (Math.abs(tok.length - word.length) > maxDist + 1) continue;
      if (levenshtein(word, tok) <= maxDist) return true;
    }}
    return false;
  }}

  function matchesRoadSearch(row, term) {{
    if (!term) return true;
    const loc = (row[IDX_LOCATION] || "").toLowerCase();
    const words = term.toLowerCase().split(/\s+/).filter(w => w.length > 0);
    return words.every(w => fuzzyWordMatch(w, loc));
  }}
  // ───────────────────────────────────────────────────────────────────────────

  function matchesRoadType(row, roadType) {{
    if (!roadType || roadType === "any") return true;
    const hw = (row.length > IDX_HIGHWAY) ? row[IDX_HIGHWAY] : null;
    if (!hw) return false;
    const hwLower = String(hw).toLowerCase();
    if (roadType === "motorway") return hwLower.includes("motorway");
    if (roadType === "trunk") return hwLower.includes("trunk");
    if (roadType === "primary") return hwLower.includes("primary");
    // tertiary is the OSM tier between secondary and residential; group with secondary
    if (roadType === "secondary") return hwLower.includes("secondary") || hwLower.includes("tertiary");
    // unclassified, service, and living_street are all local/minor roads
    if (roadType === "residential") return (
      hwLower.includes("residential") ||
      hwLower === "unclassified" ||
      hwLower === "service" ||
      hwLower === "living_street"
    );
    return true;
  }}

  function matchesNwsAlerts(row, f) {{
    if (!f.chkFlood && !f.chkStorm && !f.chkTornado) return true;
    const flood = (row.length > IDX_NWS_FLOOD) ? row[IDX_NWS_FLOOD] : null;
    const storm = (row.length > IDX_NWS_STORM) ? row[IDX_NWS_STORM] : null;
    const tornado = (row.length > IDX_NWS_TORNADO) ? row[IDX_NWS_TORNADO] : null;
    if (f.chkFlood && !(flood === 1 || flood === true)) return false;
    if (f.chkStorm && !(storm === 1 || storm === true)) return false;
    if (f.chkTornado && !(tornado === 1 || tornado === true)) return false;
    return true;
  }}

  function matchesWeather(row, f) {{
    const temp = weatherNumber(row, IDX_TEMP_F);
    const pop = weatherNumber(row, IDX_PRECIP_PROB);
    const precipIn = weatherNumber(row, IDX_PRECIP_IN);
    const windSpeed = weatherNumber(row, IDX_WIND_SPEED);
    const visibility = weatherNumber(row, IDX_VISIBILITY);
    const skyCover = weatherNumber(row, IDX_SKY_COVER);
    const hasWeather = hasWeatherData(row);
    if (f.weatherOnly && !hasWeather) return false;
    if (!matchesTempBand(temp, f.tempBand)) return false;
    if (!matchesPrecipBand(pop, f.precipBand)) return false;
    if (!matchesPrecipAmountBand(precipIn, f.precipAmountBand)) return false;
    if (!matchesWindBand(windSpeed, f.windBand)) return false;
    if (!matchesVisibilityBand(visibility, f.visBand)) return false;
    if (!matchesCloudBand(skyCover, f.cloudBand)) return false;
    return true;
  }}

  // ── Normalized Rates panel ──────────────────────────────────────────────────
  // Walk the full dataset once to find its date span, then count how many rush-
  // hour hours, school-day hours, etc. actually exist in that span.  These
  // totals become the denominators for per-hour rates.  The result is cached
  // because the full incident list doesn't change during a page session.
  let _dataSpanCache = null;

  function computeDataSpan() {{
    if (!INCIDENTS || !INCIDENTS.length) return null;
    let minMs = Infinity, maxMs = -Infinity;
    for (const row of INCIDENTS) {{
      const pr = parseReported(row[IDX_REPORTED]);
      if (!pr || !pr.dt) continue;
      const ms = pr.dt.getTime();
      if (ms < minMs) minMs = ms;
      if (ms > maxMs) maxMs = ms;
    }}
    if (!isFinite(minMs)) return null;

    // Walk day-by-day from earliest to latest incident and tally hours
    // Rush hour definition (mirrors Python): Mon-Fri 7-9 AM + 4-7 PM = 5 hrs/day
    let rushHours = 0, nonRushWeekdayHours = 0;
    let schoolDayHours = 0, nonSchoolWeekdayHours = 0;
    const d = new Date(minMs);
    d.setHours(0, 0, 0, 0);
    const end = new Date(maxMs);
    end.setHours(23, 59, 59, 999);

    while (d <= end) {{
      const dow = d.getDay(); // 0=Sun, 6=Sat
      if (dow !== 0 && dow !== 6) {{
        rushHours += 5;          // 7-9 am (2 hrs) + 4-7 pm (3 hrs)
        nonRushWeekdayHours += 19;
        if (isSchoolDayJS(d)) {{
          schoolDayHours += 24;
        }} else {{
          nonSchoolWeekdayHours += 24;
        }}
      }}
      d.setDate(d.getDate() + 1);
    }}
    return {{ rushHours, nonRushWeekdayHours, schoolDayHours, nonSchoolWeekdayHours }};
  }}

  function _getDataSpan() {{
    if (!_dataSpanCache) _dataSpanCache = computeDataSpan();
    return _dataSpanCache;
  }}

  // Row-level helpers that reuse stored DB values when present (fast path)
  function _isRushHourRow(row) {{
    const h   = (row.length > IDX_HOUR && row[IDX_HOUR] != null) ? row[IDX_HOUR] : null;
    const dow = (row.length > IDX_DOW  && row[IDX_DOW]  != null) ? row[IDX_DOW]  : null;
    if (h !== null && dow !== null) {{
      if (dow >= 5) return false; // Python weekday: 5=Sat, 6=Sun
      return (h >= 7 && h < 9) || (h >= 16 && h < 19);
    }}
    const pr = parseReported(row[IDX_REPORTED]);
    return isRushHourFromParsed(pr);
  }}

  function _isSchoolDayRow(row) {{
    const val = (row.length > IDX_SCHOOL_DAY) ? row[IDX_SCHOOL_DAY] : null;
    if (val === 1) return true;
    if (val === 0) return false;
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr || !pr.dt) return false;
    return isSchoolDayJS(pr.dt);
  }}

  function _isWeekendRow(row) {{
    const dow = (row.length > IDX_DOW && row[IDX_DOW] != null) ? row[IDX_DOW] : null;
    if (dow !== null) return dow >= 5; // Python: 5=Sat, 6=Sun
    const pr = parseReported(row[IDX_REPORTED]);
    if (!pr || !pr.dt) return false;
    const d = pr.dt.getDay();
    return d === 0 || d === 6;
  }}

  function renderRatesPanel(rows) {{
    const el = document.getElementById("ratesContent");
    if (!el) return;

    const span = _getDataSpan();
    if (!span || span.rushHours === 0) {{
      el.innerHTML = '<span class="rates-no-data">Not enough data to compute rates.</span>';
      return;
    }}

    // Tally incidents per category from the filtered row set
    let rushCount = 0, nonRushCount = 0;
    let schoolCount = 0, nonSchoolCount = 0;
    let rainCount = 0, noRainCount = 0;
    let windyCount = 0, calmCount = 0;
    let lowVisCount = 0, goodVisCount = 0;

    for (const row of rows) {{
      const weekend = _isWeekendRow(row);
      if (!weekend) {{
        if (_isRushHourRow(row)) rushCount++; else nonRushCount++;
        if (_isSchoolDayRow(row)) schoolCount++; else nonSchoolCount++;
      }}
      const precipIn   = (row.length > IDX_PRECIP_IN   && row[IDX_PRECIP_IN]   != null) ? parseFloat(row[IDX_PRECIP_IN])   : null;
      const precipProb = (row.length > IDX_PRECIP_PROB  && row[IDX_PRECIP_PROB]  != null) ? parseFloat(row[IDX_PRECIP_PROB])  : null;
      const wind       = (row.length > IDX_WIND_SPEED   && row[IDX_WIND_SPEED]   != null) ? parseFloat(row[IDX_WIND_SPEED])   : null;
      const vis        = (row.length > IDX_VISIBILITY   && row[IDX_VISIBILITY]   != null) ? parseFloat(row[IDX_VISIBILITY])   : null;
      const hasWeather = precipIn != null || precipProb != null || wind != null || vis != null;
      if (hasWeather) {{
        const hasRain = (precipIn != null && precipIn > 0.005) || (precipProb != null && precipProb >= 20);
        if (hasRain) rainCount++; else noRainCount++;
        if (wind != null) {{ if (wind >= 20) windyCount++; else calmCount++; }}
        if (vis  != null) {{ if (vis  <  5)  lowVisCount++; else goodVisCount++; }}
      }}
    }}

    // Render a single labelled bar row
    function barRow(label, count, denominator, maxRate, color, isRate) {{
      const rate = denominator > 0 ? count / denominator : 0;
      const pct  = maxRate > 0 ? Math.min(100, (rate / maxRate) * 100) : 0;
      let valText;
      if (isRate) {{
        if (denominator <= 0 || count === 0) {{
          valText = '<span class="rates-n">0</span>';
        }} else {{
          const rStr = rate >= 0.1 ? rate.toFixed(2) + "/hr" : (rate * 1000).toFixed(1) + "/1000\u202fhr";
          valText = rStr + '<span class="rates-n"> (' + count + ')</span>';
        }}
      }} else {{
        // proportion mode: denominator is the group total
        const p = denominator > 0 ? ((count / denominator) * 100).toFixed(1) + "%" : "\u2014";
        valText = p + '<span class="rates-n"> (' + count + ')</span>';
      }}
      return '<div class="rates-row">'
        + '<span class="rates-label">' + esc(label) + '</span>'
        + '<div class="rates-bar-wrap"><div class="rates-bar-fill" style="width:' + pct.toFixed(1) + '%;background:' + esc(color) + '"></div></div>'
        + '<span class="rates-val">' + valText + '</span>'
        + '</div>';
    }}

    // Relative risk sentence for two rate-based buckets
    function relRisk(countA, hoursA, countB, hoursB, labelA, labelB) {{
      if (hoursA <= 0 || hoursB <= 0) return "";
      const rA = countA / hoursA, rB = countB / hoursB;
      if (rA === 0 && rB === 0) return "";
      if (rB === 0) return labelA + " has incidents but " + labelB + " does not.";
      const ratio = rA / rB;
      if (Math.abs(ratio - 1) < 0.05) return labelA + " and " + labelB + " are about equal per hour.";
      if (ratio > 1) return labelA + " is <strong>" + ratio.toFixed(1) + "&times;</strong> more frequent per hour than " + labelB + ".";
      return labelB + " is <strong>" + (1 / ratio).toFixed(1) + "&times;</strong> more frequent per hour than " + labelA + ".";
    }}

    // ── Rush hour ────────────────────────────────────────────────────────────
    const maxRR = Math.max(rushCount / span.rushHours, nonRushCount / span.nonRushWeekdayHours) || 1e-9;
    const rushHTML =
      barRow("Rush hour",  rushCount,    span.rushHours,             maxRR, "#c53030", true) +
      barRow("Off-peak",   nonRushCount, span.nonRushWeekdayHours,   maxRR, "#2b6cb0", true);
    const rushRatio = relRisk(rushCount, span.rushHours, nonRushCount, span.nonRushWeekdayHours, "Rush hour", "Off-peak");

    // ── School day ───────────────────────────────────────────────────────────
    const maxSR = Math.max(schoolCount / span.schoolDayHours, nonSchoolCount / span.nonSchoolWeekdayHours) || 1e-9;
    const schoolHTML =
      barRow("School day", schoolCount,    span.schoolDayHours,         maxSR, "#c53030", true) +
      barRow("No school",  nonSchoolCount, span.nonSchoolWeekdayHours,  maxSR, "#2b6cb0", true);
    const schoolRatio = relRisk(schoolCount, span.schoolDayHours, nonSchoolCount, span.nonSchoolWeekdayHours, "School days", "No-school days");

    // ── Weather ──────────────────────────────────────────────────────────────
    const wTotal = rainCount + noRainCount;
    let weatherHTML = "";
    if (wTotal === 0) {{
      weatherHTML = '<div class="rates-row"><span class="rates-no-data">No weather data in this selection.</span></div>';
    }} else {{
      const maxW = Math.max(rainCount, noRainCount) || 1;
      weatherHTML +=
        barRow("Rain / precip", rainCount,   wTotal, maxW,   "#2b6cb0", false) +
        barRow("No rain",       noRainCount, wTotal, maxW,   "#276749", false);
      if (windyCount + calmCount > 0) {{
        const wndT = windyCount + calmCount;
        const maxWnd = Math.max(windyCount, calmCount) || 1;
        weatherHTML +=
          barRow("Windy (\u226520 mph)", windyCount, wndT, maxWnd, "#744210", false) +
          barRow("Calm wind",            calmCount,  wndT, maxWnd, "#276749", false);
      }}
      if (lowVisCount + goodVisCount > 0) {{
        const visT = lowVisCount + goodVisCount;
        const maxVis = Math.max(lowVisCount, goodVisCount) || 1;
        weatherHTML +=
          barRow("Low vis (<5\u202fmi)", lowVisCount,  visT, maxVis, "#744210", false) +
          barRow("Good vis",             goodVisCount, visT, maxVis, "#276749", false);
      }}
    }}

    // ── Assemble ─────────────────────────────────────────────────────────────
    el.innerHTML =
      '<div class="rates-subtitle">From ' + rows.length.toLocaleString() + ' filtered incident' + (rows.length !== 1 ? 's' : '') + ' \u2014 denominators are hours in dataset\u2019s date range</div>' +

      '<div class="rates-group">' +
        '<div class="rates-group-title">Rush Hour vs. Off-Peak' +
          ' <span class="rates-span-note">(' + span.rushHours.toLocaleString() + ' rush hrs \u00b7 ' + span.nonRushWeekdayHours.toLocaleString() + ' off-peak hrs in range)</span>' +
        '</div>' +
        rushHTML +
        (rushRatio ? '<div class="rates-ratio">' + rushRatio + '</div>' : '') +
      '</div>' +

      '<div class="rates-group">' +
        '<div class="rates-group-title">School Day vs. No School' +
          ' <span class="rates-span-note">(' + span.schoolDayHours.toLocaleString() + ' school hrs \u00b7 ' + span.nonSchoolWeekdayHours.toLocaleString() + ' no-school hrs)</span>' +
        '</div>' +
        schoolHTML +
        (schoolRatio ? '<div class="rates-ratio">' + schoolRatio + '</div>' : '') +
      '</div>' +

      '<div class="rates-group">' +
        '<div class="rates-group-title">Weather Breakdown' +
          ' <span class="rates-span-note">% of incidents with weather data</span>' +
        '</div>' +
        weatherHTML +
        (wTotal > 0 ? '<div class="rates-ratio">Compare % to local rainfall/wind frequency for relative risk.</div>' : '') +
      '</div>';
  }}

  function inferCorridor(location) {{
    const raw = String(location || "").trim().toUpperCase();
    if (!raw) return "Unknown location";
    const primary = raw.split(/\s+(?:AT|&|\/|NEAR|@)\s+/)[0].trim();
    if (!primary) return "Unknown location";
    return primary.replace(/\s+/g, " ");
  }}

  function renderInsightsPanel(rows) {{
    const el = document.getElementById("insightsContent");
    if (!el) return;
    if (!rows || rows.length === 0) {{
      el.innerHTML = '<span class="rates-no-data">No incidents match the current filters.</span>';
      return;
    }}

    const byHour = Array(24).fill(0);
    const byDow = Array(7).fill(0);
    const byRoadType = new Map();
    const byCorridor = new Map();
    const dated = [];

    for (const row of rows) {{
      const parsed = parseReported(row[IDX_REPORTED]);
      if (parsed && parsed.dt) {{
        if (Number.isInteger(parsed.hh) && parsed.hh >= 0 && parsed.hh < 24) {{
          byHour[parsed.hh] += 1;
        }}
        const jsDow = parsed.dt.getDay();
        if (Number.isInteger(jsDow) && jsDow >= 0 && jsDow < 7) {{
          byDow[jsDow] += 1;
        }}
        dated.push(parsed.dt);
      }}

      const roadType = String(row[IDX_HIGHWAY] || "unknown").trim().toLowerCase() || "unknown";
      byRoadType.set(roadType, (byRoadType.get(roadType) || 0) + 1);

      const corridor = inferCorridor(row[IDX_LOCATION]);
      byCorridor.set(corridor, (byCorridor.get(corridor) || 0) + 1);
    }}

    const bestHour = byHour.reduce((best, count, hour) => (count > best.count ? {{ hour, count }} : best), {{ hour: -1, count: 0 }});
    const dayNames = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
    const bestDow = byDow.reduce((best, count, jsDow) => (count > best.count ? {{ jsDow, count }} : best), {{ jsDow: -1, count: 0 }});

    const topRoadType = Array.from(byRoadType.entries()).sort((a, b) => b[1] - a[1])[0] || ["unknown", 0];
    const topCorridor = Array.from(byCorridor.entries()).sort((a, b) => b[1] - a[1])[0] || ["Unknown location", 0];

    let trendText = "Not enough date coverage for a 7-day trend.";
    if (dated.length >= 4) {{
      dated.sort((a, b) => a - b);
      const end = dated[dated.length - 1];
      const recentStart = new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000);
      const priorStart = new Date(end.getTime() - 14 * 24 * 60 * 60 * 1000);
      let recent = 0;
      let prior = 0;
      for (const dt of dated) {{
        if (dt > recentStart) recent += 1;
        else if (dt > priorStart) prior += 1;
      }}
      if (prior === 0 && recent > 0) {{
        trendText = "Recent 7 days show activity, but the prior 7-day window had none.";
      }} else if (prior > 0) {{
        const pct = ((recent - prior) / prior) * 100;
        const dir = pct >= 0 ? "up" : "down";
        trendText = "Recent 7 days are <strong>" + dir + " " + Math.abs(pct).toFixed(0) + "%</strong> vs the prior 7-day window.";
      }}
    }}

    const hourText = bestHour.hour >= 0
      ? (bestHour.hour.toString().padStart(2, "0") + ":00–" + ((bestHour.hour + 1) % 24).toString().padStart(2, "0") + ":00")
      : "Unknown";
    const roadTypeLabel = topRoadType[0] === "unknown" ? "Unknown / uncategorized" : topRoadType[0];
    const dayLabel = bestDow.jsDow >= 0 ? dayNames[bestDow.jsDow] : "Unknown day";

    el.innerHTML =
      '<ul class="insight-list">' +
        '<li>Peak hour: <strong>' + esc(hourText) + '</strong> (' + bestHour.count + ' incidents).</li>' +
        '<li>Busiest day: <strong>' + esc(dayLabel) + '</strong> (' + bestDow.count + ' incidents).</li>' +
        '<li>Most common road type: <strong>' + esc(roadTypeLabel) + '</strong> (' + topRoadType[1] + ').</li>' +
        '<li>Top corridor: <strong>' + esc(topCorridor[0]) + '</strong> (' + topCorridor[1] + ' incidents).</li>' +
        '<li>' + trendText + '</li>' +
      '</ul>';
  }}

  function currentFilterObj() {{
    const mm = (els.monthSelect.value || "").trim();
    const dd = (els.daySelect.value || "").trim();
    const yy = (els.yearSelect.value || "").trim();
    const dayType = (els.dayTypeSelect.value || "all").trim();
    const timeBlock = (els.timeBlockSelect.value || "all").trim();
    const cause = (els.causeSelect.value || "__ALL__").trim();
    const causeGroup = (els.causeGroupSelect.value || "__ALL__").trim();
    const inViewOnly = !!els.chkInViewOnly.checked;
    const todayOnly = !!els.chkTodayOnly.checked;
    const weatherOnly = !!els.chkWeatherOnly.checked;
    const tempBand = (els.tempBand.value || "any").trim();
    const precipBand = (els.precipBand.value || "any").trim();
    const precipAmountBand = (els.precipAmountBand.value || "any").trim();
    const windBand = (els.windBand.value || "any").trim();
    const visBand = (els.visBand.value || "any").trim();
    const cloudBand = (els.cloudBand.value || "any").trim();
    const rushHour = !!(els.chkRushHour && els.chkRushHour.checked);
    const schoolDay = !!(els.chkSchoolDay && els.chkSchoolDay.checked);
    const dowValue = els.dowSelect ? (els.dowSelect.value || "all").trim() : "all";
    const roadType = els.roadTypeSelect ? (els.roadTypeSelect.value || "any").trim() : "any";
    const roadSearch = (els.roadSearch ? els.roadSearch.value : "").trim();
    const chkFlood = !!(els.chkFloodWarning && els.chkFloodWarning.checked);
    const chkStorm = !!(els.chkThunderstormWarning && els.chkThunderstormWarning.checked);
    const chkTornado = !!(els.chkTornadoWatch && els.chkTornadoWatch.checked);
    return {{
      mm: mm || "",
      dd: dd || "",
      yy: yy || "",
      dayType,
      timeBlock,
      cause,
      causeGroup,
      todayOnly,
      inViewOnly,
      weatherOnly,
      tempBand,
      precipBand,
      precipAmountBand,
      windBand,
      visBand,
      cloudBand,
      rushHour,
      schoolDay,
      dowValue,
      roadType,
      roadSearch,
      chkFlood,
      chkStorm,
      chkTornado,
    }};
  }}

  function filteredIncidents(filterObj, mapObj) {{
    const out = [];
    const bounds = (filterObj.inViewOnly && mapObj) ? mapObj.getBounds() : null;

    for (const row of INCIDENTS) {{
      if (!matchesCauseGroup(row, filterObj.causeGroup)) continue;
      if (!matchesCause(row, filterObj.cause)) continue;
      if (!matchesDateFilter(row, filterObj)) continue;
      if (!matchesDayType(row, filterObj.dayType)) continue;
      if (!matchesTimeBlock(row, filterObj.timeBlock)) continue;
      if (!matchesWeather(row, filterObj)) continue;
      if (!matchesRushHour(row, filterObj.rushHour)) continue;
      if (!matchesSchoolDay(row, filterObj.schoolDay)) continue;
      if (!matchesDow(row, filterObj.dowValue)) continue;
      if (!matchesRoadType(row, filterObj.roadType)) continue;
      if (!matchesRoadSearch(row, filterObj.roadSearch)) continue;
      if (!matchesNwsAlerts(row, filterObj)) continue;

      if (bounds) {{
        const latlng = L.latLng(row[0], row[1]);
        if (!bounds.contains(latlng)) continue;
      }}

      out.push(row);
    }}
    return out;
  }}

  function computeInViewCount(rows, mapObj) {{
    if (!mapObj) return 0;
    const b = mapObj.getBounds();
    let c = 0;
    for (const row of rows) {{
      if (b.contains(L.latLng(row[0], row[1]))) c += 1;
    }}
    return c;
  }}

  let layers = {{
    points: null,
    heat: null,
    intersections: null,
    osmIntersections: null,
    micro: null,
    rings: null,
    hotSpots: null
  }};

  let lastFiltered = [];
  let renderTimer = null;
  let pointMarkers = {{
    singles: [],
    counts: []
  }};

  function updateCurrentWeather(mapObj) {{
    // Show the best available stored weather immediately as a placeholder
    const row = latestWeatherRow();
    const summary = buildWeatherSummary(row);
    if (els.currentWeather) els.currentWeather.innerHTML = summary.html;
    if (els.weatherWidgetBody) els.weatherWidgetBody.innerHTML = summary.html;
    // Kick off an async live fetch from NWS; updates the widget when it arrives
    fetchLiveNWSWeather();
  }}

  // Fetches current conditions from NWS KLFT (Lafayette Regional Airport) station.
  // The NWS public API supports CORS so this works directly from the browser.
  function fetchLiveNWSWeather() {{
    var NWS_OBS_URL = "https://api.weather.gov/stations/KLFT/observations/latest";
    // Subtle dimming while the live fetch is in flight
    if (els.weatherWidgetBody) els.weatherWidgetBody.classList.add("updating");
    fetch(NWS_OBS_URL, {{ headers: {{ "Accept": "application/geo+json" }} }})
      .then(function(resp) {{ return resp.ok ? resp.json() : Promise.reject(resp.status); }})
      .then(function(data) {{
        var props = data && data.properties;
        if (!props) return;

        function toF(c) {{ return (c != null && !isNaN(c)) ? (c * 9 / 5) + 32 : null; }}
        function toMph(kmh) {{ return (kmh != null && !isNaN(kmh)) ? kmh / 1.609344 : null; }}
        function toMi(m) {{ return (m != null && !isNaN(m)) ? m / 1609.344 : null; }}
        function toIn(mm) {{ return (mm != null && !isNaN(mm)) ? mm / 25.4 : null; }}

        var tempF = toF(props.temperature && props.temperature.value);
        var windMph = toMph(props.windSpeed && props.windSpeed.value);
        var gustMph = toMph(props.windGust && props.windGust.value);
        var visMi = toMi(props.visibility && props.visibility.value);
        var precipIn = toIn(props.precipitationLastHour && props.precipitationLastHour.value);

        // Derive sky cover % from cloud layers
        var skyCover = null;
        var coverMap = {{ CLR: 0, SKC: 0, FEW: 15, SCT: 38, BKN: 75, OVC: 100 }};
        var layers = props.cloudLayers || [];
        if (layers.length > 0) {{
          var maxCover = 0;
          for (var i = 0; i < layers.length; i++) {{
            var pct = coverMap[layers[i].amount];
            if (pct != null && pct > maxCover) maxCover = pct;
          }}
          skyCover = maxCover;
        }}

        var parts = [];
        if (tempF != null) parts.push(tempF.toFixed(0) + "\u00b0F");
        if (precipIn != null && precipIn > 0) parts.push(precipIn.toFixed(2) + " in precip");
        if (windMph != null) parts.push(windMph.toFixed(0) + " mph wind");
        if (gustMph != null) parts.push("gust " + gustMph.toFixed(0) + " mph");
        if (visMi != null) parts.push(visMi.toFixed(1) + " mi vis");
        if (skyCover != null) parts.push(skyCover.toFixed(0) + "% clouds");
        if (parts.length === 0) return;

        var obsAt = props.timestamp ? formatCentralTime(props.timestamp) : "";
        var metaLine = "<div class='weather-meta'>"
          + (obsAt ? "Observed " + esc(obsAt) + " \u00b7 " : "")
          + "Source: NWS KLFT</div>";
        var html = "<div class='weather-summary'><div class='weather-main'>"
          + esc(parts.join(" \u00b7 ")) + "</div>" + metaLine + "</div>";

        if (els.currentWeather) els.currentWeather.innerHTML = html;
        if (els.weatherWidgetBody) {{
          els.weatherWidgetBody.classList.remove("updating");
          els.weatherWidgetBody.innerHTML = html;
        }}
      }})
      .catch(function() {{
        // NWS unavailable – stale stored data remains visible; silently ignore
        if (els.weatherWidgetBody) els.weatherWidgetBody.classList.remove("updating");
      }});
  }}

  function scheduleRender(mapObj, delayMs) {{
    const delay = Number.isFinite(delayMs) ? delayMs : 0;
    if (renderTimer) {{
      clearTimeout(renderTimer);
      renderTimer = null;
    }}
    renderTimer = setTimeout(function() {{
      renderTimer = null;
      // Defer the actual DOM work to the next animation frame so the browser
      // can batch the canvas redraws and avoid mid-paint flicker.
      requestAnimationFrame(function() {{ renderAll(mapObj); }});
    }}, delay);
  }}

  function clearLayers(mapObj) {{
    for (const k of Object.keys(layers)) {{
      if (layers[k]) {{
        try {{ mapObj.removeLayer(layers[k]); }} catch (e) {{}}
        layers[k] = null;
      }}
    }}
  }}

  function getPointSizing(mapObj) {{
    const zoom = mapObj && mapObj.getZoom ? mapObj.getZoom() : 12;
    const inverse = 12 - zoom;
    let radius = 5.6 + inverse * 0.7;
    radius = Math.max(3.6, Math.min(10.8, radius));
    if (isTouch) {{
      radius = Math.min(13.5, radius * 1.35 + 1.2);
    }}
    const countSize = Math.max(16, Math.round(radius * 2.05 + (isTouch ? 5 : 3)));
    const countFont = Math.max(10, Math.round(countSize * 0.55));
    return {{ radius, countSize, countFont }};
  }}

  function updatePointSizing(mapObj) {{
    if (!mapObj) return;
    const pointCount = pointMarkers.singles.length + pointMarkers.counts.length;
    // Updating thousands of marker radii/icons on every zoom tick is expensive.
    // Keep existing symbol sizes when the layer is very large to keep zoom/pan responsive.
    if (pointCount > 2000) return;
    const sizing = getPointSizing(mapObj);
    for (const mk of pointMarkers.singles) {{
      if (mk && mk.setRadius) {{
        mk.setRadius(sizing.radius);
      }}
    }}
    for (const mk of pointMarkers.counts) {{
      if (!mk || !mk.setIcon) continue;
      const count = mk.__count || 1;
      const size = sizing.countSize;
      const fontSize = sizing.countFont;
      const icon = L.divIcon({{
        className: "",
        html: "<div class='incident-count-marker' style='width:" + size + "px;height:" + size + "px;line-height:" + size + "px;font-size:" + fontSize + "px;'>" + count + "</div>",
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2]
      }});
      mk.setIcon(icon);
    }}
  }}

  function focusMarker(mapObj, marker) {{
    if (!mapObj || !marker || !marker.getLatLng) return;
    const latlng = marker.getLatLng();
    const currentZoom = mapObj.getZoom ? mapObj.getZoom() : 12;
    const targetZoom = Math.max(currentZoom, 15);
    if (currentZoom >= targetZoom) {{
      // Already at the right zoom — just pan smoothly then show popup
      if (marker.openPopup) marker.openPopup();
      mapObj.panTo(latlng, {{ animate: true, duration: 0.25 }});
    }} else {{
      // Need to zoom in — open the popup once the animation finishes so it
      // lands at the correct screen position.
      mapObj.once("moveend", function() {{
        if (marker.openPopup) marker.openPopup();
      }});
      mapObj.setView(latlng, targetZoom, {{ animate: true, duration: 0.35 }});
    }}
  }}

  function renderAll(mapObj) {{
    const f = currentFilterObj();
    clearLayers(mapObj);
    pointMarkers = {{ singles: [], counts: [] }};

    const showPoints = !!els.chkPoints.checked;
    const showHeat = !!els.chkHeat.checked;
    const showInter = !!els.chkIntersections.checked;
    const showOsmInter = !!els.chkOsmIntersections.checked;
    const showMicro = !!els.chkMicro.checked;
    const showRings = !!els.chkRings.checked;
    const showHotSpots = !!(els.chkHotSpots && els.chkHotSpots.checked);

    const topN = parseInt(els.topNSelect.value || "10", 10);
    const dInter = parseInt(els.precIntersections.value || "3", 10);
    const dMicro = parseInt(els.precMicro.value || "4", 10);

    const filtered = filteredIncidents(f, mapObj);
    lastFiltered = filtered;
    renderRatesPanel(filtered);
    renderInsightsPanel(filtered);

    function setPillCount(el, value) {{
      if (!el) return;
      const next = String(value);
      if (el.textContent !== next) {{
        el.textContent = next;
        // Brief pop animation to signal the count changed
        const pill = el.closest(".pill");
        if (pill) {{
          pill.classList.remove("updated");
          void pill.offsetWidth; // force reflow to restart animation
          pill.classList.add("updated");
        }}
      }}
    }}
    setPillCount(els.countTotal, INCIDENTS.length);
    setPillCount(els.countFiltered, filtered.length);

    const inView = computeInViewCount(filtered, mapObj);
    setPillCount(els.countInView, inView);

    if (showPoints) {{
      const zoom = mapObj && mapObj.getZoom ? mapObj.getZoom() : 12;
      // Adaptive aggregation: when zoomed out, bucket nearby incidents so we
      // render far fewer symbols. This keeps interactions responsive with large datasets.
      const pointsPrecision = zoom <= 10 ? 3 : (zoom <= 12 ? 4 : 6);
      const grouped = (pointsPrecision >= 6)
        ? groupByExactLocation(filtered)
        : (function(rows, decimals) {{
            const byKey = new Map();
            for (const row of rows) {{
              const lat = row[0];
              const lng = row[1];
              const key = lat.toFixed(decimals) + "," + lng.toFixed(decimals);
              let g = byKey.get(key);
              if (!g) {{
                g = {{
                  key: key,
                  lat: parseFloat(lat.toFixed(decimals)),
                  lng: parseFloat(lng.toFixed(decimals)),
                  rows: []
                }};
                byKey.set(key, g);
              }}
              g.rows.push(row);
            }}
            return Array.from(byKey.values());
          }})(filtered, pointsPrecision);
      const sizing = getPointSizing(mapObj);
      const mkList = [];
      for (const group of grouped) {{
        let mk = null;
        if (group.rows.length > 1) {{
          const count = group.rows.length;
          const size = sizing.countSize;
          const fontSize = sizing.countFont;
          const icon = L.divIcon({{
            className: "",
            html: "<div class='incident-count-marker' style='width:" + size + "px;height:" + size + "px;line-height:" + size + "px;font-size:" + fontSize + "px;'>" + count + "</div>",
            iconSize: [size, size],
            iconAnchor: [size / 2, size / 2]
          }});
          mk = L.marker([group.lat, group.lng], {{ icon: icon, riseOnHover: true }});
          mk.__count = count;
          pointMarkers.counts.push(mk);
          // Lazy popup: DOM is only built when the popup first opens
          (function(rows) {{
            mk.bindPopup(function() {{ return createIncidentPopup(rows); }}, {{ maxWidth: 320, autoPan: false }});
          }})(group.rows);
          (function(m) {{ mk.on("click", function() {{ focusMarker(mapObj, m); }}); }})(mk);
        }} else {{
          const radius = sizing.radius;
          mk = L.circleMarker([group.lat, group.lng], {{
            radius: radius,
            renderer: renderer,
            color: "#2b6cb0",
            weight: isTouch ? 2.2 : 1.4,
            fillColor: "#cfe1ff",
            fillOpacity: 0.65
          }});
          pointMarkers.singles.push(mk);
          // Lazy popup: HTML string is only built when the popup first opens
          (function(row) {{
            mk.bindPopup(function() {{ return popupHtml(row); }}, {{ maxWidth: 320, autoPan: false }});
          }})(group.rows[0]);
          (function(m) {{ mk.on("click", function() {{ focusMarker(mapObj, m); }}); }})(mk);
        }}
        mkList.push(mk);
      }}
      // Add all markers to the layer in one shot to minimise map repaints
      const layer = L.layerGroup(mkList).addTo(mapObj);
      layers.points = layer;
    }}

    if (showHeat && typeof L.heatLayer === "function") {{
      const heatPts = filtered.map(r => [r[0], r[1], (r.length >= 7 ? (parseFloat(r[6]) || 1.0) : 1.0)]);
      const heat = L.heatLayer(heatPts, {{ radius: 18, blur: 14, maxZoom: 17 }});
      heat.addTo(mapObj);
      layers.heat = heat;
    }}

    if (showInter) {{
      const groups = groupByRounded(filtered, dInter).slice(0, topN);
      const layer = L.layerGroup().addTo(mapObj);
      for (const g of groups) {{
        const radius = Math.max(7, Math.min(30, 4 + Math.sqrt(g.count) * 3));
        const c = L.circleMarker([g.lat, g.lng], {{ radius: radius, renderer: renderer }});
        c.bindPopup(
          "Rounded intersection<br>Key: " + esc(g.key) + "<br>Count: " + g.count + "<br><br>" + popupHtml(g.sample),
          {{ maxWidth: 340 }}
        );
        c.addTo(layer);
      }}
      layers.intersections = layer;
    }}

    if (showOsmInter) {{
      const layer = L.layerGroup().addTo(mapObj);
      if (!OSM_INTERSECTIONS || OSM_INTERSECTIONS.length === 0) {{
        const center = getCenterFromData();
        const mk = L.circleMarker([center[0], center[1]], {{ radius: 8, renderer: renderer }});
        mk.bindPopup("OSM hotspots unavailable.<br>Install osmnx and rebuild once.");
        mk.addTo(layer);
      }} else {{
        const top = OSM_INTERSECTIONS.slice(0, topN);
        for (const it of top) {{
          const lat = it[0], lng = it[1], cnt = it[2], nodeId = it[3];
          const radius = Math.max(8, Math.min(34, 4 + Math.sqrt(cnt) * 3));
          const c = L.circleMarker([lat, lng], {{ radius: radius, renderer: renderer }});
          c.bindPopup("OSM hotspot<br>Count: " + cnt + "<br>Node: " + esc(nodeId), {{ maxWidth: 320 }});
          c.addTo(layer);
        }}
      }}
      layers.osmIntersections = layer;
    }}

    if (showMicro) {{
      const groups = groupByRounded(filtered, dMicro).slice(0, topN);
      const layer = L.layerGroup().addTo(mapObj);
      for (const g of groups) {{
        const radius = Math.max(6, Math.min(26, 3 + Math.sqrt(g.count) * 2.5));
        const c = L.circleMarker([g.lat, g.lng], {{ radius: radius, renderer: renderer }});
        c.bindPopup(
          "Micro-hotspot<br>Key: " + esc(g.key) + "<br>Count: " + g.count + "<br><br>" + popupHtml(g.sample),
          {{ maxWidth: 340 }}
        );
        c.addTo(layer);
      }}
      layers.micro = layer;
    }}

    const center = getCenterFromData();
    const centerLat = center[0];
    const centerLng = center[1];

    if (showRings) {{
      const ringLayer = L.layerGroup().addTo(mapObj);
      const radiiKm = [1, 2, 3, 5, 8];
      for (const rk of radiiKm) {{
        const circle = L.circle([centerLat, centerLng], {{ radius: rk * 1000, weight: 1, fill: false }});
        circle.addTo(ringLayer);
      }}
      const centerMarker = L.circleMarker([centerLat, centerLng], {{ radius: 7, renderer: renderer }});
      centerMarker.bindPopup("Dataset center<br>" + centerLat.toFixed(5) + ", " + centerLng.toFixed(5));
      centerMarker.addTo(ringLayer);
      layers.rings = ringLayer;
    }}

    if (showHotSpots && HOT_SPOTS.length > 0) {{
      const hsLayer = L.layerGroup().addTo(mapObj);
      const maxScore = HOT_SPOTS[0][3] || 1;
      const sliced = HOT_SPOTS.slice(0, parseInt(els.topNSelect.value || "10", 10));
      for (const hs of sliced) {{
        const hsLat = hs[0], hsLng = hs[1], cnt = hs[2], score = hs[3], label = hs[4];
        const t = Math.max(0, Math.min(1, score / maxScore));
        const r = Math.max(10, Math.min(40, 8 + Math.sqrt(score) * 4));
        // Color: cool (low) → hot (high)
        const red = Math.round(255 * t);
        const blue = Math.round(255 * (1 - t));
        const fillColor = "rgb(" + red + ",60," + blue + ")";
        const c = L.circleMarker([hsLat, hsLng], {{
          radius: r,
          renderer: renderer,
          color: fillColor,
          weight: 2,
          fillColor: fillColor,
          fillOpacity: 0.35
        }});
        c.bindPopup(
          "<b>Hot Spot</b><br>" +
          esc(label || "Unknown location") +
          "<br>Incidents: " + cnt +
          "<br>Recency score: " + score.toFixed(2) +
          "<br><span class='muted'>(score weights recent incidents higher)</span>",
          {{ maxWidth: 320 }}
        );
        c.addTo(hsLayer);
      }}
      layers.hotSpots = hsLayer;
    }}

  }}

  function updateInViewOnly(mapObj) {{
    if (!mapObj) return;
    const f = currentFilterObj();

    let rows = lastFiltered;

    if (f.inViewOnly) {{
      rows = filteredIncidents(f, mapObj);
      lastFiltered = rows;
      if (els.countFiltered) els.countFiltered.textContent = String(rows.length);
    }}

    const inView = computeInViewCount(rows, mapObj);
    if (els.countInView) els.countInView.textContent = String(inView);
  }}

  function clearAll() {{
    els.causeSelect.value = "__ALL__";
    els.causeGroupSelect.value = "__ALL__";
    els.chkInViewOnly.checked = false;

    els.monthSelect.value = "";
    els.daySelect.value = "";
    els.yearSelect.value = "";
    els.chkTodayOnly.checked = false;

    els.dayTypeSelect.value = "all";
    els.timeBlockSelect.value = "all";

    els.chkWeatherOnly.checked = false;
    els.tempBand.value = "any";
    els.precipBand.value = "any";
    els.precipAmountBand.value = "any";
    els.windBand.value = "any";
    els.visBand.value = "any";
    els.cloudBand.value = "any";

    if (els.chkRushHour) els.chkRushHour.checked = false;
    if (els.chkSchoolDay) els.chkSchoolDay.checked = false;
    if (els.dowSelect) els.dowSelect.value = "all";
    if (els.roadTypeSelect) els.roadTypeSelect.value = "any";

    if (els.roadSearch) {{
      els.roadSearch.value = "";
      els.roadSearch.classList.remove("has-value");
      var _rw = els.roadSearch.closest(".road-search-wrap");
      if (_rw) _rw.classList.remove("has-value");
      if (els.roadSearchClear) els.roadSearchClear.classList.remove("visible");
    }}

    if (els.chkFloodWarning) els.chkFloodWarning.checked = false;
    if (els.chkThunderstormWarning) els.chkThunderstormWarning.checked = false;
    if (els.chkTornadoWatch) els.chkTornadoWatch.checked = false;

    els.chkPoints.checked = true;
    els.chkHeat.checked = false;
    els.chkIntersections.checked = false;
    els.chkOsmIntersections.checked = false;
    els.chkMicro.checked = false;
    els.chkRings.checked = false;
    if (els.chkHotSpots) els.chkHotSpots.checked = false;

    els.topNSelect.value = "10";
    els.precIntersections.value = "3";
    els.precMicro.value = "4";
  }}

  function setDateSelectState() {{
    const disabled = !!els.chkTodayOnly.checked;
    if (els.monthSelect) {{
      els.monthSelect.disabled = disabled;
      if (disabled) els.monthSelect.value = "";
    }}
    if (els.daySelect) {{
      els.daySelect.disabled = disabled;
      if (disabled) els.daySelect.value = "";
    }}
    if (els.yearSelect) {{
      els.yearSelect.disabled = disabled;
      if (disabled) els.yearSelect.value = "";
    }}
  }}

  function wireUI(mapObj) {{
    function setBtnText() {{
      if (els.panel.classList.contains("collapsed")) els.toggleBtn.textContent = "Filters";
      else els.toggleBtn.textContent = "Hide";
    }}

    els.toggleBtn.addEventListener("click", function() {{
      els.panel.classList.toggle("collapsed");
      setBtnText();
    }});
    setBtnText();

    els.clearBtn.addEventListener("click", function() {{
      clearAll();
      setDateSelectState();
      scheduleRender(mapObj, 0);
    }});

    const ids = [
      "causeGroupSelect","causeSelect","chkInViewOnly",
      "monthSelect","daySelect","yearSelect",
      "chkTodayOnly",
      "dayTypeSelect","timeBlockSelect",
      "chkRushHour","chkSchoolDay","dowSelect","roadTypeSelect",
      "chkFloodWarning","chkThunderstormWarning","chkTornadoWatch",
      "chkWeatherOnly","tempBand","precipBand","precipAmountBand","windBand","visBand","cloudBand",
      "chkPoints","chkHeat","chkIntersections","chkOsmIntersections","chkMicro","chkRings","chkHotSpots",
      "topNSelect","precIntersections","precMicro"
    ];
    for (const id of ids) {{
      const el = document.getElementById(id);
      if (!el) continue;
      el.addEventListener("change", function() {{
        if (id === "chkTodayOnly") {{
          setDateSelectState();
        }}
        scheduleRender(mapObj, 0);
      }});
    }}

    // Road search: filters on every keystroke, no debounce delay needed
    if (els.roadSearch) {{
      var roadWrap = els.roadSearch.closest(".road-search-wrap");
      els.roadSearch.addEventListener("input", function() {{
        const hasVal = els.roadSearch.value.length > 0;
        els.roadSearch.classList.toggle("has-value", hasVal);
        if (roadWrap) roadWrap.classList.toggle("has-value", hasVal);
        if (els.roadSearchClear) els.roadSearchClear.classList.toggle("visible", hasVal);
        scheduleRender(mapObj, 60);
      }});
      if (els.roadSearchClear) {{
        // Use mousedown + preventDefault so focus stays in the input and the
        // clear always fires even if the pointer drifts slightly after press.
        els.roadSearchClear.addEventListener("mousedown", function(e) {{
          e.preventDefault();
          els.roadSearch.value = "";
          els.roadSearch.classList.remove("has-value");
          if (roadWrap) roadWrap.classList.remove("has-value");
          els.roadSearchClear.classList.remove("visible");
          scheduleRender(mapObj, 0);
        }});
      }}
    }}

    mapObj.on("moveend", function() {{
      if (els.chkInViewOnly.checked) {{
        scheduleRender(mapObj, 80);
      }} else {{
        updateInViewOnly(mapObj);
      }}
    }});

    mapObj.on("zoomend", function() {{
      if (els.chkInViewOnly.checked) {{
        scheduleRender(mapObj, 80);
      }} else {{
        if (els.chkPoints && els.chkPoints.checked) {{
          // Rebuild point layer on zoom so adaptive aggregation can reduce
          // on-screen symbols at low zoom levels for large datasets.
          scheduleRender(mapObj, 40);
        }} else {{
          updatePointSizing(mapObj);
        }}
        updateInViewOnly(mapObj);
      }}
    }});
  }}

  function getMapWhenReady(cb) {{
    let tries = 0;
    const t = setInterval(function() {{
      tries++;
      if (typeof window.{map_var} !== "undefined" && window.{map_var}) {{
        clearInterval(t);
        cb(window.{map_var});
      }}
      if (tries > 200) {{
        clearInterval(t);
      }}
    }}, 50);
  }}

  getMapWhenReady(function(mapObj) {{
    buildCauseDropdown();
    buildCauseGroupDropdown();
    setDateSelectState();
    wireUI(mapObj);
    updateCurrentWeather(mapObj);

    if (els.countTotal) els.countTotal.textContent = String(INCIDENTS.length);
    scheduleRender(mapObj, 0);

    // Show a banner if any incidents are missing geocoded coordinates.
    const unlocatedCount = (typeof window.INCIDENTS_UNLOCATED_COUNT === "number")
      ? window.INCIDENTS_UNLOCATED_COUNT : 0;
    if (unlocatedCount > 0) {{
      const banner = document.getElementById("unlocatedBanner");
      const countEl = document.getElementById("unlocatedCount");
      if (banner && countEl) {{
        countEl.textContent = String(unlocatedCount);
        banner.style.display = "block";
      }}
    }}
  }});
}})();
</script>
"""

    html = html.replace("</body>", f"{inject}\n</body>")

    atomic_write_text(output_map, html)
    _ensure_world_readable(output_map)


def _in_lafayette_bounds(lat, lng):
    try:
        lat = float(lat)
        lng = float(lng)
    except Exception:
        return False
    return (LAF_LAT_MIN <= lat <= LAF_LAT_MAX) and (LAF_LON_MIN <= lng <= LAF_LON_MAX)


def _load_dataframe_from_csv(input_csv: str) -> pd.DataFrame:
    header_cols = []
    try:
        header_cols = list(pd.read_csv(input_csv, nrows=0).columns)
        header_cols = [c.strip() for c in header_cols]
    except Exception:
        header_cols = []

    needed = {
        "location",
        "cause",
        "reported",
        "assisting",
        "incident_number",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "lng",
        "long",
        "x",
        "y",
        "tc_total_count",
        "tc_first_reported",
        "tc_last_reported",
        "weather_temp_f",
        "weather_precip_prob",
        "weather_precip_in",
        "weather_wind_speed_mph",
        "weather_wind_gust_mph",
        "weather_visibility_mi",
        "weather_sky_cover_pct",
        "weather_observed_at",
        "weather_source",
        "road_type",
    }
    if header_cols:
        normalized = {c.strip().lower(): c for c in header_cols}
        matched = [orig for key, orig in normalized.items() if key in needed]
        usecols = matched if matched else None
    else:
        usecols = None

    df = pd.read_csv(
        input_csv,
        usecols=usecols,
        dtype={
            "location": "string",
            "cause": "string",
            "reported": "string",
            "assisting": "string",
            "incident_number": "string",
        },
        engine="c",
    )
    df.columns = [c.strip() for c in df.columns]
    return df


def _load_dataframe_from_db(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT location, cause, reported, assisting, incident_number, latitude, longitude,
                   weather_temp_f, weather_precip_prob, weather_precip_in,
                   weather_wind_speed_mph, weather_wind_gust_mph, weather_visibility_mi,
                   weather_sky_cover_pct, weather_observed_at, weather_source
            FROM incidents
            """,
            conn,
        )
    finally:
        conn.close()
    return df


def create_map_from_csv(input_csv: str, output_map: str, output_datajs: str, osm_cache_dir: str) -> None:
    if not os.path.exists(input_csv):
        _write_text_if_changed(output_datajs, "window.INCIDENTS_DATA=[];window.OSM_INTERSECTIONS_DATA=[];")
        return

    df = _load_dataframe_from_csv(input_csv)
    _create_map_from_dataframe(df, output_map, output_datajs, osm_cache_dir)


def create_map_from_db(db_path: str, output_map: str, output_datajs: str, osm_cache_dir: str) -> None:
    if not os.path.exists(db_path):
        _write_text_if_changed(output_datajs, "window.INCIDENTS_DATA=[];window.OSM_INTERSECTIONS_DATA=[];")
        _write_map_html(30.2241, -92.0198, output_map, output_datajs)
        return
    try:
        center_lat, center_lng, _ = _write_streaming_datajs(db_path, output_datajs, osm_cache_dir)
        _write_map_html(center_lat, center_lng, output_map, output_datajs)
    except Exception:
        _write_text_if_changed(output_datajs, "window.INCIDENTS_DATA=[];window.OSM_INTERSECTIONS_DATA=[];")
        _write_map_html(30.2241, -92.0198, output_map, output_datajs)


def backfill_road_types(db_path: str, osm_cache_dir: str) -> int:
    """Assign OSM road types to every geocoded incident that is missing one.

    Intended to be called once at service startup so historical incidents
    stored before the OSM write-back was introduced get road types immediately,
    rather than waiting for the next render cycle.  Safe to call repeatedly;
    only rows whose road_type actually changes are written.

    Returns the number of rows updated (0 if osmnx is unavailable).
    """
    if not os.path.exists(db_path):
        return 0
    try:
        osm_road_types = _precompute_osm_road_types(db_path, osm_cache_dir)
        return _persist_osm_road_types(db_path, osm_road_types)
    except Exception:
        return 0


def _create_map_from_dataframe(df: pd.DataFrame, output_map: str, output_datajs: str, osm_cache_dir: str) -> None:
    if df.empty:
        _write_text_if_changed(output_datajs, "window.INCIDENTS_DATA=[];window.OSM_INTERSECTIONS_DATA=[];")
        return

    lat_col, lon_col = _find_lat_lon_columns(df)
    if not lat_col or not lon_col:
        _write_text_if_changed(output_datajs, "window.INCIDENTS_DATA=[];window.OSM_INTERSECTIONS_DATA=[];")
        return

    df[lat_col] = pd.to_numeric(df[lat_col].astype(str).str.strip(), errors="coerce")
    df[lon_col] = pd.to_numeric(df[lon_col].astype(str).str.strip(), errors="coerce")
    df = df.dropna(subset=[lat_col, lon_col]).copy()
    if df.empty:
        _write_text_if_changed(output_datajs, "window.INCIDENTS_DATA=[];window.OSM_INTERSECTIONS_DATA=[];")
        return

    for c in ["reported", "location", "cause", "assisting", "incident_number"]:
        if c not in df.columns:
            df[c] = ""

    df = _collapse_traffic_control(df, lat_col, lon_col)

    df_map = df[
        (df[lat_col] >= LAF_LAT_MIN)
        & (df[lat_col] <= LAF_LAT_MAX)
        & (df[lon_col] >= LAF_LON_MIN)
        & (df[lon_col] <= LAF_LON_MAX)
    ].copy()

    incidents = []
    incidents_latlng: List[Tuple[float, float]] = []
    for _, r in df_map.iterrows():
        lat = round(float(r[lat_col]), 6)
        lng = round(float(r[lon_col]), 6)

        reported = str(r.get("reported", "")).strip()
        loc = str(r.get("location", "")).strip()
        cause = str(r.get("cause", "")).strip()
        assist = str(r.get("assisting", "")).strip()

        cause_norm = cause.strip().upper()
        if cause_norm == "TRAFFIC CONTROL":
            weight = 1.0
            total_count = int(pd.to_numeric(r.get("tc_total_count", 1), errors="coerce") or 1)
        else:
            weight = 1.0
            total_count = 1

        temp_f = _safe_float(r.get("weather_temp_f"))
        precip_prob = _safe_float(r.get("weather_precip_prob"))
        precip_in = _safe_float(r.get("weather_precip_in"))
        wind_speed = _safe_float(r.get("weather_wind_speed_mph"))
        wind_gust = _safe_float(r.get("weather_wind_gust_mph"))
        visibility = _safe_float(r.get("weather_visibility_mi"))
        sky_cover = _safe_float(r.get("weather_sky_cover_pct"))
        observed_at = _safe_text(r.get("weather_observed_at"))
        source = _safe_text(r.get("weather_source"))

        incidents.append(
            [
                lat,
                lng,
                reported,
                loc,
                cause,
                assist,
                weight,
                total_count,
                temp_f,
                precip_prob,
                precip_in,
                wind_speed,
                wind_gust,
                visibility,
                sky_cover,
                observed_at,
                source,
                # New enrichment fields — not available from CSV path
                None,  # hour_of_day
                None,  # day_of_week
                None,  # is_school_day
                None,  # nws_flash_flood_warning
                None,  # nws_severe_thunderstorm_warning
                None,  # nws_tornado_watch
                # Road type: use CSV value if present, otherwise infer from location name
                _safe_text(r.get("road_type")) or _infer_road_type(loc) or None,
            ]
        )
        incidents_latlng.append((lat, lng))

    _, _, osm_overall_counts = _compute_osm_context_for_incidents(
        df_map, lat_col, lon_col, incidents_latlng, osm_cache_dir
    )
    osm_intersections = osm_overall_counts

    # Compute hot spots from the dataframe (CSV path)
    hot_spots: List[List] = []
    try:
        spots: Dict[tuple, dict] = {}
        now_dt = datetime.now()
        for _, r in df_map.iterrows():
            lat_v = _safe_float(r.get(lat_col))
            lon_v = _safe_float(r.get(lon_col))
            if lat_v is None or lon_v is None:
                continue
            key = (round(lat_v, 3), round(lon_v, 3))
            if key not in spots:
                spots[key] = {
                    "lat": round(lat_v, 3),
                    "lon": round(lon_v, 3),
                    "count": 0,
                    "hot_score": 0.0,
                    "label": str(r.get("location", "") or "").strip(),
                }
            spots[key]["count"] += 1
            reported_dt = _parse_reported(r.get("reported"))
            if reported_dt is not None:
                try:
                    days_old = max(0.0, (now_dt - reported_dt.replace(tzinfo=None)).total_seconds() / 86400.0)
                    weight = math.exp(-days_old / 30.0)
                except Exception:
                    weight = 0.1
            else:
                weight = 0.1
            spots[key]["hot_score"] += weight
        hot_spots = [
            [s["lat"], s["lon"], s["count"], round(s["hot_score"], 3), s["label"]]
            for s in sorted(spots.values(), key=lambda x: x["hot_score"], reverse=True)
            if s["count"] >= 2
        ][:100]
    except Exception:
        hot_spots = []

    _write_jsonjs_if_changed(output_datajs, incidents, osm_intersections, hot_spots)
    _ensure_world_readable(output_datajs)

    map_dir = os.path.dirname(output_map) or "."
    datajs_dir = os.path.dirname(output_datajs) or "."
    _ensure_world_readable_dir(map_dir)
    _ensure_world_readable_dir(datajs_dir)
    if os.path.abspath(map_dir) != os.path.abspath(datajs_dir):
        map_datajs_path = os.path.join(map_dir, os.path.basename(output_datajs))
        try:
            with open(output_datajs, "r", encoding="utf-8") as handle:
                datajs_text = handle.read()
            _write_text_if_changed(map_datajs_path, datajs_text)
            _ensure_world_readable(map_datajs_path)
        except Exception:
            pass

    center_lat, center_lng = _compute_center(df_map, lat_col, lon_col)
    _write_map_html(center_lat, center_lng, output_map, output_datajs)

    del df
    del df_map
    del incidents
    del incidents_latlng
