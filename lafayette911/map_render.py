import hashlib
import importlib
import importlib.util
import json
import os
import re
from typing import Dict, List, Tuple

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


def _write_jsonjs_if_changed(path: str, incidents, osm_intersections) -> bool:
    s = "window.INCIDENTS_DATA=" + json.dumps(incidents, ensure_ascii=False, separators=(",", ":"))
    s += ";\nwindow.OSM_INTERSECTIONS_DATA=" + json.dumps(
        osm_intersections, ensure_ascii=False, separators=(",", ":")
    )
    s += ";"
    return _write_text_if_changed(path, s)


def _ensure_world_readable(path: str) -> None:
    try:
        os.chmod(path, 0o644)
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
    usecols = [c for c in header_cols if c in needed] if header_cols else None

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
    df = _load_dataframe_from_db(db_path)
    _create_map_from_dataframe(df, output_map, output_datajs, osm_cache_dir)


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
    script_src = os.path.basename(output_datajs)
    if os.path.abspath(map_dir) != os.path.abspath(datajs_dir):
        map_datajs_path = os.path.join(map_dir, os.path.basename(output_datajs))
        try:
            with open(output_datajs, "r", encoding="utf-8") as handle:
                datajs_text = handle.read()
            _write_text_if_changed(map_datajs_path, datajs_text)
            _ensure_world_readable(map_datajs_path)
            script_src = os.path.basename(map_datajs_path)
        except Exception:
            script_src = os.path.basename(output_datajs)

    center_lat, center_lng = _compute_center(df_map, lat_col, lon_col)

    base_map = folium.Map(location=[center_lat, center_lng], zoom_start=12, control_scale=True)

    tmp_map_path = output_map + ".tmp"
    base_map.save(tmp_map_path)

    with open(tmp_map_path, "r", encoding="utf-8") as handle:
        html = handle.read()

    os.remove(tmp_map_path)

    m = re.search(r"var\s+(map_[a-zA-Z0-9_]+)\s*=\s*L\.map\(", html)
    if not m:
        return
    map_var = m.group(1)

    html = html.replace(
        "</head>",
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />\n'
        f'<script src="{script_src}"></script>\n'
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

  .pill {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(0,0,0,0.05);
    font-size: 12px;
    white-space: nowrap;
  }}

  #panelBody {{
    padding: 10px 12px 12px 12px;
    overflow: hidden;
    max-height: calc(82vh - 140px);
    transition: max-height 0.28s ease, opacity 0.2s ease, transform 0.28s ease;
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
  }}

  input[type="checkbox"] {{
    width: 16px;
    height: 16px;
  }}

  select:disabled {{
    background: #f2f2f2;
    color: rgba(0,0,0,0.45);
    cursor: not-allowed;
  }}

  button {{
    padding: 8px 10px;
    border-radius: 12px;
    border: 1px solid rgba(0,0,0,0.14);
    background: rgba(255,255,255,0.95);
  }}

  button:active {{
    transform: translateY(1px);
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

  @media (max-width: 520px) {{
    #controlPanel {{
      left: 0;
      top: auto;
      bottom: 0;
      width: 100%;
      max-height: 92vh;
      border-radius: 18px 18px 0 0;
      box-shadow: 0 -10px 30px rgba(0,0,0,0.22);
    }}

    #panelBody {{
      max-height: calc(92vh - 120px);
      padding-top: 8px;
    }}

    #mobileHandle {{
      display: block;
      width: 44px;
      height: 5px;
      border-radius: 999px;
      background: rgba(0,0,0,0.18);
      margin: 8px auto 0 auto;
    }}

    #panelHeader {{
      padding-top: 6px;
      flex-direction: column;
      align-items: flex-start;
      gap: 6px;
    }}

    #panelActions {{
      width: 100%;
      display: flex;
      gap: 10px;
    }}

    #panelActions button {{
      flex: 1;
      padding: 12px 10px;
      font-size: 15px;
      border-radius: 14px;
    }}

    select {{
      padding: 10px 12px;
      font-size: 15px;
      border-radius: 12px;
    }}

    input[type="checkbox"] {{
      width: 20px;
      height: 20px;
    }}

    .pill {{
      font-size: 13px;
      padding: 8px 12px;
    }}

    #panelCountBar {{
      padding: 8px 12px 4px 12px;
    }}

    #panelQuickFilters {{
      padding-bottom: 6px;
    }}

    .section {{
      padding: 4px 0;
    }}

    .row {{
      gap: 6px;
      margin-bottom: 6px;
    }}

    .row-grid {{
      gap: 8px;
    }}

    .row-checks {{
      gap: 6px;
    }}

    #controlPanel.collapsed {{
      max-height: 120px;
    }}
  }}

  #mobileHandle {{
    display: none;
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
      <div class="section-title">Filters</div>
      <div class="row row-grid">
        <label>Group:
          <select id="causeGroupSelect">
            <option value="__ALL__">All</option>
          </select>
        </label>

        <label>Type:
          <select id="causeSelect">
            <option value="__ALL__">All</option>
          </select>
        </label>
      </div>

      <div class="row row-grid">
        <label>Month:
          <select id="monthSelect">
            <option value="">--</option>
            <option value="01">Jan</option><option value="02">Feb</option><option value="03">Mar</option>
            <option value="04">Apr</option><option value="05">May</option><option value="06">Jun</option>
            <option value="07">Jul</option><option value="08">Aug</option><option value="09">Sep</option>
            <option value="10">Oct</option><option value="11">Nov</option><option value="12">Dec</option>
          </select>
        </label>

        <label>Day:
          <select id="daySelect">
            <option value="">--</option>
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
        <label>Day type:
          <select id="dayTypeSelect">
            <option value="all">All</option>
            <option value="weekday">Weekdays</option>
            <option value="weekend">Weekends</option>
          </select>
        </label>

        <label>Time block:
          <select id="timeBlockSelect">
            <option value="all">All</option>
            <option value="morning">Morning (06-10)</option>
            <option value="midday">Midday (10-15)</option>
            <option value="evening">Evening (15-19)</option>
            <option value="night">Night (19-24)</option>
            <option value="latenight">Late night (00-06)</option>
          </select>
        </label>
      </div>

      <div class="row row-checks">
        <label><input type="checkbox" id="chkTodayOnly"> Today only</label>
      </div>
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

