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


if __name__ == "__main__":
    unittest.main()
