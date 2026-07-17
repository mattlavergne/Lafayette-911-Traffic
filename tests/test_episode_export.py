import json
import os
import re
import tempfile
import unittest

from lafayette911.map_render import create_map_from_csv, create_map_from_db
from lafayette911.state_store import StateStore


def _read_rows(datajs_path):
    datajs = open(datajs_path, encoding="utf-8").read()
    return json.loads(re.search(r"window\.INCIDENTS_DATA=(\[.*?\]);", datajs).group(1))


class DbEpisodeExportTests(unittest.TestCase):
    def test_duplicate_listing_folds_into_primary_with_extras_at_32(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "incident_index.sqlite")
            store = StateStore(db_path, os.path.join(tmpdir, "traffic_incidents.csv"))
            store.store_new_incidents([
                {"incident_number": "A", "location": "W CONGRESS ST/JOHNSTON",
                 "cause": "TRAFFIC ACCIDENT MINOR", "reported": "07/16/2026 8:02 AM",
                 "assisting": "LPD", "latitude": 30.2241, "longitude": -92.0198},
                {"incident_number": "B", "location": "W CONGRESS ST/JOHNSTON",
                 "cause": "HIT AND RUN", "reported": "07/16/2026 8:05 AM",
                 "assisting": "LPD", "latitude": 30.2241, "longitude": -92.0198},
                {"incident_number": "C", "location": "AMBASSADOR CAFFERY PKWY",
                 "cause": "STALLED VEHICLE", "reported": "07/16/2026 9:00 AM",
                 "assisting": "LPD", "latitude": 30.21, "longitude": -92.02},
            ])
            store.close()

            datajs_path = os.path.join(tmpdir, "traffic_data.js")
            create_map_from_db(db_path, os.path.join(tmpdir, "m.html"), datajs_path,
                               os.path.join(tmpdir, "osm"))
            rows = _read_rows(datajs_path)

        # Two exported rows: the folded episode (hit-and-run primary) + the
        # unrelated stalled vehicle. The raw DB kept all three records.
        self.assertEqual(len(rows), 2)
        by_cause = {r[4]: r for r in rows}
        self.assertIn("HIT AND RUN", by_cause)
        self.assertNotIn("TRAFFIC ACCIDENT MINOR", by_cause)
        primary = by_cause["HIT AND RUN"]
        self.assertEqual(len(primary), 33)
        self.assertIsNone(primary[31])                       # TC-history slot padded
        self.assertEqual(primary[32], [["TRAFFIC ACCIDENT MINOR", "07/16/2026 8:02 AM"]])
        self.assertEqual(len(by_cause["STALLED VEHICLE"]), 31)   # no extras

    def test_unlocated_secondary_never_counts_as_locating(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "incident_index.sqlite")
            store = StateStore(db_path, os.path.join(tmpdir, "traffic_incidents.csv"))
            store.store_new_incidents([
                {"incident_number": "A", "location": "W CONGRESS ST/JOHNSTON",
                 "cause": "TRAFFIC ACCIDENT MAJOR", "reported": "07/16/2026 8:02 AM",
                 "assisting": "LPD", "latitude": 30.2241, "longitude": -92.0198},
                # Same crash re-listed; still waiting on geocoding.
                {"incident_number": "B", "location": "W CONGRESS ST/JOHNSTON",
                 "cause": "RESCUE SQUAD NEEDED", "reported": "07/16/2026 8:04 AM",
                 "assisting": "LPD"},
            ])
            store.close()

            datajs_path = os.path.join(tmpdir, "traffic_data.js")
            create_map_from_db(db_path, os.path.join(tmpdir, "m.html"), datajs_path,
                               os.path.join(tmpdir, "osm"))
            datajs = open(datajs_path, encoding="utf-8").read()

        self.assertIn("window.INCIDENTS_UNLOCATED_COUNT=0;", datajs)
        rows = _read_rows(datajs_path) if False else None  # datajs already read
        arr = json.loads(re.search(r"window\.INCIDENTS_DATA=(\[.*?\]);", datajs).group(1))
        self.assertEqual(len(arr), 1)
        self.assertEqual(arr[0][4], "TRAFFIC ACCIDENT MAJOR")


class CsvEpisodeExportTests(unittest.TestCase):
    def test_csv_render_folds_duplicates_too(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            datajs_path = os.path.join(tmpdir, "traffic_data.js")
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write("location,cause,reported,assisting,incident_number,latitude,longitude\n")
                handle.write("W CONGRESS ST/JOHNSTON,TRAFFIC ACCIDENT MINOR,07/16/2026 8:02 AM,LPD,A,30.2241,-92.0198\n")
                handle.write("W CONGRESS ST/JOHNSTON,HIT AND RUN,07/16/2026 8:05 AM,LPD,B,30.2241,-92.0198\n")
                handle.write("JOHNSTON ST,STALLED VEHICLE,07/16/2026 9:00 AM,LPD,C,30.21,-92.02\n")

            create_map_from_csv(csv_path, os.path.join(tmpdir, "m.html"), datajs_path,
                                os.path.join(tmpdir, "osm"))
            rows = _read_rows(datajs_path)

        self.assertEqual(len(rows), 2)
        by_cause = {r[4]: r for r in rows}
        primary = by_cause["HIT AND RUN"]
        self.assertEqual(primary[32], [["TRAFFIC ACCIDENT MINOR", "07/16/2026 8:02 AM"]])


class DigestEpisodeTests(unittest.TestCase):
    def test_digest_counts_episodes_not_listings(self):
        from datetime import datetime

        from lafayette911.daily_digest import collect_digest_stats

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "incident_index.sqlite")
            store = StateStore(db_path, os.path.join(tmpdir, "traffic_incidents.csv"))
            store.store_new_incidents([
                {"incident_number": "A", "location": "W CONGRESS ST/JOHNSTON",
                 "cause": "TRAFFIC ACCIDENT MINOR", "reported": "07/16/2026 8:02 AM",
                 "assisting": "LPD", "latitude": 30.2241, "longitude": -92.0198},
                {"incident_number": "B", "location": "W CONGRESS ST/JOHNSTON",
                 "cause": "HIT AND RUN", "reported": "07/16/2026 8:05 AM",
                 "assisting": "LPD", "latitude": 30.2241, "longitude": -92.0198},
                {"incident_number": "C", "location": "AMBASSADOR CAFFERY PKWY",
                 "cause": "STALLED VEHICLE", "reported": "07/16/2026 9:00 AM",
                 "assisting": "LPD", "latitude": 30.21, "longitude": -92.02},
            ])
            store.close()
            stats = collect_digest_stats(db_path, now=datetime.utcnow())

        self.assertEqual(stats["new_total"], 2)               # 2 real events
        self.assertEqual(stats["duplicates_folded_24h"], 1)   # 1 extra listing
        self.assertEqual(stats["by_category"].get("Accidents"), 1)


if __name__ == "__main__":
    unittest.main()