<script>
(function() {{
  const INCIDENTS = window.INCIDENTS_DATA || [];
  const OSM_INTERSECTIONS = window.OSM_INTERSECTIONS_DATA || [];
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

    chkPoints: document.getElementById("chkPoints"),
    chkHeat: document.getElementById("chkHeat"),
    chkIntersections: document.getElementById("chkIntersections"),
    chkOsmIntersections: document.getElementById("chkOsmIntersections"),
    chkMicro: document.getElementById("chkMicro"),
    chkRings: document.getElementById("chkRings"),

    topNSelect: document.getElementById("topNSelect"),
    precIntersections: document.getElementById("precIntersections"),
    precMicro: document.getElementById("precMicro"),
  }};

  const defaultCenter = [30.2241, -92.0198];
  const map = window.{map_var};

  const layerGroups = {{
    points: L.layerGroup(),
    heat: L.layerGroup(),
    intersections: L.layerGroup(),
    osm: L.layerGroup(),
    micro: L.layerGroup(),
    rings: L.layerGroup(),
  }};

  const byCause = new Map();
  const causeGroups = new Map();

  function parseDateParts(reported) {{
    const match = /^(\d{{4}})-(\d{{2}})-(\d{{2}})\s+(\d{{2}}):(\d{{2}})/.exec(reported || "");
    if (!match) return null;
    return {{
      year: match[1],
      month: match[2],
      day: match[3],
      hour: parseInt(match[4], 10),
      date: new Date(`${{match[1]}}-${{match[2]}}-${{match[3]}}T${{match[4]}}:${{match[5]}}:00`),
    }};
  }}

  function classifyDayType(dateObj) {{
    const day = dateObj.getDay();
    return (day === 0 || day === 6) ? "weekend" : "weekday";
  }}

  function classifyTimeBlock(hour) {{
    if (hour >= 6 && hour < 10) return "morning";
    if (hour >= 10 && hour < 15) return "midday";
    if (hour >= 15 && hour < 19) return "evening";
    if (hour >= 19 && hour < 24) return "night";
    return "latenight";
  }}

  const normalizedIncidents = INCIDENTS.map((item, idx) => {{
    const [lat, lng, reported, location, cause, assisting, weight, totalCount] = item;
    const parsed = parseDateParts(reported);
    const dateObj = parsed ? parsed.date : null;
    const dayType = dateObj ? classifyDayType(dateObj) : "";
    const timeBlock = parsed ? classifyTimeBlock(parsed.hour) : "";

    const causeKey = (cause || "").trim();
    const groupKey = causeKey.split("-")[0].trim();

    if (causeKey) {{
      if (!byCause.has(causeKey)) byCause.set(causeKey, []);
      byCause.get(causeKey).push(idx);
    }}

    if (groupKey) {{
      if (!causeGroups.has(groupKey)) causeGroups.set(groupKey, new Set());
      causeGroups.get(groupKey).add(causeKey);
    }}

    return {{
      lat,
      lng,
      reported,
      location,
      cause,
      assisting,
      weight,
      totalCount: totalCount || 1,
      year: parsed ? parsed.year : "",
      month: parsed ? parsed.month : "",
      day: parsed ? parsed.day : "",
      dayType,
      timeBlock,
    }};
  }});

  function populateSelect(select, values) {{
    values.forEach((val) => {{
      const option = document.createElement("option");
      option.value = val;
      option.textContent = val;
      select.appendChild(option);
    }});
  }}

  populateSelect(els.causeGroupSelect, Array.from(causeGroups.keys()).sort());

  function updateCauseSelect() {{
    const group = els.causeGroupSelect.value;
    els.causeSelect.innerHTML = '<option value="__ALL__">All</option>';
    if (group === "__ALL__") {{
      populateSelect(els.causeSelect, Array.from(byCause.keys()).sort());
      return;
    }}
    const causes = Array.from(causeGroups.get(group) || []);
    populateSelect(els.causeSelect, causes.sort());
  }}

  updateCauseSelect();

  function getFilters() {{
    return {{
      group: els.causeGroupSelect.value,
      cause: els.causeSelect.value,
      month: els.monthSelect.value,
      day: els.daySelect.value,
      year: els.yearSelect.value,
      dayType: els.dayTypeSelect.value,
      timeBlock: els.timeBlockSelect.value,
      todayOnly: els.chkTodayOnly.checked,
      inViewOnly: els.chkInViewOnly.checked,
    }};
  }}

  function matchFilters(incident, filters) {{
    if (filters.group !== "__ALL__" && !incident.cause.startsWith(filters.group)) return false;
    if (filters.cause !== "__ALL__" && incident.cause !== filters.cause) return false;
    if (filters.month && incident.month !== filters.month) return false;
    if (filters.day && incident.day !== filters.day) return false;
    if (filters.year && incident.year !== filters.year) return false;
    if (filters.dayType !== "all" && incident.dayType !== filters.dayType) return false;
    if (filters.timeBlock !== "all" && incident.timeBlock !== filters.timeBlock) return false;

    if (filters.todayOnly) {{
      const today = new Date();
      const y = String(today.getFullYear());
      const m = String(today.getMonth() + 1).padStart(2, "0");
      const d = String(today.getDate()).padStart(2, "0");
      if (incident.year !== y || incident.month !== m || incident.day !== d) return false;
    }}

    return true;
  }}

  function clearLayers() {{
    Object.values(layerGroups).forEach((layer) => layer.clearLayers());
  }}

  function renderLayers(filtered) {{
    clearLayers();

    if (els.chkPoints.checked) {{
      filtered.forEach((incident) => {{
        const marker = L.circleMarker([incident.lat, incident.lng], {{
          radius: 4,
          fillColor: "#1f77b4",
          color: "#1f77b4",
          weight: 1,
          fillOpacity: 0.8,
          renderer,
        }}).bindPopup(`
          <div>
            <div><strong>${{incident.cause}}</strong></div>
            <div>${{incident.location}}</div>
            <div>${{incident.reported}}</div>
            <div>${{incident.assisting}}</div>
          </div>
        `);
        layerGroups.points.addLayer(marker);
      }});
    }}

    if (els.chkHeat.checked) {{
      const heatPoints = filtered.map((incident) => [incident.lat, incident.lng, incident.weight || 1]);
      const heatLayer = L.heatLayer(heatPoints, {{ radius: 25, blur: 18, maxZoom: 14 }});
      layerGroups.heat.addLayer(heatLayer);
    }}

    const precision = parseInt(els.precIntersections.value, 10);
    const microPrecision = parseInt(els.precMicro.value, 10);

    if (els.chkIntersections.checked) {{
      const rounded = new Map();
      filtered.forEach((incident) => {{
        const key = `${{incident.lat.toFixed(precision)}},${{incident.lng.toFixed(precision)}}`;
        rounded.set(key, (rounded.get(key) || 0) + (incident.totalCount || 1));
      }});

      rounded.forEach((count, key) => {{
        const [lat, lng] = key.split(",").map(Number);
        const marker = L.circleMarker([lat, lng], {{
          radius: 12,
          fillColor: "#ff7f0e",
          color: "#c05621",
          weight: 2,
          fillOpacity: 0.65,
          renderer,
        }}).bindTooltip(`Count: ${{count}}`, {{ permanent: false }});
        layerGroups.intersections.addLayer(marker);
      }});
    }}

    if (els.chkMicro.checked) {{
      const micro = new Map();
      filtered.forEach((incident) => {{
        const key = `${{incident.lat.toFixed(microPrecision)}},${{incident.lng.toFixed(microPrecision)}}`;
        micro.set(key, (micro.get(key) || 0) + (incident.totalCount || 1));
      }});

      micro.forEach((count, key) => {{
        const [lat, lng] = key.split(",").map(Number);
        const marker = L.circleMarker([lat, lng], {{
          radius: 6,
          fillColor: "#2f855a",
          color: "#22543d",
          weight: 1,
          fillOpacity: 0.65,
          renderer,
        }}).bindTooltip(`Count: ${{count}}`, {{ permanent: false }});
        layerGroups.micro.addLayer(marker);
      }});
    }}

    if (els.chkRings.checked) {{
      const rings = new Map();
      filtered.forEach((incident) => {{
        const key = `${{incident.lat.toFixed(precision)}},${{incident.lng.toFixed(precision)}}`;
        rings.set(key, (rings.get(key) || 0) + (incident.totalCount || 1));
      }});

      rings.forEach((count, key) => {{
        const [lat, lng] = key.split(",").map(Number);
        const marker = L.circle([lat, lng], {{
          radius: 30 + count * 5,
          fillColor: "#805ad5",
          color: "#6b46c1",
          weight: 2,
          fillOpacity: 0.25,
          renderer,
        }}).bindTooltip(`Count: ${{count}}`, {{ permanent: false }});
        layerGroups.rings.addLayer(marker);
      }});
    }}

    if (els.chkOsmIntersections.checked && OSM_INTERSECTIONS.length) {{
      const topN = parseInt(els.topNSelect.value, 10);
      OSM_INTERSECTIONS.slice(0, topN).forEach((item) => {{
        const [lat, lng, count] = item;
        const marker = L.circleMarker([lat, lng], {{
          radius: 16,
          fillColor: "#2b6cb0",
          color: "#2c5282",
          weight: 2,
          fillOpacity: 0.7,
          renderer,
        }}).bindTooltip(`Count: ${{count}}`, {{ permanent: false }});
        layerGroups.osm.addLayer(marker);
      }});
    }}

    Object.values(layerGroups).forEach((layer) => {{
      if (!map.hasLayer(layer)) {{
        map.addLayer(layer);
      }}
    }});
  }}

  function applyFilters() {{
    const filters = getFilters();
    let filtered = normalizedIncidents.filter((incident) => matchFilters(incident, filters));

    if (filters.inViewOnly) {{
      const bounds = map.getBounds();
      filtered = filtered.filter((incident) => bounds.contains([incident.lat, incident.lng]));
    }}

    els.countTotal.textContent = normalizedIncidents.length;
    els.countFiltered.textContent = filtered.length;

    const bounds = map.getBounds();
    const inView = filtered.filter((incident) => bounds.contains([incident.lat, incident.lng])).length;
    els.countInView.textContent = inView;

    renderLayers(filtered);
  }}

  function clearFilters() {{
    els.causeGroupSelect.value = "__ALL__";
    updateCauseSelect();
    els.causeSelect.value = "__ALL__";
    els.monthSelect.value = "";
    els.daySelect.value = "";
    els.yearSelect.value = "";
    els.dayTypeSelect.value = "all";
    els.timeBlockSelect.value = "all";
    els.chkTodayOnly.checked = false;
    els.chkInViewOnly.checked = false;
    applyFilters();
  }}

  els.causeGroupSelect.addEventListener("change", () => {{
    updateCauseSelect();
    applyFilters();
  }});
  els.causeSelect.addEventListener("change", applyFilters);
  els.monthSelect.addEventListener("change", applyFilters);
  els.daySelect.addEventListener("change", applyFilters);
  els.yearSelect.addEventListener("change", applyFilters);
  els.dayTypeSelect.addEventListener("change", applyFilters);
  els.timeBlockSelect.addEventListener("change", applyFilters);
  els.chkTodayOnly.addEventListener("change", applyFilters);
  els.chkInViewOnly.addEventListener("change", applyFilters);
  els.chkPoints.addEventListener("change", applyFilters);
  els.chkHeat.addEventListener("change", applyFilters);
  els.chkIntersections.addEventListener("change", applyFilters);
  els.chkOsmIntersections.addEventListener("change", applyFilters);
  els.chkMicro.addEventListener("change", applyFilters);
  els.chkRings.addEventListener("change", applyFilters);
  els.topNSelect.addEventListener("change", applyFilters);
  els.precIntersections.addEventListener("change", applyFilters);
  els.precMicro.addEventListener("change", applyFilters);

  els.clearBtn.addEventListener("click", clearFilters);

  els.toggleBtn.addEventListener("click", () => {{
    els.panel.classList.toggle("collapsed");
  }});

  map.setView(defaultCenter, 12);

  map.on("moveend", applyFilters);
  applyFilters();
}})();
</script>
"""

    html = html.replace("</body>", f"{inject}\n</body>")

    atomic_write_text(output_map, html)

    del df
    del df_map
    del incidents
    del incidents_latlng
