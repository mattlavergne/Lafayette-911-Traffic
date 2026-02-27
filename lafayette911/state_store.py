import csv
import math
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


class StateStore:
    def __init__(self, db_path: str, csv_path: str) -> None:
        self.db_path = db_path
        self.csv_path = csv_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()
        self._seed_from_csv_if_empty()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def _ensure_schema(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS incident_index (incident_number TEXT PRIMARY KEY)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                incident_number TEXT PRIMARY KEY,
                location TEXT,
                cause TEXT,
                reported TEXT,
                assisting TEXT,
                latitude REAL,
                longitude REAL,
                created_at TEXT,
                weather_temp_f REAL,
                weather_precip_prob REAL,
                weather_precip_in REAL,
                weather_wind_speed_mph REAL,
                weather_wind_gust_mph REAL,
                weather_visibility_mi REAL,
                weather_sky_cover_pct REAL,
                weather_observed_at TEXT,
                weather_source TEXT
            )
            """
        )
        self._ensure_incidents_columns()
        self.conn.commit()

    def _ensure_incidents_columns(self) -> None:
        try:
            cols = {
                row[1]
                for row in self.conn.execute("PRAGMA table_info(incidents)").fetchall()
                if row and row[1]
            }
        except Exception:
            cols = set()

        additions = [
            ("weather_temp_f", "REAL"),
            ("weather_precip_prob", "REAL"),
            ("weather_precip_in", "REAL"),
            ("weather_wind_speed_mph", "REAL"),
            ("weather_wind_gust_mph", "REAL"),
            ("weather_visibility_mi", "REAL"),
            ("weather_sky_cover_pct", "REAL"),
            ("weather_observed_at", "TEXT"),
            ("weather_source", "TEXT"),
        ]
        for name, col_type in additions:
            if name in cols:
                continue
            try:
                self.conn.execute(f"ALTER TABLE incidents ADD COLUMN {name} {col_type}")
            except Exception:
                continue

    def _seed_from_csv_if_empty(self) -> None:
        try:
            count = self.conn.execute("SELECT COUNT(1) FROM incident_index").fetchone()[0]
        except Exception:
            count = 0
        if count == 0:
            self._seed_from_csv()

    def _seed_from_csv(self) -> None:
        if not os.path.exists(self.csv_path):
            return

        with open(self.csv_path, "r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return

            rows: List[Tuple] = []
            index_rows: List[Tuple[str]] = []
            for row in reader:
                incident_number = (row.get("incident_number") or "").strip()
                if not incident_number:
                    continue
                index_rows.append((incident_number,))
                rows.append(
                    (
                        incident_number,
                        row.get("location", ""),
                        row.get("cause", ""),
                        row.get("reported", ""),
                        row.get("assisting", ""),
                        _safe_float(row.get("latitude")),
                        _safe_float(row.get("longitude")),
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        _safe_float(row.get("weather_temp_f")),
                        _safe_float(row.get("weather_precip_prob")),
                        _safe_float(row.get("weather_precip_in")),
                        _safe_float(row.get("weather_wind_speed_mph")),
                        _safe_float(row.get("weather_wind_gust_mph")),
                        _safe_float(row.get("weather_visibility_mi")),
                        _safe_float(row.get("weather_sky_cover_pct")),
                        _safe_text(row.get("weather_observed_at")),
                        _safe_text(row.get("weather_source")),
                    )
                )
                if len(rows) >= 500:
                    self._insert_batch(rows, index_rows)
                    rows = []
                    index_rows = []

            if rows:
                self._insert_batch(rows, index_rows)

    def _insert_batch(self, rows: Sequence[Tuple], index_rows: Sequence[Tuple[str]]) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO incident_index (incident_number) VALUES (?)",
            index_rows,
        )
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO incidents
            (incident_number, location, cause, reported, assisting, latitude, longitude, created_at,
             weather_temp_f, weather_precip_prob, weather_precip_in, weather_wind_speed_mph,
             weather_wind_gust_mph, weather_visibility_mi, weather_sky_cover_pct,
             weather_observed_at, weather_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()

    def _existing_ids(self, ids: Sequence[str], batch_size: int = 900) -> set:
        existing = set()
        for i in range(0, len(ids), batch_size):
            chunk = ids[i : i + batch_size]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            query = f"SELECT incident_number FROM incident_index WHERE incident_number IN ({placeholders})"
            rows = self.conn.execute(query, chunk).fetchall()
            existing.update(r[0] for r in rows if r and r[0] is not None)
        return existing

    def store_new_incidents(self, incidents: Sequence[Dict]) -> List[Dict]:
        if not incidents:
            return []

        new_incidents = self.filter_new_incidents(incidents)
        if not new_incidents:
            return []

        rows: List[Tuple] = []
        index_rows: List[Tuple[str]] = []
        for inc in new_incidents:
            incident_number = str(inc.get("incident_number") or "").strip()
            if not incident_number:
                continue
            index_rows.append((incident_number,))
            rows.append(
                (
                    incident_number,
                    inc.get("location", ""),
                    inc.get("cause", ""),
                    inc.get("reported", ""),
                    inc.get("assisting", ""),
                    _safe_float(inc.get("latitude")),
                    _safe_float(inc.get("longitude")),
                    datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    _safe_float(inc.get("weather_temp_f")),
                    _safe_float(inc.get("weather_precip_prob")),
                    _safe_float(inc.get("weather_precip_in")),
                    _safe_float(inc.get("weather_wind_speed_mph")),
                    _safe_float(inc.get("weather_wind_gust_mph")),
                    _safe_float(inc.get("weather_visibility_mi")),
                    _safe_float(inc.get("weather_sky_cover_pct")),
                    _safe_text(inc.get("weather_observed_at")),
                    _safe_text(inc.get("weather_source")),
                )
            )

        if rows:
            self._insert_batch(rows, index_rows)

        return new_incidents

    def filter_new_incidents(self, incidents: Sequence[Dict]) -> List[Dict]:
        if not incidents:
            return []

        ids = [str(inc.get("incident_number") or "") for inc in incidents]
        existing = self._existing_ids(ids)
        return [inc for inc in incidents if str(inc.get("incident_number") or "") not in existing]

    def append_to_csv(self, incidents: Sequence[Dict]) -> None:
        if not incidents:
            return
        ensure_csv_exists(self.csv_path)

        col_order = [
            "location",
            "cause",
            "reported",
            "assisting",
            "incident_number",
            "latitude",
            "longitude",
            "weather_temp_f",
            "weather_precip_prob",
            "weather_precip_in",
            "weather_wind_speed_mph",
            "weather_wind_gust_mph",
            "weather_visibility_mi",
            "weather_sky_cover_pct",
            "weather_observed_at",
            "weather_source",
        ]

        write_header = os.path.getsize(self.csv_path) == 0
        with open(self.csv_path, "a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=col_order)
            if write_header:
                writer.writeheader()
            for inc in incidents:
                row = {k: inc.get(k, "") for k in col_order}
                writer.writerow(row)

    def read_all_incidents(self) -> List[Dict[str, str]]:
        rows = self.conn.execute(
            """
            SELECT location, cause, reported, assisting, incident_number, latitude, longitude,
                   weather_temp_f, weather_precip_prob, weather_precip_in,
                   weather_wind_speed_mph, weather_wind_gust_mph, weather_visibility_mi,
                   weather_sky_cover_pct, weather_observed_at, weather_source
            FROM incidents
            """
        ).fetchall()

        out: List[Dict[str, str]] = []
        for row in rows:
            out.append(
                {
                    "location": row[0] or "",
                    "cause": row[1] or "",
                    "reported": row[2] or "",
                    "assisting": row[3] or "",
                    "incident_number": row[4] or "",
                    "latitude": row[5],
                    "longitude": row[6],
                    "weather_temp_f": row[7],
                    "weather_precip_prob": row[8],
                    "weather_precip_in": row[9],
                    "weather_wind_speed_mph": row[10],
                    "weather_wind_gust_mph": row[11],
                    "weather_visibility_mi": row[12],
                    "weather_sky_cover_pct": row[13],
                    "weather_observed_at": row[14] or "",
                    "weather_source": row[15] or "",
                }
            )
        return out


def ensure_csv_exists(filename: str) -> None:
    if os.path.exists(filename):
        _ensure_csv_columns(filename)
        return
    cols = [
        "location",
        "cause",
        "reported",
        "assisting",
        "incident_number",
        "latitude",
        "longitude",
        "weather_temp_f",
        "weather_precip_prob",
        "weather_precip_in",
        "weather_wind_speed_mph",
        "weather_wind_gust_mph",
        "weather_visibility_mi",
        "weather_sky_cover_pct",
        "weather_observed_at",
        "weather_source",
    ]
    with open(filename, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=cols)
        writer.writeheader()


def _ensure_csv_columns(filename: str) -> None:
    try:
        with open(filename, "r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
    except Exception:
        header = []

    needed = [
        "location",
        "cause",
        "reported",
        "assisting",
        "incident_number",
        "latitude",
        "longitude",
        "weather_temp_f",
        "weather_precip_prob",
        "weather_precip_in",
        "weather_wind_speed_mph",
        "weather_wind_gust_mph",
        "weather_visibility_mi",
        "weather_sky_cover_pct",
        "weather_observed_at",
        "weather_source",
    ]

    if not header:
        return

    missing = [c for c in needed if c not in header]
    if not missing:
        return

    tmp_path = filename + ".tmp"
    with open(filename, "r", encoding="utf-8", newline="") as src, open(
        tmp_path, "w", encoding="utf-8", newline=""
    ) as dst:
        reader = csv.DictReader(src)
        fieldnames = list(reader.fieldnames or [])
        for col in missing:
            fieldnames.append(col)
        writer = csv.DictWriter(dst, fieldnames=fieldnames)
        writer.writeheader()
        for row in reader:
            writer.writerow(row)

    os.replace(tmp_path, filename)


def _safe_float(value) -> Optional[float]:
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
