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
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from lafayette911.corridors import corridor_ids
from lafayette911.map_template import render_map_html
from lafayette911.utils import atomic_write_text

# Version of the exported traffic_data.js row layout. Bump when a row gains
# fields so the page (and traffic_meta.json consumers) can tell schemas apart.
DATA_SCHEMA_VERSION = 2


def _write_meta_file(output_datajs: str, incident_count: int) -> None:
    """Write traffic_meta.json alongside the data file.

    The web page polls this tiny file before downloading the full incident
    data; the browser only re-fetches the big file when ``data_version``
    (a hash of the data file) changes.  ``generated_at`` is preserved from
    the existing meta when the data is unchanged, so an idle render cycle
    doesn't dirty the file (which would force a pointless Pages publish).
    """
    meta_path = os.path.join(os.path.dirname(output_datajs) or ".", "traffic_meta.json")
    try:
        with open(output_datajs, "rb") as handle:
            data_version = hashlib.sha1(handle.read()).hexdigest()[:16]
    except OSError:
        return
    try:
        with open(meta_path, encoding="utf-8") as handle:
            existing = json.load(handle)
        if existing.get("data_version") == data_version:
            return  # data unchanged; keep the existing meta byte-for-byte
    except Exception:
        pass
    meta = {
        "schema_version": DATA_SCHEMA_VERSION,
        "data_version": data_version,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "incident_count": int(incident_count),
    }
    try:
        atomic_write_text(meta_path, json.dumps(meta, separators=(",", ":")) + "\n")
        _ensure_world_readable(meta_path)
    except Exception:
        pass


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


def _build_incidents_script(
    incidents, osm_intersections, hot_spots=None, unlocated_count: int = 0, unlocated_list=None,
    unmappable_count: int = 0,
) -> str:
    s = "window.INCIDENTS_DATA=" + json.dumps(incidents, ensure_ascii=False, separators=(",", ":"))
    s += ";\nwindow.OSM_INTERSECTIONS_DATA=" + json.dumps(
        osm_intersections, ensure_ascii=False, separators=(",", ":")
    )
    s += ";\nwindow.HOT_SPOTS_DATA=" + json.dumps(
        hot_spots or [], ensure_ascii=False, separators=(",", ":")
    )
    s += f";\nwindow.INCIDENTS_UNLOCATED_COUNT={int(unlocated_count)};"
    s += "\nwindow.INCIDENTS_UNLOCATED_LIST=" + json.dumps(
        unlocated_list or [], ensure_ascii=False, separators=(",", ":")
    )
    s += f";\nwindow.INCIDENTS_UNMAPPABLE_COUNT={int(unmappable_count)};"
    return s


def _write_jsonjs_if_changed(
    path: str, incidents, osm_intersections, hot_spots=None, unlocated_count: int = 0, unlocated_list=None,
    unmappable_count: int = 0,
) -> bool:
    return _write_text_if_changed(
        path,
        _build_incidents_script(
            incidents, osm_intersections, hot_spots, unlocated_count, unlocated_list, unmappable_count
        ),
    )


def _stream_jsonjs_header(handle) -> None:
    handle.write("window.INCIDENTS_DATA=[")


def _stream_jsonjs_incident(handle, incident, first: bool) -> bool:
    if not first:
        handle.write(",")
    handle.write(json.dumps(incident, ensure_ascii=False, separators=(",", ":")))
    return False


def _stream_jsonjs_footer(
    handle, osm_intersections, hot_spots=None, unlocated_count: int = 0, unlocated_list=None,
    unmappable_count: int = 0,
) -> None:
    handle.write("];\nwindow.OSM_INTERSECTIONS_DATA=")
    handle.write(json.dumps(osm_intersections, ensure_ascii=False, separators=(",", ":")))
    handle.write(";\nwindow.HOT_SPOTS_DATA=")
    handle.write(json.dumps(hot_spots or [], ensure_ascii=False, separators=(",", ":")))
    handle.write(f";\nwindow.INCIDENTS_UNLOCATED_COUNT={int(unlocated_count)};")
    handle.write("\nwindow.INCIDENTS_UNLOCATED_LIST=")
    handle.write(json.dumps(unlocated_list or [], ensure_ascii=False, separators=(",", ":")))
    handle.write(f";\nwindow.INCIDENTS_UNMAPPABLE_COUNT={int(unmappable_count)};")


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
    # _safe_text, not `s or ""`: pandas string-dtype columns yield pd.NA for
    # empty cells, and pd.NA raises "boolean value of NA is ambiguous" in `or`.
    return re.sub(r"\s+", " ", _safe_text(s).upper())


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
    # "<na>" is str(pd.NA) — pandas string-dtype columns yield pd.NA for
    # missing cells, and pd.NA cannot be used in boolean context at all.
    if text.lower() in {"nan", "none", "null", "undefined", "<na>"}:
        return ""
    return text


