import csv
import json
import math
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


class StateStore:
    # app_meta keys for feed-visibility tracking: the timestamp of the most
    # recent successful feed sweep, and the exact incident ids that sweep
    # contained. The id list is what makes "currently in the feed" exact —
    # timestamp comparisons alone can't tell two sweeps in the same second
    # apart — and comparing it across sweeps triggers a re-render when
    # incidents leave the feed, not only when new ones arrive.
    FEED_SWEEP_META_KEY = "last_feed_sweep_at"
    FEED_ACTIVE_IDS_META_KEY = "feed_active_ids"

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
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS geocode_api_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requested_at_epoch INTEGER NOT NULL
            )
            """
        )
        # Second line of dedupe defence. The primary key is the synthesized
        # incident_number (location+cause+reported verbatim), which breaks if
        # the feed ever reformats whitespace or casing of an existing entry.
        # This index keys the same triple in normalized form so formatting
        # jitter can never re-insert an incident we already have.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incident_content_index (
                content_key TEXT PRIMARY KEY,
                incident_number TEXT NOT NULL
            )
            """
        )
        # Address-level geocode negative cache. The feed re-lists recurring
        # unresolvable streets as brand-new incidents (fresh incident_number),
        # and without this table every recurrence burned another Google API
        # call — enough to drain a small daily budget on its own.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS geocode_failed_locations (
                location_key TEXT PRIMARY KEY,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt_epoch INTEGER NOT NULL
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
            # Time-context enrichment
            ("hour_of_day", "INTEGER"),
            ("day_of_week", "INTEGER"),
            ("is_weekend", "INTEGER"),
            ("is_rush_hour", "INTEGER"),
            ("month", "INTEGER"),
            ("is_school_day", "INTEGER"),
            ("is_holiday", "INTEGER"),
            # NWS active alerts
            ("nws_flash_flood_warning", "INTEGER"),
            ("nws_severe_thunderstorm_warning", "INTEGER"),
            ("nws_tornado_watch", "INTEGER"),
            ("nws_active_alert_count", "INTEGER"),
            # Road classification (from location name or OSM)
            ("road_type", "TEXT"),
            # Number of times geocoding has been attempted for this incident.
            # Incidents with geocode_attempts >= 3 are skipped by the retry
            # loop to prevent burning API quota on permanently-unresolvable
            # addresses.
            ("geocode_attempts", "INTEGER"),
            # Feed-visibility tracking: when this incident first/last appeared
            # in the public feed and how many feed checks listed it. This
            # measures time VISIBLE in the feed only — not response time and
            # not clearance time. NULL on rows that predate the tracking.
            ("first_seen_at", "TEXT"),
            ("last_seen_at", "TEXT"),
            ("observation_count", "INTEGER"),
            # How the map point was placed: precise / intersection / place /
            # approximate / cached. NULL on rows geocoded before tracking.
            ("geocode_confidence", "TEXT"),
        ]
        for name, col_type in additions:
            if name in cols:
                continue
            try:
                self.conn.execute(f"ALTER TABLE incidents ADD COLUMN {name} {col_type}")
            except Exception:
                continue

    def _seed_from_csv_if_empty(self) -> None:
        # Always sync from CSV so any rows present in the CSV but absent from the
        # DB (e.g. after a DB reset or partial restore) become visible on the map.
        # _insert_batch uses INSERT OR IGNORE, so re-processing existing rows is
        # a safe no-op.
        self._seed_from_csv()
        # One-time migration: reset geocode_attempts for legacy rows impacted by
        # a historical cache bug. This should never run every startup because
        # that would allow infinite geocoding retries and burn API quota.
        self._reset_geocode_attempts_for_unlocated_once()
        # One-time backfill of the normalized-content dedupe index for rows
        # stored before the index existed.
        self._backfill_content_index_once()

    def _meta_get(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    def _reset_geocode_attempts_for_unlocated_once(self) -> None:
        """Run one legacy geocode-attempt reset migration once per DB."""
        if self._meta_get("legacy_geocode_attempt_reset_v1") == "1":
            return
        try:
            self.conn.execute(
                """
                UPDATE incidents
                SET geocode_attempts = NULL
                WHERE (latitude IS NULL OR longitude IS NULL)
                  AND geocode_attempts IS NOT NULL
                """
            )
            self.conn.commit()
            self._meta_set("legacy_geocode_attempt_reset_v1", "1")
        except Exception:
            pass

    def reserve_geocode_requests(self, requested: int, max_requests_per_24h: int = 75) -> int:
        """Reserve and record geocoding API request slots for the last 24 hours.

        Returns the number of requests allowed right now (0..requested).
        """
        if requested <= 0 or max_requests_per_24h <= 0:
            return 0
        now = int(time.time())
        cutoff = now - 24 * 60 * 60
        self.conn.execute(
            "DELETE FROM geocode_api_requests WHERE requested_at_epoch < ?",
            (cutoff,),
        )
        current = self.conn.execute(
            "SELECT COUNT(1) FROM geocode_api_requests WHERE requested_at_epoch >= ?",
            (cutoff,),
        ).fetchone()[0]
        remaining = max(0, int(max_requests_per_24h) - int(current))
        approved = min(int(requested), remaining)
        if approved > 0:
            self.conn.executemany(
                "INSERT INTO geocode_api_requests (requested_at_epoch) VALUES (?)",
                [(now,)] * approved,
            )
        self.conn.commit()
        return approved

    def get_skippable_geocode_failures(
        self,
        max_attempts: int = 3,
        retry_after_seconds: int = 7 * 24 * 60 * 60,
    ) -> set:
        """Return normalized address keys that should NOT be sent to Google.

        An address becomes skippable once it has failed *max_attempts* times;
        it becomes eligible again after *retry_after_seconds* so a temporarily
        broken address is not blacklisted forever.
        """
        cutoff = int(time.time()) - int(retry_after_seconds)
        rows = self.conn.execute(
            "SELECT location_key FROM geocode_failed_locations "
            "WHERE attempts >= ? AND last_attempt_epoch > ?",
            (int(max_attempts), cutoff),
        ).fetchall()
        return {r[0] for r in rows if r and r[0]}

    def record_geocode_failures(self, location_keys: Sequence[str]) -> None:
        """Record one failed geocode attempt for each normalized address key."""
        keys = [k for k in location_keys if k]
        if not keys:
            return
        now = int(time.time())
        self.conn.executemany(
            """
            INSERT INTO geocode_failed_locations (location_key, attempts, last_attempt_epoch)
            VALUES (?, 1, ?)
            ON CONFLICT(location_key) DO UPDATE SET
                attempts = attempts + 1,
                last_attempt_epoch = excluded.last_attempt_epoch
            """,
            [(k, now) for k in keys],
        )
        self.conn.commit()

    def clear_geocode_failures(self, location_keys: Sequence[str]) -> None:
        """Forget failure history for addresses that geocoded successfully."""
        keys = [k for k in location_keys if k]
        if not keys:
            return
        placeholders = ",".join("?" for _ in keys)
        self.conn.execute(
            f"DELETE FROM geocode_failed_locations WHERE location_key IN ({placeholders})",
            keys,
        )
        self.conn.commit()

    def count_unlocated_incidents(self) -> int:
        """Total incidents still waiting for coordinates (the geocode queue)."""
        row = self.conn.execute(
            "SELECT COUNT(1) FROM incidents WHERE latitude IS NULL OR longitude IS NULL"
        ).fetchone()
        return int(row[0]) if row else 0

    def retire_unlocated_incidents(self, incident_numbers: Sequence[str], attempts: int = 3) -> None:
        """Take incidents out of the retry queue without spending API budget.

        Used for incidents whose address is blacklisted in the negative cache:
        leaving them in the queue would clog the retry batch every cycle and
        starve incidents behind them.
        """
        nums = [n for n in incident_numbers if n]
        if not nums:
            return
        placeholders = ",".join("?" for _ in nums)
        self.conn.execute(
            f"""
            UPDATE incidents
            SET geocode_attempts = MAX(COALESCE(geocode_attempts, 0), ?)
            WHERE incident_number IN ({placeholders})
              AND (latitude IS NULL OR longitude IS NULL)
            """,
            [int(attempts)] + nums,
        )
        self.conn.commit()

    def purge_shared_fallback_coordinates(self, min_distinct_locations: int = 8):
        """One-time repair: re-queue incidents pinned on geocoder-fallback points.

        Before precision validation shipped, Google's locality/route fallbacks
        pinned MANY different addresses onto identical coordinates — the
        downtown city centroid, whole-road midpoints. A genuine address never
        hosts that many distinct location strings, so any exact coordinate
        shared by ``min_distinct_locations`` different (normalized) locations
        is treated as a fallback point: those incidents lose their coordinates
        and rejoin the geocoding queue, where the precision filter either
        places them properly or retires them honestly.

        Runs once (app_meta-guarded, restart-safe). Returns a list of
        ``(lat, lng, distinct_locations, incidents_requeued)`` for logging.
        """
        if self._meta_get("fallback_coord_purge_v1") == "done":
            return []
        rows = self.conn.execute(
            """
            SELECT latitude, longitude,
                   COUNT(DISTINCT LOWER(TRIM(location))) AS distinct_locs,
                   COUNT(*) AS n
            FROM incidents
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            GROUP BY latitude, longitude
            HAVING distinct_locs >= ?
            """,
            (int(min_distinct_locations),),
        ).fetchall()
        purged = []
        for lat, lng, distinct_locs, n in rows:
            self.conn.execute(
                "UPDATE incidents SET latitude = NULL, longitude = NULL, geocode_attempts = 0"
                " WHERE latitude = ? AND longitude = ?",
                (lat, lng),
            )
            purged.append((lat, lng, int(distinct_locs), int(n)))
        self._meta_set("fallback_coord_purge_v1", "done")
        self.conn.commit()
        return purged

    def seed_negative_cache_from_history(self) -> int:
        """One-time migration: blacklist addresses that already failed 3+ times.

        Incidents stored before the address-level negative cache existed carry
        their failure history only in per-incident geocode_attempts. Without
        this seeding, a large historical backlog re-burns API budget on
        addresses that were already proven unresolvable.
        """
        if self._meta_get("negcache_seed_v1"):
            return 0
        from lafayette911.fetch_incidents import normalize_geocode_key

        rows = self.conn.execute(
            """
            SELECT location, MAX(COALESCE(geocode_attempts, 0))
            FROM incidents
            WHERE (latitude IS NULL OR longitude IS NULL)
              AND location IS NOT NULL AND location != ''
              AND COALESCE(geocode_attempts, 0) >= 3
            GROUP BY location
            """
        ).fetchall()
        now = int(time.time())
        seeded = 0
        for location, attempts in rows:
            key = normalize_geocode_key(location)
            if not key:
                continue
            self.conn.execute(
                """
                INSERT INTO geocode_failed_locations (location_key, attempts, last_attempt_epoch)
                VALUES (?, ?, ?)
                ON CONFLICT(location_key) DO NOTHING
                """,
                (key, int(attempts), now),
            )
            seeded += 1
        self._meta_set("negcache_seed_v1", "1")
        self.conn.commit()
        return seeded

    def get_remaining_geocode_requests(self, max_requests_per_24h: int = 75) -> int:
        """Return remaining Google geocode requests allowed in the rolling 24h window."""
        now = int(time.time())
        cutoff = now - 24 * 60 * 60
        self.conn.execute(
            "DELETE FROM geocode_api_requests WHERE requested_at_epoch < ?",
            (cutoff,),
        )
        current = self.conn.execute(
            "SELECT COUNT(1) FROM geocode_api_requests WHERE requested_at_epoch >= ?",
            (cutoff,),
        ).fetchone()[0]
        self.conn.commit()
        return max(0, int(max_requests_per_24h) - int(current))

    def _seed_from_csv(self) -> None:
        if not os.path.exists(self.csv_path):
            return

        with open(self.csv_path, "r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return

            rows: List[Tuple] = []
            index_rows: List[Tuple[str]] = []
            content_rows: List[Tuple[str, str]] = []
            for row in reader:
                incident_number = (row.get("incident_number") or "").strip()
                if not incident_number:
                    continue
                index_rows.append((incident_number,))
                content_rows.append(
                    (
                        self.content_key(row.get("location"), row.get("cause"), row.get("reported")),
                        incident_number,
                    )
                )
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
                        _safe_int(row.get("hour_of_day")),
                        _safe_int(row.get("day_of_week")),
                        _safe_int(row.get("is_weekend")),
                        _safe_int(row.get("is_rush_hour")),
                        _safe_int(row.get("month")),
                        _safe_int(row.get("is_school_day")),
                        _safe_int(row.get("is_holiday")),
                        _safe_int(row.get("nws_flash_flood_warning")),
                        _safe_int(row.get("nws_severe_thunderstorm_warning")),
                        _safe_int(row.get("nws_tornado_watch")),
                        _safe_int(row.get("nws_active_alert_count")),
                        _safe_text(row.get("road_type")),
                        # Feed-visibility fields are unknown for CSV-seeded
                        # rows; leave them NULL rather than inventing history.
                        None,
                        None,
                        None,
                        None,
                    )
                )
                if len(rows) >= 500:
                    self._insert_batch(rows, index_rows, content_rows)
                    rows = []
                    index_rows = []
                    content_rows = []

            if rows:
                self._insert_batch(rows, index_rows, content_rows)

    @staticmethod
    def content_key(location, cause, reported) -> str:
        """Normalized identity of an incident: whitespace-collapsed, uppercased
        location|cause|reported. Two feed entries with the same key are the
        same real-world incident regardless of formatting."""
        def norm(value) -> str:
            return re.sub(r"\s+", " ", str(value or "").strip()).upper()
        return norm(location) + "|" + norm(cause) + "|" + norm(reported)

    def _backfill_content_index_once(self) -> None:
        if self._meta_get("content_index_backfill_v1") == "1":
            return
        try:
            rows = self.conn.execute(
                "SELECT incident_number, location, cause, reported FROM incidents"
            ).fetchall()
            self.conn.executemany(
                "INSERT OR IGNORE INTO incident_content_index (content_key, incident_number) VALUES (?, ?)",
                [
                    (self.content_key(loc, cause, reported), num)
                    for num, loc, cause, reported in rows
                    if num
                ],
            )
            self.conn.commit()
            self._meta_set("content_index_backfill_v1", "1")
        except Exception:
            pass

    def _existing_content_keys(self, keys: Sequence[str], batch_size: int = 900) -> set:
        existing = set()
        for i in range(0, len(keys), batch_size):
            chunk = keys[i : i + batch_size]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT content_key FROM incident_content_index WHERE content_key IN ({placeholders})",
                chunk,
            ).fetchall()
            existing.update(r[0] for r in rows if r and r[0] is not None)
        return existing

    def _insert_batch(
        self,
        rows: Sequence[Tuple],
        index_rows: Sequence[Tuple[str]],
        content_rows: Sequence[Tuple[str, str]] = (),
    ) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO incident_index (incident_number) VALUES (?)",
            index_rows,
        )
        if content_rows:
            self.conn.executemany(
                "INSERT OR IGNORE INTO incident_content_index (content_key, incident_number) VALUES (?, ?)",
                content_rows,
            )
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO incidents
            (incident_number, location, cause, reported, assisting, latitude, longitude, created_at,
             weather_temp_f, weather_precip_prob, weather_precip_in, weather_wind_speed_mph,
             weather_wind_gust_mph, weather_visibility_mi, weather_sky_cover_pct,
             weather_observed_at, weather_source,
             hour_of_day, day_of_week, is_weekend, is_rush_hour, month, is_school_day, is_holiday,
             nws_flash_flood_warning, nws_severe_thunderstorm_warning, nws_tornado_watch,
             nws_active_alert_count, road_type,
             first_seen_at, last_seen_at, observation_count, geocode_confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        now_iso = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        rows: List[Tuple] = []
        index_rows: List[Tuple[str]] = []
        content_rows: List[Tuple[str, str]] = []
        for inc in new_incidents:
            incident_number = str(inc.get("incident_number") or "").strip()
            if not incident_number:
                continue
            index_rows.append((incident_number,))
            content_rows.append(
                (
                    self.content_key(inc.get("location"), inc.get("cause"), inc.get("reported")),
                    incident_number,
                )
            )
            rows.append(
                (
                    incident_number,
                    inc.get("location", ""),
                    inc.get("cause", ""),
                    inc.get("reported", ""),
                    inc.get("assisting", ""),
                    _safe_float(inc.get("latitude")),
                    _safe_float(inc.get("longitude")),
                    now_iso,
                    _safe_float(inc.get("weather_temp_f")),
                    _safe_float(inc.get("weather_precip_prob")),
                    _safe_float(inc.get("weather_precip_in")),
                    _safe_float(inc.get("weather_wind_speed_mph")),
                    _safe_float(inc.get("weather_wind_gust_mph")),
                    _safe_float(inc.get("weather_visibility_mi")),
                    _safe_float(inc.get("weather_sky_cover_pct")),
                    _safe_text(inc.get("weather_observed_at")),
                    _safe_text(inc.get("weather_source")),
                    _safe_int(inc.get("hour_of_day")),
                    _safe_int(inc.get("day_of_week")),
                    _safe_int(inc.get("is_weekend")),
                    _safe_int(inc.get("is_rush_hour")),
                    _safe_int(inc.get("month")),
                    _safe_int(inc.get("is_school_day")),
                    _safe_int(inc.get("is_holiday")),
                    _safe_int(inc.get("nws_flash_flood_warning")),
                    _safe_int(inc.get("nws_severe_thunderstorm_warning")),
                    _safe_int(inc.get("nws_tornado_watch")),
                    _safe_int(inc.get("nws_active_alert_count")),
                    _safe_text(inc.get("road_type")),
                    # First appearance in the feed IS this insert; the sweep
                    # that discovered it counts as observation #1.
                    now_iso,
                    now_iso,
                    1,
                    _safe_text(inc.get("geocode_confidence")) or None,
                )
            )

        if rows:
            self._insert_batch(rows, index_rows, content_rows)

        return new_incidents

    def touch_feed_observations(self, incidents: Sequence[Dict]) -> Tuple[int, bool]:
        """Record one feed observation for every incident currently listed.

        Called once per *successful* feed check with the full parsed snapshot,
        BEFORE new incidents are stored — so only already-known incidents
        match here (brand-new ones get their first observation stamped at
        insert). Every matched row gets the same per-sweep timestamp in
        last_seen_at, observation_count is incremented, and first_seen_at is
        backfilled from created_at for rows that predate this tracking.

        These fields measure how long an incident stays VISIBLE in the public
        feed — they are NOT response time or clearance time.

        Returns (rows_touched, active_set_changed). The second value is True
        when the set of feed-listed incidents differs from the previous sweep
        (something appeared or disappeared), which callers use to trigger a
        re-render so the page's "currently in feed" markers stay honest.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        ids = {str(inc.get("incident_number") or "").strip() for inc in incidents}
        ids.discard("")
        # Resolve through the content index too, so feed formatting jitter
        # (which changes the synthesized incident_number) still counts as a
        # sighting of the incident we already have.
        keys = sorted({
            self.content_key(inc.get("location"), inc.get("cause"), inc.get("reported"))
            for inc in incidents
        })
        for i in range(0, len(keys), 900):
            chunk = keys[i : i + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT incident_number FROM incident_content_index WHERE content_key IN ({placeholders})",
                chunk,
            ).fetchall()
            ids.update(str(r[0]) for r in rows if r and r[0])

        touched = 0
        id_list = sorted(ids)
        for i in range(0, len(id_list), 900):
            chunk = id_list[i : i + 900]
            placeholders = ",".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"""
                UPDATE incidents
                SET last_seen_at = ?,
                    observation_count = COALESCE(observation_count, 0) + 1,
                    first_seen_at = COALESCE(first_seen_at, created_at, ?)
                WHERE incident_number IN ({placeholders})
                """,
                [now, now] + chunk,
            )
            touched += cursor.rowcount
        self.conn.commit()

        # The exact membership of this sweep is the source of truth for
        # "currently in the feed" — brand-new incidents (not yet in the DB at
        # touch time) are included by their feed ids, which match once stored.
        active_json = json.dumps(id_list, separators=(",", ":"))
        changed = active_json != (self._meta_get(self.FEED_ACTIVE_IDS_META_KEY) or "")
        self._meta_set(self.FEED_ACTIVE_IDS_META_KEY, active_json)
        self._meta_set(self.FEED_SWEEP_META_KEY, now)
        return touched, changed

    def get_last_feed_sweep_at(self) -> Optional[str]:
        """Timestamp of the most recent successful feed sweep, or None."""
        return self._meta_get(self.FEED_SWEEP_META_KEY)

    def filter_new_incidents(self, incidents: Sequence[Dict]) -> List[Dict]:
        """Return only incidents we have never stored.

        Two filters, both required for the no-duplicates guarantee:
          1. exact incident_number match against incident_index;
          2. normalized content match against incident_content_index, which
             also dedupes repeats *within* the same feed snapshot.
        """
        if not incidents:
            return []

        ids = [str(inc.get("incident_number") or "") for inc in incidents]
        existing = self._existing_ids(ids)
        keys = [
            self.content_key(inc.get("location"), inc.get("cause"), inc.get("reported"))
            for inc in incidents
        ]
        existing_keys = self._existing_content_keys(keys)

        out: List[Dict] = []
        seen_in_batch: set = set()
        for inc, key in zip(incidents, keys):
            if str(inc.get("incident_number") or "") in existing:
                continue
            if key in existing_keys or key in seen_in_batch:
                continue
            seen_in_batch.add(key)
            out.append(inc)
        return out

    def get_location_coords_map(self) -> Dict[str, tuple]:
        """Return {location: (lat, lon)} for all incidents with valid coordinates.

        Used as an in-process cache so repeated incidents at the same address
        (e.g. a busy intersection) are resolved from the DB rather than
        consuming Google Geocoding API quota.
        """
        rows = self.conn.execute(
            "SELECT location, latitude, longitude FROM incidents "
            "WHERE location IS NOT NULL AND location != '' "
            "  AND latitude IS NOT NULL AND longitude IS NOT NULL"
        ).fetchall()
        cache: Dict[str, tuple] = {}
        for loc, lat, lon in rows:
            if loc and loc not in cache:
                lat_f = _safe_float(lat)
                lon_f = _safe_float(lon)
                if lat_f is not None and lon_f is not None:
                    cache[loc] = (lat_f, lon_f)
        return cache

    def get_unlocated_incidents(self, limit: int = 100) -> List[Dict]:
        """Return incidents stored with NULL lat/lon that still have retry budget.

        Incidents with geocode_attempts >= 3 are excluded — they have been
        tried and failed repeatedly and should not burn more API quota.
        """
        rows = self.conn.execute(
            """
            SELECT incident_number, location, cause, reported, assisting
            FROM incidents
            WHERE (latitude IS NULL OR longitude IS NULL)
              AND (geocode_attempts IS NULL OR geocode_attempts < 3)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "incident_number": r[0],
                "location": r[1] or "",
                "cause": r[2] or "",
                "reported": r[3] or "",
                "assisting": r[4] or "",
            }
            for r in rows
        ]

    def increment_geocode_attempts(self, incident_numbers: Sequence[str]) -> None:
        """Increment geocode_attempts for incidents that still lack coordinates.

        Called after a geocoding pass so permanently-unresolvable addresses
        are eventually retired from the retry queue.
        """
        if not incident_numbers:
            return
        placeholders = ",".join("?" for _ in incident_numbers)
        self.conn.execute(
            f"""
            UPDATE incidents
            SET geocode_attempts = COALESCE(geocode_attempts, 0) + 1
            WHERE incident_number IN ({placeholders})
              AND (latitude IS NULL OR longitude IS NULL)
            """,
            list(incident_numbers),
        )
        self.conn.commit()

    def update_incident_coords(self, incidents: Sequence[Dict]) -> int:
        """UPDATE lat/lon for incidents that previously lacked coordinates.

        Only updates rows whose coordinates are still NULL so already-located
        incidents are never overwritten.  Returns the number of rows updated.
        """
        updated = 0
        for inc in incidents:
            lat = _safe_float(inc.get("latitude"))
            lon = _safe_float(inc.get("longitude"))
            if lat is None or lon is None:
                continue
            incident_number = str(inc.get("incident_number") or "").strip()
            if not incident_number:
                continue
            cursor = self.conn.execute(
                """
                UPDATE incidents
                SET latitude = ?, longitude = ?,
                    geocode_confidence = COALESCE(?, geocode_confidence)
                WHERE incident_number = ?
                  AND (latitude IS NULL OR longitude IS NULL)
                """,
                (lat, lon, _safe_text(inc.get("geocode_confidence")) or None, incident_number),
            )
            updated += cursor.rowcount
        if updated:
            self.conn.commit()
        return updated

    def update_csv_coords(self, incidents: Sequence[Dict]) -> None:
        """Back-fill latitude/longitude in the CSV for rows that now have coords.

        Reads the whole CSV, updates the matching rows in-memory, and writes
        a new file atomically.  No-ops gracefully if the CSV doesn't exist.
        """
        if not incidents or not os.path.exists(self.csv_path):
            return

        # Build a lookup: incident_number -> (lat, lon)
        coord_map: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
        for inc in incidents:
            lat = _safe_float(inc.get("latitude"))
            lon = _safe_float(inc.get("longitude"))
            if lat is None or lon is None:
                continue
            num = str(inc.get("incident_number") or "").strip()
            if num:
                coord_map[num] = (lat, lon)

        if not coord_map:
            return

        tmp_path = self.csv_path + ".tmp"
        try:
            with open(self.csv_path, "r", encoding="utf-8", newline="") as src, \
                 open(tmp_path, "w", encoding="utf-8", newline="") as dst:
                reader = csv.DictReader(src)
                if not reader.fieldnames:
                    return
                writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
                writer.writeheader()
                for row in reader:
                    num = (row.get("incident_number") or "").strip()
                    if num in coord_map:
                        lat, lon = coord_map[num]
                        row["latitude"] = str(lat)
                        row["longitude"] = str(lon)
                    writer.writerow(row)
            os.replace(tmp_path, self.csv_path)
        except Exception:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

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
            "hour_of_day",
            "day_of_week",
            "is_weekend",
            "is_rush_hour",
            "month",
            "is_school_day",
            "is_holiday",
            "nws_flash_flood_warning",
            "nws_severe_thunderstorm_warning",
            "nws_tornado_watch",
            "nws_active_alert_count",
            "road_type",
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
                   weather_sky_cover_pct, weather_observed_at, weather_source,
                   hour_of_day, day_of_week, is_weekend, is_rush_hour, month, is_school_day, is_holiday,
                   nws_flash_flood_warning, nws_severe_thunderstorm_warning,
                   nws_tornado_watch, nws_active_alert_count, road_type, created_at
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
                    "hour_of_day": row[16],
                    "day_of_week": row[17],
                    "is_weekend": row[18],
                    "is_rush_hour": row[19],
                    "month": row[20],
                    "is_school_day": row[21],
                    "is_holiday": row[22],
                    "nws_flash_flood_warning": row[23],
                    "nws_severe_thunderstorm_warning": row[24],
                    "nws_tornado_watch": row[25],
                    "nws_active_alert_count": row[26],
                    "road_type": row[27] or "",
                    "created_at": row[28] or "",
                }
            )
        return out


_ALL_CSV_COLS = [
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
    "hour_of_day",
    "day_of_week",
    "is_weekend",
    "is_rush_hour",
    "month",
    "is_school_day",
    "is_holiday",
    "nws_flash_flood_warning",
    "nws_severe_thunderstorm_warning",
    "nws_tornado_watch",
    "nws_active_alert_count",
    "road_type",
]


def ensure_csv_exists(filename: str) -> None:
    if os.path.exists(filename):
        _ensure_csv_columns(filename)
        return
    with open(filename, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_ALL_CSV_COLS)
        writer.writeheader()


def _ensure_csv_columns(filename: str) -> None:
    try:
        with open(filename, "r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
    except Exception:
        header = []

    needed = _ALL_CSV_COLS

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


def _safe_int(value) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None
