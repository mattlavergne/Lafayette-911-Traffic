import os
import re
import time
import json
import math
import csv
import hashlib
import sqlite3
from datetime import datetime

import requests
import pandas as pd
from bs4 import BeautifulSoup
import folium

# ========= CONFIG =========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FILENAME = os.path.join(BASE_DIR, "traffic_incidents.csv")
MAPNAME = os.path.join(BASE_DIR, "traffic_map.html")
DATAJS = os.path.join(BASE_DIR, "traffic_data.js")
INDEX_DB = os.path.join(BASE_DIR, "incident_index.sqlite")

# Cache for OpenStreetMap road graph + derived intersections
OSM_CACHE_DIR = os.path.join(BASE_DIR, "osm_cache")

DEFAULT_FULL_CITY = "Lafayette, Louisiana, USA"
SLEEP_SECONDS = 300

# SECURITY NOTE:
# Do not hardcode API keys in source.
# Set an environment variable instead:
#   Windows (PowerShell):  $env:GOOGLE_API_KEY="YOUR_KEY"
#   macOS/Linux:          export GOOGLE_API_KEY="YOUR_KEY"
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
# ==========================

# Lafayette-only safety bounds (used for map sanity, OSM bbox, and incident acceptance)
LAF_LAT_MIN = 29.50
LAF_LAT_MAX = 31.00
LAF_LON_MIN = -92.25
LAF_LON_MAX = -91.90

# OSM snapping config
OSM_PAD_DEG = 0.02
OSM_INTERSECTION_MIN_STREETS = 3

LAFAYETTE_PARISH_PLACES = {
    "Lafayette",
    "Lafayette Parish",
    "Carencro",
    "Broussard",
    "Youngsville",
    "Scott",
    "Duson",
    "Milton",
}


def _file_mtime_int(path: str) -> int:
    try:
        return int(os.path.getmtime(path))
    except Exception:
        return 0


def _write_text_if_changed(path: str, text: str) -> bool:
    data = text.encode("utf-8")
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                old = f.read()
            if old == data:
                return False

        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return True
    except Exception:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return True


def _write_jsonjs_if_changed(path: str, incidents, osm_intersections) -> bool:
    s = "window.INCIDENTS_DATA=" + json.dumps(incidents, ensure_ascii=False, separators=(",", ":"))
    s += ";\nwindow.OSM_INTERSECTIONS_DATA=" + json.dumps(osm_intersections, ensure_ascii=False, separators=(",", ":"))
    s += ";"
    return _write_text_if_changed(path, s)


