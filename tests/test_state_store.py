import os
import tempfile
import unittest

from lafayette911.state_store import StateStore


class StateStoreTests(unittest.TestCase):
    def test_dedupe_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            db_path = os.path.join(tmpdir, "incident_index.sqlite")

            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write("location,cause,reported,assisting,incident_number,latitude,longitude\n")
                handle.write("LocA,CauseA,2024-01-01 10:00,AssistA,INC1,30.0,-92.0\n")

            store = StateStore(db_path, csv_path)

            incidents = [
                {
                    "location": "LocA",
                    "cause": "CauseA",
                    "reported": "2024-01-01 10:00",
                    "assisting": "AssistA",
                    "incident_number": "INC1",
                    "latitude": 30.0,
                    "longitude": -92.0,
                },
                {
                    "location": "LocB",
                    "cause": "CauseB",
                    "reported": "2024-01-02 11:00",
                    "assisting": "AssistB",
                    "incident_number": "INC2",
                    "latitude": 30.1,
                    "longitude": -92.1,
                },
            ]

            new_incidents = store.store_new_incidents(incidents)
            self.assertEqual(len(new_incidents), 1)
            self.assertEqual(new_incidents[0]["incident_number"], "INC2")

            all_rows = store.read_all_incidents()
            incident_numbers = {row["incident_number"] for row in all_rows}
            self.assertIn("INC1", incident_numbers)
            self.assertIn("INC2", incident_numbers)

            store.close()


    def test_sync_from_csv_even_when_db_not_empty(self):
        """CSV rows missing from DB should be added even if the DB is not empty.

        This is the core fix for the bug where incidents collected after a
        partial DB reset/restore were present in the CSV but invisible on the
        map (which renders from the DB).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            db_path = os.path.join(tmpdir, "incident_index.sqlite")

            # Pre-populate the CSV with two incidents.
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "location,cause,reported,assisting,incident_number,latitude,longitude\n"
                )
                handle.write("LocA,CauseA,2026-02-10 10:00,AssistA,INC_OLD,30.0,-92.0\n")
                handle.write("LocB,CauseB,2026-02-20 11:00,AssistB,INC_NEW,30.1,-92.1\n")

            # First StateStore initialisation: seeded INC_OLD into the DB, but
            # simulate the pre-fix behaviour by manually deleting INC_NEW from
            # the DB after creation (mimics a partial DB state).
            store1 = StateStore(db_path, csv_path)
            store1.conn.execute(
                "DELETE FROM incident_index WHERE incident_number = 'INC_NEW'"
            )
            store1.conn.execute(
                "DELETE FROM incidents WHERE incident_number = 'INC_NEW'"
            )
            store1.conn.commit()
            store1.close()

            # Verify the gap: DB now lacks INC_NEW even though it's in the CSV.
            import sqlite3
            conn = sqlite3.connect(db_path)
            count = conn.execute(
                "SELECT COUNT(1) FROM incident_index WHERE incident_number = 'INC_NEW'"
            ).fetchone()[0]
            conn.close()
            self.assertEqual(count, 0)

            # Re-open StateStore — with the fix, it should re-sync from the CSV
            # and restore INC_NEW.
            store2 = StateStore(db_path, csv_path)
            all_rows = store2.read_all_incidents()
            incident_numbers = {row["incident_number"] for row in all_rows}
            self.assertIn("INC_OLD", incident_numbers)
            self.assertIn(
                "INC_NEW",
                incident_numbers,
                "INC_NEW was in the CSV but not the DB; re-opening StateStore "
                "should have synced it so it appears on the map.",
            )
            store2.close()

    def test_geocode_quota_enforced_over_rolling_24h_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            db_path = os.path.join(tmpdir, "incident_index.sqlite")
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write("location,cause,reported,assisting,incident_number,latitude,longitude\n")

            store = StateStore(db_path, csv_path)
            approved = store.reserve_geocode_requests(60, max_requests_per_24h=75)
            self.assertEqual(approved, 60)
            approved = store.reserve_geocode_requests(30, max_requests_per_24h=75)
            self.assertEqual(approved, 15)
            approved = store.reserve_geocode_requests(1, max_requests_per_24h=75)
            self.assertEqual(approved, 0)
            remaining = store.get_remaining_geocode_requests(max_requests_per_24h=75)
            self.assertEqual(remaining, 0)
            store.close()

    def test_legacy_geocode_attempt_reset_runs_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            db_path = os.path.join(tmpdir, "incident_index.sqlite")
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write("location,cause,reported,assisting,incident_number,latitude,longitude\n")
                handle.write("LocA,CauseA,2026-01-01 10:00,AssistA,INC1,,\n")

            store = StateStore(db_path, csv_path)
            store.conn.execute("UPDATE incidents SET geocode_attempts = 3 WHERE incident_number = 'INC1'")
            store.conn.commit()
            store.close()

            # Re-open and verify the one-time migration does not keep resetting.
            store2 = StateStore(db_path, csv_path)
            row = store2.conn.execute(
                "SELECT geocode_attempts FROM incidents WHERE incident_number = 'INC1'"
            ).fetchone()
            self.assertEqual(row[0], 3)
            store2.close()


if __name__ == "__main__":
    unittest.main()


class ContentDedupeTests(unittest.TestCase):
    def test_feed_formatting_jitter_cannot_duplicate_an_incident(self):
        """The bottom-line guarantee: the same real-world incident re-listed
        with different whitespace/casing (which changes the synthesized
        incident_number) must NOT be stored twice."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            db_path = os.path.join(tmpdir, "incident_index.sqlite")
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write("location,cause,reported,assisting,incident_number,latitude,longitude\n")

            store = StateStore(db_path, csv_path)
            original = {
                "location": "W CONGRESS ST & AMBASSADOR CAFFERY PKWY",
                "cause": "ACCIDENT",
                "reported": "07/06/2026 10:00 AM",
                "assisting": "LPD",
                "incident_number": "N1",
            }
            jittered = {
                "location": "W  Congress St &  Ambassador Caffery Pkwy",  # extra spaces + case
                "cause": "Accident",
                "reported": "07/06/2026  10:00 AM",
                "assisting": "LPD",
                "incident_number": "N2",  # different synthesized id!
            }
            stored = store.store_new_incidents([original])
            self.assertEqual(len(stored), 1)
            stored2 = store.store_new_incidents([jittered])
            self.assertEqual(stored2, [], "jittered duplicate must be rejected")

            count = store.conn.execute("SELECT COUNT(1) FROM incidents").fetchone()[0]
            self.assertEqual(count, 1)
            store.close()

    def test_in_batch_duplicates_collapse(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write("location,cause,reported,assisting,incident_number,latitude,longitude\n")
            store = StateStore(os.path.join(tmpdir, "db.sqlite"), csv_path)
            batch = [
                {"location": "MOSS ST", "cause": "ACCIDENT", "reported": "07/06/2026 9:00 AM",
                 "assisting": "", "incident_number": "A"},
                {"location": "Moss  St", "cause": "accident", "reported": "07/06/2026 9:00 AM",
                 "assisting": "", "incident_number": "B"},
            ]
            self.assertEqual(len(store.store_new_incidents(batch)), 1)
            store.close()

    def test_content_index_backfilled_for_existing_rows(self):
        """Rows stored before the content index existed are protected after
        the one-time backfill migration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            db_path = os.path.join(tmpdir, "db.sqlite")
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write("location,cause,reported,assisting,incident_number,latitude,longitude\n")
                handle.write("PINHOOK RD,ACCIDENT,07/01/2026 8:00 AM,LPD,OLD1,30.2,-92.0\n")

            store = StateStore(db_path, csv_path)  # seeds from CSV + backfills index
            jittered = {
                "location": "Pinhook  Rd", "cause": "Accident",
                "reported": "07/01/2026 8:00 AM", "assisting": "LPD",
                "incident_number": "NEW-ID",
            }
            self.assertEqual(store.store_new_incidents([jittered]), [])
            store.close()
