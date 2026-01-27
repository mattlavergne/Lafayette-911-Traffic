import csv
import os
import sqlite3
from datetime import datetime
from typing import Dict, Iterable, List, Sequence, Tuple


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
                created_at TEXT
            )
            """
        )
        self.conn.commit()

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
                        datetime.utcnow().isoformat(timespec="seconds") + "Z",
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
            (incident_number, location, cause, reported, assisting, latitude, longitude, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            SELECT location, cause, reported, assisting, incident_number, latitude, longitude
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
                }
            )
        return out


def ensure_csv_exists(filename: str) -> None:
    if os.path.exists(filename):
        return
    cols = ["location", "cause", "reported", "assisting", "incident_number", "latitude", "longitude"]
    with open(filename, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=cols)
        writer.writeheader()


def _safe_float(value) -> float:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None