def _seed_index_from_csv(conn, filename):
    if not os.path.exists(filename):
        return

    try:
        with open(filename, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return
            try:
                idx = [h.strip() for h in header].index("incident_number")
            except ValueError:
                return

            batch = []
            for row in reader:
                if idx >= len(row):
                    continue
                incident = row[idx].strip()
                if not incident:
                    continue
                batch.append((incident,))
                if len(batch) >= 1000:
                    conn.executemany(
                        "INSERT OR IGNORE INTO incident_index (incident_number) VALUES (?)",
                        batch,
                    )
                    batch = []

            if batch:
                conn.executemany(
                    "INSERT OR IGNORE INTO incident_index (incident_number) VALUES (?)",
                    batch,
                )
        conn.commit()
    except Exception as e:
        print(f"Warning: failed seeding incident index: {e}")


def _ensure_incident_index(db_path, csv_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS incident_index (incident_number TEXT PRIMARY KEY)"
    )
    conn.commit()
    try:
        count = conn.execute("SELECT COUNT(1) FROM incident_index").fetchone()[0]
    except Exception:
        count = 0
    if count == 0:
        _seed_index_from_csv(conn, csv_path)
    return conn


def _filter_new_incidents(incidents, conn, batch_size=900):
    if incidents is None or incidents.empty:
        return incidents

    ids = incidents["incident_number"].astype(str).fillna("").tolist()
    existing = set()

    for i in range(0, len(ids), batch_size):
        chunk = ids[i : i + batch_size]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        query = f"SELECT incident_number FROM incident_index WHERE incident_number IN ({placeholders})"
        try:
            rows = conn.execute(query, chunk).fetchall()
            existing.update(r[0] for r in rows if r and r[0] is not None)
        except Exception as e:
            print(f"Warning: incident index lookup failed: {e}")
            return incidents

    return incidents[~incidents["incident_number"].astype(str).isin(existing)].copy()



def _in_lafayette_bounds(lat, lng):
    try:
        lat = float(lat)
        lng = float(lng)
    except Exception:
        return False
    return (LAF_LAT_MIN <= lat <= LAF_LAT_MAX) and (LAF_LON_MIN <= lng <= LAF_LON_MAX)


def _normalize_location(s: str) -> str:
    s = str(s or "").strip().upper()
    s = re.sub(r"\s+", " ", s)
    return s


def _has_allowed_lafayette_place(address_components) -> bool:
    if not address_components:
        return False
    allowed = {name.strip().casefold() for name in LAFAYETTE_PARISH_PLACES}
    for comp in address_components:
        long_name = (comp.get("long_name") or "").strip().casefold()
        if long_name in allowed:
            return True
    return False


def fetch_traffic_data():
    url = "https://lafayette911.org/wp-json/traffic-feed/v1/data"
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            return response.json()
        print(f"Failed to fetch data: {response.status_code}")
    except requests.RequestException as e:
        print(f"Error fetching data: {e}")
    return None


def parse_traffic_data(json_data):
    if not (json_data and json_data.get("success")):
        return pd.DataFrame()

    html_data = json_data.get("data", "")
    soup = BeautifulSoup(html_data, "html.parser")
    table_rows = soup.select("table tr")[1:]  # skip header

    data = []
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

        data.append(
            {
                "location": location,
                "cause": cause,
                "reported": reported,
                "assisting": assisting,
                "incident_number": incident_number,
            }
        )

    return pd.DataFrame(data)


def geocode_with_google(address, api_key):
    if not api_key:
        print("GOOGLE_API_KEY is not set. Skipping geocoding.")
        return None, None

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": api_key}

    try:
        response = requests.get(url, params=params, timeout=30)
        if response.status_code != 200:
            print(f"HTTP Error {response.status_code} for address: {address}")
            return None, None

        geocode_data = response.json()
        if geocode_data.get("status") != "OK" or not geocode_data.get("results"):
            print(f"Google Geocoding failed for {address}: {geocode_data.get('status')}")
            return None, None

        result = geocode_data["results"][0]

        # Enforce Lafayette Parish using address components (best-effort)
        comps = result.get("address_components", []) or []
        if not _has_allowed_lafayette_place(comps):
            print(f"Geocode rejected (not Lafayette Parish): {address}")
            return None, None

        loc = result["geometry"]["location"]
        lat = loc.get("lat")
        lng = loc.get("lng")

        if lat is None or lng is None:
            return None, None

        # Hard clamp to Lafayette bounds as final gate
        if not _in_lafayette_bounds(lat, lng):
            print(f"Geocode rejected (outside Lafayette bounds): {address} => {lat},{lng}")
            return None, None

        return lat, lng

    except requests.RequestException as e:
        print(f"Request exception: {e} for address: {address}")
        return None, None


def geocode_new_incidents(df, google_api_key):
    if df.empty or "location" not in df.columns:
        print("No data or 'location' column not found.")
        return df

    df = df.copy()

    if "latitude" not in df.columns:
        df["latitude"] = None
    if "longitude" not in df.columns:
        df["longitude"] = None

    print(f"Geocoding {len(df)} new incidents...")
    for idx, row in df.iterrows():
        if pd.notnull(row.get("latitude")) and pd.notnull(row.get("longitude")):
            continue

        address = f"{row['location']}, {DEFAULT_FULL_CITY}"
        lat, lng = geocode_with_google(address, google_api_key)
        if lat is None or lng is None:
            print(f"Skipping {address} due to failed geocoding.")
            continue

        df.at[idx, "latitude"] = lat
        df.at[idx, "longitude"] = lng

    print("Geocoding completed.")
    return df


def ensure_csv_exists(filename):
    if os.path.exists(filename):
        return
    cols = ["location", "cause", "reported", "assisting", "incident_number", "latitude", "longitude"]
    pd.DataFrame(columns=cols).to_csv(filename, index=False)


def _tail_lines(path, max_lines=5000, chunk_size=65536):
    """
    Return last up to max_lines lines from a text file, efficiently.
    """
    if max_lines <= 0:
        return []

    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            if end == 0:
                return []

            buf = b""
            pos = end
            lines_found = 0

            while pos > 0 and lines_found <= max_lines:
                read_size = chunk_size if pos >= chunk_size else pos
                pos -= read_size
                f.seek(pos, os.SEEK_SET)
                chunk = f.read(read_size)
                buf = chunk + buf
                lines_found = buf.count(b"\n")

            lines = buf.splitlines()[-max_lines:]
            return [x.decode("utf-8", errors="replace") for x in lines]
    except Exception as e:
        print(f"Warning: _tail_lines failed: {e}")
        return []


def _tail_incident_numbers_csv(filename, max_lines=5000):
    """
    Best-effort: parse incident_number values from the tail of the CSV
    to catch duplicates without reading the whole file.

    Assumes CSV columns include 'incident_number'. Handles quoted CSV rows
    in a basic way but does not attempt full RFC CSV parsing.
    """
    if not os.path.exists(filename):
        return set()

    lines = _tail_lines(filename, max_lines=max_lines)
    if not lines:
        return set()

    header_idx = None
    header = None
    for i, line in enumerate(lines):
        if "incident_number" in line:
            header_idx = i
            header = line
            break

    if header_idx is None or header is None:
        return set()

    try:
        cols = [c.strip().strip('"') for c in header.split(",")]
        if "incident_number" not in cols:
            return set()
        inc_i = cols.index("incident_number")
    except Exception:
        return set()

    out = set()

    # Parse data rows after the header line found in the tail
    for line in lines[header_idx + 1 :]:
        if not line.strip():
            continue

        # Minimal CSV split that respects simple quotes:
        # If your incident_number can contain commas inside quotes, this still works
        # in most cases. If you ever see misses, switch to full csv.reader over tail lines.
        parts = []
        cur = []
        in_quotes = False
        for ch in line:
            if ch == '"':
                in_quotes = not in_quotes
                cur.append(ch)
                continue
            if ch == "," and not in_quotes:
                parts.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        parts.append("".join(cur))

        if inc_i >= len(parts):
            continue

        v = parts[inc_i].strip()
        if v.startswith('"') and v.endswith('"') and len(v) >= 2:
            v = v[1:-1]
        v = v.strip()
        if v:
            out.add(v)

    return out


def save_new_to_csv(new_rows, filename=FILENAME, tail_dedupe_lines=5000):
    """
    Append-only writer with lightweight tail dedupe.

    - Does NOT rewrite the full file.
    - Does NOT require caller filtering to be perfect.
    - Tail dedupe prevents most duplicate appends with low I/O cost.

    tail_dedupe_lines:
      - 0 disables tail dedupe.
      - Typical: 2000-10000. Use larger if bursts can exceed this between restarts.
    """
    if new_rows is None or new_rows.empty:
        print("No new data to save.")
        return True

    try:
        ensure_csv_exists(filename)

        d = new_rows.copy()

        # Ensure required columns exist in appended chunk
        col_order = ["location", "cause", "reported", "assisting", "incident_number", "latitude", "longitude"]
        for c in col_order:
            if c not in d.columns:
                d[c] = None
        d = d[col_order]

        # Tail dedupe: filter out incident_numbers already present near the end of the file
        if tail_dedupe_lines and tail_dedupe_lines > 0:
            tail_ids = _tail_incident_numbers_csv(filename, max_lines=tail_dedupe_lines)
            if tail_ids:
                before = len(d)
                d = d[~d["incident_number"].astype(str).isin(tail_ids)].copy()
                dropped = before - len(d)
                if dropped > 0:
                    print(f"Tail dedupe: dropped {dropped} duplicate rows (last {tail_dedupe_lines} lines).")

        if d.empty:
            print("All candidate rows were duplicates (tail dedupe).")
            return True

        # Append, header only if file empty
        try:
            write_header = (os.path.getsize(filename) == 0)
        except Exception:
            write_header = True

        d.to_csv(
            filename,
            mode="a",
            header=write_header,
            index=False,
        )

        print(f"Appended {len(d)} rows to {filename} at {datetime.now()}")
        return True

    except Exception as e:
        print(f"Error appending data: {e}")
        return False


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

    return (lat_min, lat_max, lon_min, lon_max)  # (south, north, west, east)


def _hash_bbox(bbox):
    s = f"{bbox[0]:.5f}_{bbox[1]:.5f}_{bbox[2]:.5f}_{bbox[3]:.5f}"
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _osm_cache_paths(bbox_id, n_points):
    graphml_path = os.path.join(OSM_CACHE_DIR, f"drive_{bbox_id}.graphml")
    cache_json = os.path.join(OSM_CACHE_DIR, f"osmctx_{bbox_id}_{n_points}.json")
    return graphml_path, cache_json


def _compute_osm_context_for_incidents(df_all, lat_col, lon_col, incidents_latlng):
    """
    Returns:
      node_info: dict(node_id -> [lat, lng])
      point_nodes: list aligned with incidents_latlng, node_id string or "" if none
      overall_counts: list [[lat,lng,count,node_id], ...] sorted desc
    """
    try:
        import osmnx as ox
    except Exception as e:
        print(f"OSM: osmnx import failed: {e}")
        return {}, [], []

    if not incidents_latlng:
        print("OSM: no incidents to process")
        return {}, [], []

    os.makedirs(OSM_CACHE_DIR, exist_ok=True)

    south, north, west, east = _bbox_from_points(df_all, lat_col, lon_col, pad_deg=OSM_PAD_DEG)
    bbox_id = _hash_bbox((south, north, west, east))

    n_points = len(incidents_latlng)
    graphml_path, cache_json = _osm_cache_paths(bbox_id, n_points)

    if os.path.exists(cache_json):
        try:
            with open(cache_json, "r", encoding="utf-8") as f:
                cached = json.load(f)
            node_info = cached.get("node_info", {}) or {}
            point_nodes = cached.get("point_nodes", []) or []
            overall_counts = cached.get("overall_counts", []) or []
            if len(point_nodes) == n_points:
                print(f"OSM: loaded cached context: nodes={len(node_info)} point_nodes={len(point_nodes)}")
                return node_info, point_nodes, overall_counts
        except Exception as e:
            print(f"OSM: cache read failed: {e}")

    try:
        if os.path.exists(graphml_path):
            G = ox.load_graphml(graphml_path)
            print(f"OSM: loaded graphml {os.path.basename(graphml_path)}")
        else:
            print(f"OSM: downloading graph bbox N{north:.5f} S{south:.5f} E{east:.5f} W{west:.5f}")
            # OSMnx 2.x expects bbox=(left,bottom,right,top) == (west,south,east,north)
            G = ox.graph_from_bbox(bbox=(west, south, east, north), network_type="drive", simplify=True)
            ox.save_graphml(G, graphml_path)
            print(f"OSM: saved graphml {os.path.basename(graphml_path)}")
    except Exception as e:
        print(f"OSM: graph load/build failed: {e}")
        return {}, [""] * n_points, []

    try:
        sc = ox.stats.count_streets_per_node(G)
        for n, v in sc.items():
            try:
                G.nodes[n]["street_count"] = int(v)
            except Exception:
                pass
        print(f"OSM: street_count computed for nodes: {len(sc)}")
    except Exception as e:
        print(f"OSM: count_streets_per_node failed, fallback to degree: {e}")

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
    print(f"OSM: intersection_nodes (>={OSM_INTERSECTION_MIN_STREETS}): {len(intersection_nodes)}")

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
        print(f"OSM: nearest_edges computed: {len(nearest_edges)}")
    except Exception as e:
        print(f"OSM: nearest_edges failed: {e}")
        return {}, point_nodes, []

    counts = {}
    hits = 0

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
        hits += 1

    print(f"OSM: snapped-to-intersection hits: {hits} unique_nodes: {len(counts)}")

    node_info = {}
    overall_counts = []
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
        with open(cache_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        print(f"OSM: cached context: {os.path.basename(cache_json)}")
    except Exception as e:
        print(f"OSM: cache write failed: {e}")

    return node_info, point_nodes, overall_counts


def _collapse_traffic_control(df: pd.DataFrame, lat_col: str, lon_col: str) -> pd.DataFrame:
    """
    Collapse all TRAFFIC CONTROL rows at the same normalized location into 1 row.
    Keeps tc_total_count, tc_first_reported, tc_last_reported for popup display.
    """
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


def create_map_from_csv(input_csv=FILENAME, output_map=MAPNAME, output_datajs=DATAJS):
    try:
        if not os.path.exists(input_csv):
            print(f"CSV not found: {input_csv}")
            _write_text_if_changed(output_datajs, "window.INCIDENTS_DATA=[];window.OSM_INTERSECTIONS_DATA=[];")
            return

        # Read header to safely choose usecols
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

        lat_col, lon_col = _find_lat_lon_columns(df)
        if not lat_col or not lon_col:
            print(f"Could not find latitude/longitude columns. Columns: {list(df.columns)}")
            _write_text_if_changed(output_datajs, "window.INCIDENTS_DATA=[];window.OSM_INTERSECTIONS_DATA=[];")
            return

        df[lat_col] = pd.to_numeric(df[lat_col].astype(str).str.strip(), errors="coerce")
        df[lon_col] = pd.to_numeric(df[lon_col].astype(str).str.strip(), errors="coerce")
        df = df.dropna(subset=[lat_col, lon_col]).copy()
        if df.empty:
            print("No mappable rows after parsing latitude/longitude.")
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

        dropped = len(df) - len(df_map)
        if dropped > 0:
            print(f"Map clamp: dropped {dropped} points outside Lafayette bounds")

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

        incidents_latlng = []
        for _, r in df_map.iterrows():
            try:
                incidents_latlng.append((float(r[lat_col]), float(r[lon_col])))
            except Exception:
                incidents_latlng.append((None, None))

        osm_node_info, osm_point_nodes, osm_overall_counts = _compute_osm_context_for_incidents(
            df_map, lat_col, lon_col, incidents_latlng
        )
        osm_intersections = osm_overall_counts

        _write_jsonjs_if_changed(output_datajs, incidents, osm_intersections)

        center_lat, center_lng = _compute_center(df_map, lat_col, lon_col)

        base_map = folium.Map(location=[center_lat, center_lng], zoom_start=12, control_scale=True)
        base_map.save(output_map)

        with open(output_map, "r", encoding="utf-8") as f:
            html = f.read()

        m = re.search(r"var\s+(map_[a-zA-Z0-9_]+)\s*=\s*L\.map\(", html)
        if not m:
            print("Could not find folium map variable name to inject JS.")
            return
        map_var = m.group(1)

        html = html.replace(
            "</head>",
            f'<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />\n'
            f'<script src="{os.path.basename(output_datajs)}"></script>\n'
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
    gap: 8px;
    align-items: center;
    padding: 8px 12px;
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
    overflow: auto;
    max-height: calc(82vh - 98px);
  }}

  .row {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 10px;
  }}

  .row label {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-right: 6px;
    white-space: nowrap;
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

  #insights {{
    border-top: 1px solid rgba(0,0,0,0.08);
    padding-top: 10px;
    margin-top: 10px;
  }}

  #insightsBody {{
    font-size: 12px;
    line-height: 1.35;
  }}

  #osmNote {{
    margin-top: 8px;
    font-size: 11px;
    color: rgba(0,0,0,0.62);
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
      max-height: calc(92vh - 98px);
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
      padding: 12px 12px;
      font-size: 16px;
      border-radius: 14px;
    }}

    .pill {{
      font-size: 13px;
      padding: 8px 12px;
    }}

    #controlPanel.collapsed {{
      max-height: 120px;
    }}
  }}

  #mobileHandle {{
    display: none;
  }}
