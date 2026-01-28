import hashlib
import importlib
import importlib.util
import json
import os
import re
import sqlite3
import tempfile
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


def _build_incidents_script(incidents, osm_intersections) -> str:
    s = "window.INCIDENTS_DATA=" + json.dumps(incidents, ensure_ascii=False, separators=(",", ":"))
    s += ";\nwindow.OSM_INTERSECTIONS_DATA=" + json.dumps(
        osm_intersections, ensure_ascii=False, separators=(",", ":")
    )
    s += ";"
    return s


def _write_jsonjs_if_changed(path: str, incidents, osm_intersections) -> bool:
    return _write_text_if_changed(path, _build_incidents_script(incidents, osm_intersections))


def _stream_jsonjs_header(handle) -> None:
    handle.write("window.INCIDENTS_DATA=[")


def _stream_jsonjs_incident(handle, incident, first: bool) -> bool:
    if not first:
        handle.write(",")
    handle.write(json.dumps(incident, ensure_ascii=False, separators=(",", ":")))
    return False


def _stream_jsonjs_footer(handle, osm_intersections) -> None:
    handle.write("];\nwindow.OSM_INTERSECTIONS_DATA=")
    handle.write(json.dumps(osm_intersections, ensure_ascii=False, separators=(",", ":")))
    handle.write(";")


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


def _compute_osm_context_for_incidents(df_all, lat_col, lon_col, incidents_latlng, osm_cache_dir: str):
    if not importlib.util.find_spec("osmnx"):
        return {}, [], []

    ox = importlib.import_module("osmnx")

    if not incidents_latlng:
        return {}, [], []

    os.makedirs(osm_cache_dir, exist_ok=True)

    south, north, west, east = _bbox_from_points(df_all, lat_col, lon_col, pad_deg=OSM_PAD_DEG)
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
        return float(value)
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