def _safe_int(value):
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


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
    unmappable_count = 0
    unlocated_rows: List[List] = []

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
                SELECT location, cause, reported, assisting, incident_number, latitude, longitude, geocode_attempts,
                       weather_temp_f, weather_precip_prob, weather_precip_in,
                       weather_wind_speed_mph, weather_wind_gust_mph, weather_visibility_mi,
                       weather_sky_cover_pct, weather_observed_at, weather_source,
                       hour_of_day, day_of_week, is_school_day,
                       nws_flash_flood_warning, nws_severe_thunderstorm_warning, nws_tornado_watch,
                       road_type, created_at, is_holiday
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
                geocode_attempts,
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
                is_holiday,
            ) in cursor:
                lat = _safe_float(lat)
                lon = _safe_float(lon)
                if lat is None or lon is None:
                    # Split the queue from the give-ups: incidents whose retry
                    # lifetime is spent (or whose address is blacklisted, which
                    # retires them at max attempts) are "unmappable" and must
                    # not inflate the "locating…" indicator forever.
                    attempts = _safe_int(geocode_attempts) or 0
                    if attempts >= 3:
                        unmappable_count += 1
                    else:
                        unlocated_count += 1
                        unlocated_rows.append([
                            str(location or "").strip(),
                            str(cause or "").strip(),
                            str(reported or "").strip(),
                            str(assisting or "").strip(),
                            _safe_text(created_at),
                        ])
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
                    # holiday flag (index 25)
                    _safe_int(is_holiday),
                    # canonical corridor ids (index 26) — an intersection
                    # legitimately lists both roads
                    corridor_ids(loc),
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
                    None,   # is_holiday
                    corridor_ids(str(entry.get("location") or "")),  # index 26
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
            # Newest first so the feed can surface pending incidents in place;
            # ISO created_at sorts lexicographically. Capped to keep the file small.
            unlocated_rows.sort(key=lambda r: r[4] or "", reverse=True)
            _stream_jsonjs_footer(
                handle, osm_intersections, hot_spots, unlocated_count, unlocated_rows[:50],
                unmappable_count,
            )
    finally:
        conn.close()

    os.replace(tmp_path, output_datajs)
    _ensure_world_readable(output_datajs)
    _write_meta_file(output_datajs, non_tc_count + tc_count)

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
        # The page polls traffic_meta.json from the map's directory too.
        meta_src = os.path.join(datajs_dir, "traffic_meta.json")
        try:
            with open(meta_src, encoding="utf-8") as handle:
                _write_text_if_changed(os.path.join(map_dir, "traffic_meta.json"), handle.read())
        except Exception:
            pass

    rel_datajs = os.path.relpath(output_datajs, map_dir).replace(os.sep, "/")
    if os.path.abspath(map_dir) != os.path.abspath(datajs_dir):
        rel_datajs = os.path.basename(output_datajs)

    html = render_map_html(center_lat, center_lng, rel_datajs)
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
        "hour_of_day",
        "day_of_week",
        "is_school_day",
        "is_holiday",
        "nws_flash_flood_warning",
        "nws_severe_thunderstorm_warning",
        "nws_tornado_watch",
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


def _load_geocode_attempts_map(db_path: str) -> dict:
    """{incident_number: geocode_attempts} for coordless rows, from the DB.

    The CSV archive carries no retry state, so the CSV render path borrows it
    from the working store to tell queued incidents ("locating…") apart from
    permanently-unresolvable ones. Empty dict when the DB is unavailable —
    every coordless row then counts as still locating, the safe default.
    """
    if not db_path or not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT incident_number, geocode_attempts FROM incidents "
                "WHERE latitude IS NULL OR longitude IS NULL"
            ).fetchall()
        finally:
            conn.close()
        return {str(num): (_safe_int(att) or 0) for num, att in rows if num}
    except Exception:
        return {}