</style>

<div id="controlPanel" class="collapsed">
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

  <div id="panelBody">

    <div class="row">
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

      <label style="margin-left:auto;">
        <input type="checkbox" id="chkInViewOnly">
        In view only
      </label>
    </div>

    <div class="row">
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

      <label style="margin-left:auto;">
        <input type="checkbox" id="chkTodayOnly">
        Today only
      </label>
    </div>

    <div class="row">
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

    <div class="row">
      <label><input type="checkbox" id="chkPoints" checked> Points</label>
      <label><input type="checkbox" id="chkHeat"> Heat</label>
      <label><input type="checkbox" id="chkIntersections"> Rounded</label>
      <label><input type="checkbox" id="chkOsmIntersections"> OSM</label>
      <label><input type="checkbox" id="chkMicro"> Micro</label>
      <label><input type="checkbox" id="chkRings"> Rings</label>
    </div>

    <div class="row">
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

    <div id="insights">
      <div style="font-weight:700; margin-bottom:6px;">Insights</div>
      <div id="insightsBody"></div>
      <div id="osmNote"></div>
    </div>

  </div>
</div>

<script>
(function() {{
  const INCIDENTS = window.INCIDENTS_DATA || [];
  const OSM_INTERSECTIONS = window.OSM_INTERSECTIONS_DATA || [];
  const renderer = L.canvas({{ padding: 0.5 }});

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

    insightsBody: document.getElementById("insightsBody"),
    osmNote: document.getElementById("osmNote"),
  }};

  function esc(s) {{
    return String(s || "").replace(/[&<>"']/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[c]));
  }}

  function popupHtml(row) {{
    const occurrences = (row.length >= 8 && row[7] != null) ? parseInt(row[7], 10) : 1;
    const occLine = (occurrences && occurrences > 1) ? ("<br>Occurrences: " + occurrences) : "";
    return "Location: " + esc(row[3]) +
           "<br>Type: " + esc(row[4]) +
           "<br>Reported: " + esc(row[2]) +
           "<br>Assisting: " + esc(row[5]) +
           occLine;
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
    const m1 = s.match(/(\\d{{1,2}})\\/(\\d{{1,2}})\\/(\\d{{4}})(?:\\s+(\\d{{1,2}}):(\\d{{2}})(?:\\s*([AaPp][Mm]))?)?/);
    if (!m1) return null;

    const mm = m1[1].padStart(2, "0");
    const dd = m1[2].padStart(2, "0");
    const yy = m1[3];

    let hh = null;
    let mi = null;

    if (m1[4] != null && m1[5] != null) {{
      hh = parseInt(m1[4], 10);
      mi = parseInt(m1[5], 10);
      const ap = m1[6] ? String(m1[6]).toLowerCase() : null;
      if (ap === "pm" && hh < 12) hh += 12;
      if (ap === "am" && hh === 12) hh = 0;
      if (hh < 0 || hh > 23) hh = null;
    }}

    const dt = new Date(parseInt(yy, 10), parseInt(mm, 10) - 1, parseInt(dd, 10), hh || 0, mi || 0, 0, 0);
    return {{ mm, dd, yy, hh, mi, dt }};
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

  function haversineKm(lat1, lon1, lat2, lon2) {{
    const R = 6371;
    const toRad = (x) => x * Math.PI / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
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
      const c = String(r[4] || "").trim();
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
    return String(row[4] || "").trim() === selected;
  }}

  function matchesCauseGroup(row, selectedGroup) {{
    if (!selectedGroup || selectedGroup === "__ALL__") return true;
    const causeNorm = normalizeCause(row[4]);
    const group = CAUSE_GROUPS.find((g) => g.id === selectedGroup);
    if (!group) return true;
    return group.keywords.some((kw) => causeNorm.includes(kw));
  }}

  function matchesDateFilter(row, f) {{
    if (!f.todayOnly && !f.mm && !f.dd && !f.yy) return true;
    const pr = parseReported(row[2]);
    if (!pr) return false;
    if (f.todayOnly) {{
      const now = new Date();
      const today = {{
        mm: String(now.getMonth() + 1).padStart(2, "0"),
        dd: String(now.getDate()).padStart(2, "0"),
        yy: String(now.getFullYear())
      }};
      return pr.yy === today.yy && pr.mm === today.mm && pr.dd === today.dd;
    }}
    if (f.yy && pr.yy !== f.yy) return false;
    if (f.mm && pr.mm !== f.mm) return false;
    if (f.dd && pr.dd !== f.dd) return false;
    return true;
  }}

  function matchesDayType(row, dayType) {{
    if (dayType === "all") return true;
    const pr = parseReported(row[2]);
    if (!pr || !pr.dt) return false;
    return dayTypeOf(pr.dt) === dayType;
  }}

  function matchesTimeBlock(row, block) {{
    if (block === "all") return true;
    const pr = parseReported(row[2]);
    if (!pr) return false;
    const tb = timeBlockOf(pr.hh);
    if (tb === "unknown") return false;
    return tb === block;
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
    return {{ mm: mm || "", dd: dd || "", yy: yy || "", dayType, timeBlock, cause, causeGroup, todayOnly, inViewOnly }};
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

  function renderInsights(filtered, centerLat, centerLng) {{
    if (!els.insightsBody) return;

    const topN = parseInt(els.topNSelect.value || "10", 10);
    const dInter = parseInt(els.precIntersections.value || "3", 10);
    const dMicro = parseInt(els.precMicro.value || "4", 10);

    const intersections = groupByRounded(filtered, dInter).slice(0, topN);
    const micro = groupByRounded(filtered, dMicro).slice(0, topN);

    const bins = [
      {{ name: "0-1 km", lo: 0, hi: 1, c: 0 }},
      {{ name: "1-2 km", lo: 1, hi: 2, c: 0 }},
      {{ name: "2-3 km", lo: 2, hi: 3, c: 0 }},
      {{ name: "3-5 km", lo: 3, hi: 5, c: 0 }},
      {{ name: "5-8 km", lo: 5, hi: 8, c: 0 }},
      {{ name: "8+ km", lo: 8, hi: 1e9, c: 0 }}
    ];

    for (const row of filtered) {{
      const d = haversineKm(centerLat, centerLng, row[0], row[1]);
      for (const b of bins) {{
        if (d >= b.lo && d < b.hi) {{
          b.c += 1;
          break;
        }}
      }}
    }}

    let html = "";
    html += "<div style='margin-bottom:8px;'><span style='font-weight:700;'>Top rounded intersections</span></div>";
    if (intersections.length === 0) {{
      html += "<div>None</div>";
    }} else {{
      html += "<ol style='margin:6px 0 10px 18px; padding:0;'>";
      for (const it of intersections) {{
        html += "<li>" + esc(it.key) + " (" + it.count + ")</li>";
      }}
      html += "</ol>";
    }}

    html += "<div style='margin-top:10px; margin-bottom:8px;'><span style='font-weight:700;'>Micro-hotspots</span></div>";
    if (micro.length === 0) {{
      html += "<div>None</div>";
    }} else {{
      html += "<ol style='margin:6px 0 10px 18px; padding:0;'>";
      for (const it of micro) {{
        html += "<li>" + esc(it.key) + " (" + it.count + ")</li>";
      }}
      html += "</ol>";
    }}

    html += "<div style='margin-top:10px; margin-bottom:6px;'><span style='font-weight:700;'>Distance from center</span></div>";
    html += "<ul style='margin:6px 0 0 18px; padding:0;'>";
    for (const b of bins) {{
      html += "<li>" + esc(b.name) + ": " + b.c + "</li>";
    }}
    html += "</ul>";

    els.insightsBody.innerHTML = html;

    if (els.osmNote) {{
      if (!OSM_INTERSECTIONS || OSM_INTERSECTIONS.length === 0) {{
        els.osmNote.textContent = "OSM hotspots unavailable. Install osmnx, then rebuild the map once.";
      }} else {{
        els.osmNote.textContent = "OSM hotspots enabled (OpenStreetMap road geometry).";
      }}
    }}
  }}

  let layers = {{
    points: null,
    heat: null,
    intersections: null,
    osmIntersections: null,
    micro: null,
    rings: null
  }};

  let lastFiltered = [];
  let renderTimer = null;

  function scheduleRender(mapObj, delayMs) {{
    const delay = Number.isFinite(delayMs) ? delayMs : 0;
    if (renderTimer) {{
      clearTimeout(renderTimer);
      renderTimer = null;
    }}
    renderTimer = setTimeout(function() {{
      renderTimer = null;
      renderAll(mapObj);
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

  function getPointRadius(mapObj) {{
    const zoom = mapObj && mapObj.getZoom ? mapObj.getZoom() : 12;
    const radius = 2.6 + (zoom - 10) * 0.55 + 2;
    return Math.max(4.4, Math.min(8.2, radius));
  }}

  function getPointRadius(mapObj) {{
    const zoom = mapObj && mapObj.getZoom ? mapObj.getZoom() : 12;
    const radius = 2.6 + (zoom - 10) * 0.55;
    return Math.max(2.4, Math.min(6.2, radius));
  }}

  function renderAll(mapObj) {{
    const f = currentFilterObj();
    clearLayers(mapObj);

    const showPoints = !!els.chkPoints.checked;
    const showHeat = !!els.chkHeat.checked;
    const showInter = !!els.chkIntersections.checked;
    const showOsmInter = !!els.chkOsmIntersections.checked;
    const showMicro = !!els.chkMicro.checked;
    const showRings = !!els.chkRings.checked;

    const topN = parseInt(els.topNSelect.value || "10", 10);
    const dInter = parseInt(els.precIntersections.value || "3", 10);
    const dMicro = parseInt(els.precMicro.value || "4", 10);

    const filtered = filteredIncidents(f, mapObj);
    lastFiltered = filtered;

    if (els.countTotal) els.countTotal.textContent = String(INCIDENTS.length);
    if (els.countFiltered) els.countFiltered.textContent = String(filtered.length);

    const inView = computeInViewCount(filtered, mapObj);
    if (els.countInView) els.countInView.textContent = String(inView);

    if (showPoints) {{
      const layer = L.layerGroup().addTo(mapObj);
      const grouped = groupByExactLocation(filtered);
      for (const group of grouped) {{
        let mk = null;
        if (group.rows.length > 1) {{
          const count = group.rows.length;
          const size = Math.round(getPointRadius(mapObj) * 2);
          const fontSize = Math.max(9, Math.round(size * 0.7));
          const icon = L.divIcon({{
            className: "",
            html: "<div class='incident-count-marker' style='width:" + size + "px;height:" + size + "px;line-height:" + size + "px;font-size:" + fontSize + "px;'>" + count + "</div>",
            iconSize: [size, size],
            iconAnchor: [size / 2, size / 2]
          }});
          mk = L.marker([group.lat, group.lng], {{ icon: icon, riseOnHover: true }});
          mk.bindPopup(createIncidentPopup(group.rows), {{ maxWidth: 320 }});
        }} else {{
          const radius = getPointRadius(mapObj);
          mk = L.circleMarker([group.lat, group.lng], {{
            radius: radius,
            renderer: renderer,
            color: "#2b6cb0",
            weight: 1.4,
            fillColor: "#cfe1ff",
            fillOpacity: 0.6
          }});
          mk.bindPopup(popupHtml(group.rows[0]), {{ maxWidth: 320 }});
        }}
        mk.addTo(layer);
      }}
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

    renderInsights(filtered, centerLat, centerLng);
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

    els.chkPoints.checked = true;
    els.chkHeat.checked = false;
    els.chkIntersections.checked = false;
    els.chkOsmIntersections.checked = false;
    els.chkMicro.checked = false;
    els.chkRings.checked = false;

    els.topNSelect.value = "10";
    els.precIntersections.value = "3";
    els.precMicro.value = "4";
  }}

  function setDateSelectState() {{
    const disabled = !!els.chkTodayOnly.checked;
    if (els.monthSelect) els.monthSelect.disabled = disabled;
    if (els.daySelect) els.daySelect.disabled = disabled;
    if (els.yearSelect) els.yearSelect.disabled = disabled;
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
      "chkPoints","chkHeat","chkIntersections","chkOsmIntersections","chkMicro","chkRings",
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

    mapObj.on("moveend zoomend", function(evt) {{
      if (evt && evt.type === "zoomend") {{
        scheduleRender(mapObj, 120);
      }} else {{
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

    if (els.countTotal) els.countTotal.textContent = String(INCIDENTS.length);
    scheduleRender(mapObj, 0);
  }});
}})();
</script>
"""

        html = html.replace("</body>", inject + "\n</body>")

        _write_text_if_changed(output_map, html)

        print(f"Map saved: {output_map}")
        print(f"Data JS saved: {output_datajs} (points={len(incidents)})")
        print(f"OSM intersections computed: {len(osm_intersections)}")

    except Exception as e:
        print(f"Error creating map: {e}")


def _in_lafayette_bounds_series(df, lat_col, lon_col):
    return (
        (df[lat_col] >= LAF_LAT_MIN)
        & (df[lat_col] <= LAF_LAT_MAX)
        & (df[lon_col] >= LAF_LON_MIN)
        & (df[lon_col] <= LAF_LON_MAX)
    )


def main():
    ensure_csv_exists(FILENAME)

    conn = _ensure_incident_index(INDEX_DB, FILENAME)
    last_csv_mtime = _file_mtime_int(FILENAME)

    while True:
        print(f"Tick {datetime.now()}")

        csv_mtime_before = _file_mtime_int(FILENAME)
        json_data = fetch_traffic_data()
        csv_changed = False

        if json_data:
            incidents = parse_traffic_data(json_data)
            if not incidents.empty:
                new_incidents = _filter_new_incidents(incidents, conn)

                if new_incidents.empty:
                    print("No new incidents found. CSV not changed.")
                else:
                    print(f"New incidents found: {len(new_incidents)}")
                    new_incidents = geocode_new_incidents(new_incidents, GOOGLE_API_KEY)
                    ok = save_new_to_csv(new_incidents, FILENAME)
                    if ok:
                        # Persist incident IDs after successful append
                        try:
                            ids_to_add = (
                                new_incidents["incident_number"]
                                .dropna()
                                .astype(str)
                                .tolist()
                            )
                            if ids_to_add:
                                conn.executemany(
                                    "INSERT OR IGNORE INTO incident_index (incident_number) VALUES (?)",
                                    [(x,) for x in ids_to_add],
                                )
                                conn.commit()
                        except Exception as e:
                            print(f"Warning: failed to persist incident index: {e}")
                        csv_changed = (_file_mtime_int(FILENAME) != csv_mtime_before)
            else:
                print("No incidents parsed.")
        else:
            print("No data fetched.")

        csv_mtime_after = _file_mtime_int(FILENAME)

        need_rebuild = False
        if not os.path.exists(MAPNAME) or not os.path.exists(DATAJS):
            need_rebuild = True
        if csv_mtime_after != last_csv_mtime:
            need_rebuild = True
        if csv_changed:
            need_rebuild = True

        if need_rebuild:
            create_map_from_csv(FILENAME, MAPNAME, DATAJS)
            last_csv_mtime = csv_mtime_after
        else:
            print("Skip map rebuild (CSV unchanged).")

        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