def _stream_osm_intersections(
    db_path: str,
    bbox,
    total_points: int,
    osm_cache_dir: str,
    tc_points: List[Tuple[float, float]],
    chunk_size: int = 800,
):
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

    map_dir = os.path.dirname(output_datajs) or "."
    os.makedirs(map_dir, exist_ok=True)
    tmp_dir = os.path.join(map_dir, ".tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=tmp_dir, delete=False) as handle:
        tmp_path = handle.name
        _stream_jsonjs_header(handle)
        first = True
        try:
            cursor = conn.execute(
                """
                SELECT location, cause, reported, assisting, incident_number, latitude, longitude
                FROM incidents
                """
            )
            for location, cause, reported, assisting, incident_number, lat, lon in cursor:
                lat = _safe_float(lat)
                lon = _safe_float(lon)
                if lat is None or lon is None:
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
                incident = [
                    round(float(lat), 6),
                    round(float(lon), 6),
                    reported_str,
                    loc,
                    cause_str,
                    assist,
                    1.0,
                    1,
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
            _stream_jsonjs_footer(handle, osm_intersections)

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

    tmp_map_path = output_map + ".tmp"
    try:
        if os.path.exists(tmp_map_path):
            os.remove(tmp_map_path)
    except Exception:
        pass

    base_map.save(tmp_map_path)

    with open(tmp_map_path, "r", encoding="utf-8") as handle:
        html = handle.read()

    try:
        os.remove(tmp_map_path)
    except Exception:
        pass

    m = re.search(r"var\s+(map_[a-zA-Z0-9_]+)\s*=\s*L\.map\(", html)
    if not m:
        return
    map_var = m.group(1)

    rel_datajs = os.path.relpath(output_datajs, os.path.dirname(output_map) or ".").replace(os.sep, "/")
    if os.path.abspath(map_dir) != os.path.abspath(datajs_dir):
        rel_datajs = os.path.basename(output_datajs)

    html = html.replace(
        "</head>",
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />\n'
        f'<script src="{rel_datajs}"></script>\n'
        f'<script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>\n'
        f"</head>",
    )

    year_options = (
        '<option value="">--</option>'
        '<option value="2024">2024</option>'
        '<option value="2025">2025</option>'
        '<option value="2026">2026</option>'
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
    width: 380px;
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

  #panelHeader h2 {{
    margin: 0;
    font-size: 14px;
    letter-spacing: 0.2px;
  }}

  #panelBody {{
    padding: 10px 12px 12px 12px;
    max-height: calc(82vh - 52px);
    overflow: auto;
  }}

  #panelBody label {{
    display: block;
    font-size: 12px;
    font-weight: 600;
    margin: 8px 0 4px;
    color: #333;
  }}

  #panelBody input,
  #panelBody select {{
    width: 100%;
    padding: 7px 9px;
    border-radius: 8px;
    border: 1px solid rgba(0,0,0,0.15);
    font-size: 12px;
    box-sizing: border-box;
    font-family: var(--font);
    outline: none;
  }}

  #panelBody .row {{
    display: flex;
    gap: 8px;
  }}

  #panelBody .row > * {{
    flex: 1;
  }}

  #panelBody .toggle {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 6px 0;
  }}

  #panelBody .toggle input {{
    width: auto;
  }}

  .panel-actions {{
    display: flex;
    gap: 8px;
    margin-top: 10px;
  }}

  .panel-actions button {{
    flex: 1;
    padding: 8px 10px;
    border-radius: 10px;
    border: none;
    cursor: pointer;
    font-size: 12px;
    font-weight: 600;
  }}

  .panel-actions button.primary {{
    background: #206ad8;
    color: white;
  }}

  .panel-actions button.secondary {{
    background: #e9eef8;
    color: #1a355e;
  }}

  .pill-row {{
    display: flex;
    gap: 6px;
    margin: 8px 0 0 0;
  }}

  .pill {{
    background: #f4f6fb;
    padding: 6px 8px;
    border-radius: 999px;
    font-size: 11px;
    display: flex;
    align-items: center;
    gap: 6px;
  }}

  .pill span:first-child {{
    color: #6c7a90;
  }}

  #panelFooter {{
    padding: 8px 12px 12px;
    font-size: 11px;
    color: #566;
  }}

  #osmSection {{
    margin-top: 10px;
    border-top: 1px dashed rgba(0,0,0,0.08);
    padding-top: 8px;
  }}

  #osmList {{
    list-style: none;
    padding: 0;
    margin: 6px 0 0 0;
    max-height: 140px;
    overflow: auto;
  }}

  #osmList li {{
    display: flex;
    justify-content: space-between;
    padding: 4px 0;
    font-size: 11px;
    border-bottom: 1px solid rgba(0,0,0,0.05);
  }}

  #osmList li span:first-child {{
    color: #4f5d73;
  }}

  @media (max-width: 500px) {{
    #controlPanel {{
      width: 92vw;
      left: 4vw;
      right: 4vw;
    }}
  }}
</style>
<div id="controlPanel">
  <div id="panelHeader">
    <h2>Traffic Incident Filters</h2>
    <button id="togglePanel" class="secondary">Hide</button>
  </div>
  <div id="panelBody">
    <label>Incident filter text</label>
    <input id="filterText" type="text" placeholder="e.g. south college" />
    <div class="row">
      <div>
        <label>Month</label>
        <select id="monthSelect">
          <option value="">--</option>
          <option value="01">January</option>
          <option value="02">February</option>
          <option value="03">March</option>
          <option value="04">April</option>
          <option value="05">May</option>
          <option value="06">June</option>
          <option value="07">July</option>
          <option value="08">August</option>
          <option value="09">September</option>
          <option value="10">October</option>
          <option value="11">November</option>
          <option value="12">December</option>
        </select>
      </div>
      <div>
        <label>Day</label>
        <select id="daySelect">
          <option value="">--</option>
          {"".join([f'<option value="{i:02d}">{i}</option>' for i in range(1, 32)])}
        </select>
      </div>
      <div>
        <label>Year</label>
        <select id="yearSelect">{year_options}</select>
      </div>
    </div>
    <label>Day type</label>
    <select id="dayTypeSelect">
      <option value="">All days</option>
      <option value="weekday">Weekdays</option>
      <option value="weekend">Weekends</option>
    </select>
    <label>Time block:</label>
    <select id="timeBlockSelect">
      <option value="all">All</option>
      <option value="morning">Morning (6a-11a)</option>
      <option value="midday">Midday (11a-3p)</option>
      <option value="afternoon">Afternoon (3p-6p)</option>
      <option value="evening">Evening (6p-10p)</option>
      <option value="night">Night (10p-6a)</option>
    </select>
    <label>Incident cause</label>
    <select id="causeSelect">
      <option value="">All causes</option>
    </select>
    <label>Cause group</label>
    <select id="causeGroupSelect">
      <option value="">All groups</option>
      <option value="collision">Collision / crash</option>
      <option value="traffic">Traffic control</option>
      <option value="vehicle">Vehicle issues</option>
      <option value="road">Road conditions</option>
      <option value="other">Other</option>
    </select>
    <div class="toggle">
      <input id="todayOnlyToggle" type="checkbox" />
      <label for="todayOnlyToggle">Only show incidents reported today</label>
    </div>
    <div class="toggle">
      <input id="inViewOnlyToggle" type="checkbox" />
      <label for="inViewOnlyToggle">Only show incidents in current map view</label>
    </div>
    <div class="panel-actions">
      <button id="clearFilters" class="secondary">Reset</button>
      <button id="applyFilters" class="primary">Apply</button>
    </div>
    <div class="pill-row">
      <div class="pill"><span>Total:</span><span id="countTotal">0</span></div>
      <div class="pill"><span>Filtered:</span><span id="countFiltered">0</span></div>
      <div class="pill"><span>In view:</span><span id="countInView">0</span></div>
    </div>
    <div id="osmSection">
      <label>Most busy intersections</label>
      <ol id="osmList"></ol>
    </div>
  </div>
  <div id="panelFooter">
    Data updates every few minutes. Incidents are aggregated by location for traffic control entries.
  </div>