def create_map_from_csv(
    input_csv: str, output_map: str, output_datajs: str, osm_cache_dir: str, db_path: str = ""
) -> None:
    if not os.path.exists(input_csv):
        _write_text_if_changed(output_datajs, "window.INCIDENTS_DATA=[];window.OSM_INTERSECTIONS_DATA=[];")
        return

    df = _load_dataframe_from_csv(input_csv)
    attempts_map = _load_geocode_attempts_map(db_path)
    _create_map_from_dataframe(df, output_map, output_datajs, osm_cache_dir, attempts_map)


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


def _create_map_from_dataframe(
    df: pd.DataFrame, output_map: str, output_datajs: str, osm_cache_dir: str,
    attempts_map: Optional[dict] = None,
) -> None:
    if df.empty:
        _write_text_if_changed(output_datajs, "window.INCIDENTS_DATA=[];window.OSM_INTERSECTIONS_DATA=[];")
        return

    lat_col, lon_col = _find_lat_lon_columns(df)
    if not lat_col or not lon_col:
        _write_text_if_changed(output_datajs, "window.INCIDENTS_DATA=[];window.OSM_INTERSECTIONS_DATA=[];")
        return

    df[lat_col] = pd.to_numeric(df[lat_col].astype(str).str.strip(), errors="coerce")
    df[lon_col] = pd.to_numeric(df[lon_col].astype(str).str.strip(), errors="coerce")

    for c in ["reported", "location", "cause", "assisting", "incident_number"]:
        if c not in df.columns:
            df[c] = ""

    # Blank out pandas NA in every text column up front: string-dtype columns
    # yield pd.NA for empty CSV cells, which crashes any `value or ...`
    # truthiness check downstream and stringifies to "<NA>" in output.
    for c in [
        "reported", "location", "cause", "assisting", "incident_number",
        "road_type", "weather_observed_at", "weather_source",
        "tc_first_reported", "tc_last_reported",
    ]:
        if c in df.columns:
            df[c] = df[c].fillna("")

    # Incidents awaiting geocoding must not vanish silently: surface them to
    # the page (feed "locating…" entries + status chip) instead of dropping
    # them on the floor.
    unlocated_mask = df[lat_col].isna() | df[lon_col].isna()
    unlocated_rows: List[List] = []
    unmappable_count = 0
    for _, r in df[unlocated_mask].iterrows():
        # Rows whose retry lifetime is spent are "unmappable": they must not
        # sit in the "locating…" indicator forever. Retry state lives in the
        # DB (attempts_map); without it, everything counts as still locating.
        attempts = (attempts_map or {}).get(_safe_text(r.get("incident_number")), 0)
        if attempts >= 3:
            unmappable_count += 1
            continue
        unlocated_rows.append([
            _safe_text(r.get("location")),
            _safe_text(r.get("cause")),
            _safe_text(r.get("reported")),
            _safe_text(r.get("assisting")),
            "",  # the CSV has no created_at column
        ])
    unlocated_count = len(unlocated_rows)
    unlocated_rows.sort(
        key=lambda r: _parse_reported(r[2]) or datetime.min, reverse=True
    )
    unlocated_rows = unlocated_rows[:50]

    df = df.dropna(subset=[lat_col, lon_col]).copy()
    if df.empty:
        _write_jsonjs_if_changed(output_datajs, [], [], [], unlocated_count, unlocated_rows, unmappable_count)
        return

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
            total_count = int(_safe_float(r.get("tc_total_count")) or 1)
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
                # Enrichment fields (indices 17-22) — the CSV archive has
                # carried these columns since enrichment shipped; hardcoding
                # None here made the NWS/rush-hour filters dead on
                # CSV-rendered deployments.
                _safe_int(r.get("hour_of_day")),
                _safe_int(r.get("day_of_week")),
                _safe_int(r.get("is_school_day")),
                _safe_int(r.get("nws_flash_flood_warning")),
                _safe_int(r.get("nws_severe_thunderstorm_warning")),
                _safe_int(r.get("nws_tornado_watch")),
                # Road type: use CSV value if present, otherwise infer from location name
                _safe_text(r.get("road_type")) or _infer_road_type(loc) or None,
                "",  # created_at (index 24): not tracked in the CSV
                _safe_int(r.get("is_holiday")),  # index 25
                corridor_ids(loc),  # canonical corridor ids (index 26)
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
                    "label": _safe_text(r.get("location")),
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

    _write_jsonjs_if_changed(
        output_datajs, incidents, osm_intersections, hot_spots, unlocated_count, unlocated_rows,
        unmappable_count,
    )
    _ensure_world_readable(output_datajs)
    _write_meta_file(output_datajs, len(incidents))

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