</div>
<script>
(function() {{
  const INCIDENTS = (window.INCIDENTS_DATA || []).slice();
  const OSM_INTERSECTIONS = (window.OSM_INTERSECTIONS_DATA || []).slice();
  const HEAT_RADIUS = 30;
  const HEAT_BLUR = 20;
  const HEAT_MAX = 1.0;
  let heatLayer = null;

  const els = {{
    filterText: document.getElementById("filterText"),
    monthSelect: document.getElementById("monthSelect"),
    daySelect: document.getElementById("daySelect"),
    yearSelect: document.getElementById("yearSelect"),
    dayTypeSelect: document.getElementById("dayTypeSelect"),
    timeBlockSelect: document.getElementById("timeBlockSelect"),
    causeSelect: document.getElementById("causeSelect"),
    causeGroupSelect: document.getElementById("causeGroupSelect"),
    todayOnlyToggle: document.getElementById("todayOnlyToggle"),
    inViewOnlyToggle: document.getElementById("inViewOnlyToggle"),
    clearFilters: document.getElementById("clearFilters"),
    applyFilters: document.getElementById("applyFilters"),
    countTotal: document.getElementById("countTotal"),
    countFiltered: document.getElementById("countFiltered"),
    countInView: document.getElementById("countInView"),
    osmList: document.getElementById("osmList"),
    togglePanel: document.getElementById("togglePanel"),
    controlPanel: document.getElementById("controlPanel"),
  }};

  function buildCauseDropdown() {{
    const causes = new Set();
    for (const row of INCIDENTS) {{
      const cause = (row[4] || "").trim();
      if (cause) causes.add(cause);
    }}
    const sorted = Array.from(causes).sort();
    for (const c of sorted) {{
      const opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      els.causeSelect.appendChild(opt);
    }}
  }}

  function buildCauseGroupDropdown() {{
    const options = Array.from(els.causeSelect.options).map((o) => o.value);
    const groups = {{
      collision: /crash|collision|accident|wreck|hit/i,
      traffic: /traffic control|flag/i,
      vehicle: /stalled|disabled|vehicle|blocking|abandoned/i,
      road: /road|debris|water|flood|hazard/i,
    }};
    const byGroup = {{
      collision: [],
      traffic: [],
      vehicle: [],
      road: [],
      other: [],
    }};
    for (const cause of options) {{
      if (!cause) continue;
      let assigned = false;
      for (const [group, regex] of Object.entries(groups)) {{
        if (regex.test(cause)) {{
          byGroup[group].push(cause);
          assigned = true;
          break;
        }}
      }}
      if (!assigned) {{
        byGroup.other.push(cause);
      }}
    }}
    for (const group of Object.keys(byGroup)) {{
      byGroup[group] = Array.from(new Set(byGroup[group])).sort();
    }}
    els.causeGroupSelect._groups = byGroup;
  }}

  function setDateSelectState() {{
    const now = new Date();
    const yy = now.getFullYear();
    const mm = String(now.getMonth() + 1).padStart(2, "0");
    const dd = String(now.getDate()).padStart(2, "0");
    if (els.yearSelect) els.yearSelect.value = String(yy);
    if (els.monthSelect) els.monthSelect.value = mm;
    if (els.daySelect) els.daySelect.value = dd;
  }}

  function renderOsmList() {{
    if (!els.osmList) return;
    els.osmList.innerHTML = "";
    const top = OSM_INTERSECTIONS.slice(0, 12);
    for (const row of top) {{
      const li = document.createElement("li");
      const label = document.createElement("span");
      const count = document.createElement("span");
      label.textContent = `#{{row[3] || ""}}`;
      count.textContent = row[2] || 0;
      li.appendChild(label);
      li.appendChild(count);
      els.osmList.appendChild(li);
    }}
  }}

  function updateHeat(mapObj, rows) {{
    if (heatLayer) {{
      mapObj.removeLayer(heatLayer);
      heatLayer = null;
    }}
    if (!rows.length) return;
    const heatData = rows.map((row) => [row[0], row[1], row[6] || 1]);
    heatLayer = L.heatLayer(heatData, {{
      radius: HEAT_RADIUS,
      blur: HEAT_BLUR,
      maxZoom: 16,
      max: HEAT_MAX,
    }});
    heatLayer.addTo(mapObj);
  }}

  function parseReportedDate(s) {{
    if (!s) return null;
    const parts = s.replace("T", " ").replace("Z", "").split(" ");
    const d = parts[0] || "";
    const t = parts[1] || "00:00";
    const dp = d.split("-");
    if (dp.length !== 3) return null;
    const yy = parseInt(dp[0], 10);
    const mm = parseInt(dp[1], 10);
    const dd = parseInt(dp[2], 10);
    const tp = t.split(":");
    const hh = parseInt(tp[0] || "0", 10);
    const mn = parseInt(tp[1] || "0", 10);
    if (isNaN(yy) || isNaN(mm) || isNaN(dd) || isNaN(hh) || isNaN(mn)) return null;
    return new Date(yy, mm - 1, dd, hh, mn, 0, 0);
  }}

  function matchesFilter(row, filterObj, mapObj) {{
    const loc = (row[3] || "").toLowerCase();
    const cause = (row[4] || "").toLowerCase();
    const reported = (row[2] || "").toLowerCase();
    const assist = (row[5] || "").toLowerCase();
    const hay = `${{loc}} ${{cause}} ${{reported}} ${{assist}}`;
    if (filterObj.text && !hay.includes(filterObj.text)) return false;

    if (filterObj.cause && cause !== filterObj.cause.toLowerCase()) return false;
    if (filterObj.causeGroup) {{
      const groupList = (els.causeGroupSelect._groups || {{}})[filterObj.causeGroup] || [];
      if (!groupList.map((x) => x.toLowerCase()).includes(cause)) return false;
    }}

    const pr = parseReportedDate(row[2] || "");
    if (filterObj.yy && (!pr || String(pr.getFullYear()) !== filterObj.yy)) return false;
    if (filterObj.mm && (!pr || String(pr.getMonth() + 1).padStart(2, "0") !== filterObj.mm)) return false;
    if (filterObj.dd && (!pr || String(pr.getDate()).padStart(2, "0") !== filterObj.dd)) return false;

    if (filterObj.todayOnly && pr) {{
      const now = new Date();
      const isToday =
        pr.getFullYear() === now.getFullYear() &&
        pr.getMonth() === now.getMonth() &&
        pr.getDate() === now.getDate();
      if (!isToday) return false;
    }}

    if (filterObj.dayType && pr) {{
      const day = pr.getDay();
      const isWeekend = day === 0 || day === 6;
      if (filterObj.dayType === "weekend" && !isWeekend) return false;
      if (filterObj.dayType === "weekday" && isWeekend) return false;
    }}

    if (filterObj.timeBlock && filterObj.timeBlock !== "all" && pr) {{
      const hh = pr.getHours();
      if (!matchesTimeBlock(row, filterObj.timeBlock, hh)) return false;
    }}

    if (filterObj.inViewOnly && mapObj) {{
      const bounds = mapObj.getBounds();
      if (!bounds.contains([row[0], row[1]])) return false;
    }}

    return true;
  }}

  function timeBlockOf(hh) {{
    if (hh >= 6 && hh < 11) return "morning";
    if (hh >= 11 && hh < 15) return "midday";
    if (hh >= 15 && hh < 18) return "afternoon";
    if (hh >= 18 && hh < 22) return "evening";
    return "night";
  }}

  function matchesTimeBlock(row, block, hh) {{
    if (block === "all") return true;
    const tb = timeBlockOf(hh);
    return tb === block;
  }}

  function collectFilter() {{
    const text = (els.filterText.value || "").toLowerCase().trim();
    const mm = (els.monthSelect.value || "").trim();
    const dd = (els.daySelect.value || "").trim();
    const yy = (els.yearSelect.value || "").trim();
    const dayType = (els.dayTypeSelect.value || "").trim();
    const timeBlock = (els.timeBlockSelect.value || "all").trim();
    const cause = (els.causeSelect.value || "").trim();
    const causeGroup = (els.causeGroupSelect.value || "").trim();
    const todayOnly = !!els.todayOnlyToggle.checked;
    const inViewOnly = !!els.inViewOnlyToggle.checked;
    return {{ mm: mm || "", dd: dd || "", yy: yy || "", dayType, timeBlock, cause, causeGroup, todayOnly, inViewOnly, text }};
  }}

  function applyFilters(mapObj) {{
    const filterObj = collectFilter();
    const out = [];
    for (const row of INCIDENTS) {{
      if (!matchesFilter(row, filterObj, mapObj)) continue;
      out.push(row);
    }}
    if (els.countFiltered) els.countFiltered.textContent = String(out.length);
    renderOsmList();
    updateHeat(mapObj, out);
    if (filterObj.inViewOnly && els.countInView) {{
      els.countInView.textContent = String(out.length);
    }} else if (els.countInView) {{
      els.countInView.textContent = String(out.length);
    }}
  }}

  function resetFilters(mapObj) {{
    els.filterText.value = "";
    els.monthSelect.value = "";
    els.daySelect.value = "";
    els.yearSelect.value = "";
    els.dayTypeSelect.value = "";
    els.timeBlockSelect.value = "all";
    els.causeSelect.value = "";
    els.causeGroupSelect.value = "";
    els.todayOnlyToggle.checked = false;
    els.inViewOnlyToggle.checked = false;
    applyFilters(mapObj);
  }}

  function scheduleRender(mapObj, delayMs) {{
    setTimeout(() => applyFilters(mapObj), delayMs);
  }}

  function wireUI(mapObj) {{
    const selects = [
      "filterText","monthSelect","daySelect","yearSelect","dayTypeSelect","timeBlockSelect",
      "causeSelect","causeGroupSelect","todayOnlyToggle","inViewOnlyToggle"
    ];
    selects.forEach((id) => {{
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("change", function() {{
        applyFilters(mapObj);
      }});
      el.addEventListener("input", function() {{
        if (id === "filterText") applyFilters(mapObj);
      }});
    }});

    if (els.applyFilters) els.applyFilters.addEventListener("click", () => applyFilters(mapObj));
    if (els.clearFilters) els.clearFilters.addEventListener("click", () => resetFilters(mapObj));
    if (els.togglePanel) {{
      els.togglePanel.addEventListener("click", () => {{
        const body = document.getElementById("panelBody");
        if (!body) return;
        const hidden = body.style.display === "none";
        body.style.display = hidden ? "block" : "none";
        els.togglePanel.textContent = hidden ? "Hide" : "Show";
      }});
    }}
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

    if (els.countTotal) els.countTotal.textContent = String(INCIDENTS.length);
    scheduleRender(mapObj, 0);
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
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT location, cause, reported, assisting, incident_number, latitude, longitude
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

        incidents.append([lat, lng, reported, loc, cause, assist, weight, total_count])

    incidents_latlng: List[Tuple[float, float]] = []
    for _, r in df_map.iterrows():
        try:
            incidents_latlng.append((float(r[lat_col]), float(r[lon_col])))
        except Exception:
            incidents_latlng.append((None, None))

    _, _, osm_overall_counts = _compute_osm_context_for_incidents(
        df_map, lat_col, lon_col, incidents_latlng, osm_cache_dir
    )
    osm_intersections = osm_overall_counts

    _write_jsonjs_if_changed(output_datajs, incidents, osm_intersections)
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
