import unittest

from lafayette911.episodes import (
    cause_severity,
    find_episode_duplicates,
    parse_reported_any,
)


def rec(rid, cause, reported, location="W CONGRESS ST/JOHNSTON LAFAYETTE, LA", located=True):
    return {"id": rid, "location": location, "cause": cause,
            "reported": reported, "located": located}


class EpisodeGroupingTests(unittest.TestCase):
    def test_hit_and_run_pair_folds_into_one_episode(self):
        """The user's exact case: HIT AND RUN + TRAFFIC ACCIDENT MINOR at the
        same location minutes apart is ONE event; the hit-and-run label wins."""
        secondary, extras = find_episode_duplicates([
            rec("A", "TRAFFIC ACCIDENT MINOR", "07/16/2026 8:02 AM"),
            rec("B", "HIT AND RUN", "07/16/2026 8:05 AM"),
        ])
        self.assertEqual(secondary, {"A"})
        self.assertEqual([x["cause"] for x in extras["B"]], ["TRAFFIC ACCIDENT MINOR"])

    def test_reclassification_major_beats_minor(self):
        secondary, extras = find_episode_duplicates([
            rec("A", "TRAFFIC ACCIDENT MINOR", "07/16/2026 2:06 PM"),
            rec("B", "TRAFFIC ACCIDENT MAJOR", "07/16/2026 2:06 PM"),
        ])
        self.assertEqual(secondary, {"A"})
        self.assertIn("B", extras)

    def test_located_record_wins_primary_even_if_less_severe(self):
        """The mapped point must never vanish because the severe record is
        still waiting on geocoding."""
        secondary, extras = find_episode_duplicates([
            rec("A", "TRAFFIC ACCIDENT MAJOR", "07/16/2026 9:00 AM", located=False),
            rec("B", "TRAFFIC ACCIDENT MINOR", "07/16/2026 9:03 AM", located=True),
        ])
        self.assertEqual(secondary, {"A"})
        self.assertEqual([x["cause"] for x in extras["B"]], ["TRAFFIC ACCIDENT MAJOR"])

    def test_far_apart_in_time_stays_separate(self):
        secondary, _ = find_episode_duplicates([
            rec("A", "TRAFFIC ACCIDENT MINOR", "07/16/2026 8:00 AM"),
            rec("B", "TRAFFIC ACCIDENT MAJOR", "07/16/2026 9:30 AM"),
        ])
        self.assertEqual(secondary, set())

    def test_different_locations_stay_separate(self):
        secondary, _ = find_episode_duplicates([
            rec("A", "HIT AND RUN", "07/16/2026 8:00 AM"),
            rec("B", "TRAFFIC ACCIDENT MINOR", "07/16/2026 8:02 AM",
                location="AMBASSADOR CAFFERY PKWY/KALISTE SALOOM"),
        ])
        self.assertEqual(secondary, set())

    def test_whitespace_jitter_in_location_still_groups(self):
        secondary, _ = find_episode_duplicates([
            rec("A", "TRAFFIC ACCIDENT MINOR", "07/16/2026 8:00 AM",
                location="100 W CONGRESS ST/\n            LAFAYETTE, LA"),
            rec("B", "HIT AND RUN", "07/16/2026 8:04 AM",
                location="100 W CONGRESS ST/ LAFAYETTE, LA"),
        ])
        self.assertEqual(secondary, {"A"})

    def test_traffic_control_never_groups(self):
        secondary, _ = find_episode_duplicates([
            rec("A", "TRAFFIC CONTROL", "07/16/2026 7:30 AM"),
            rec("B", "TRAFFIC ACCIDENT MINOR", "07/16/2026 7:31 AM"),
        ])
        self.assertEqual(secondary, set())

    def test_three_record_chain_folds_to_one_primary(self):
        secondary, extras = find_episode_duplicates([
            rec("A", "STALLED VEHICLE", "07/16/2026 5:00 PM"),
            rec("B", "TRAFFIC ACCIDENT NO INJURI", "07/16/2026 5:08 PM"),
            rec("C", "TRAFFIC ACCIDENT W/INJURIES", "07/16/2026 5:15 PM"),
        ])
        self.assertEqual(secondary, {"A", "B"})
        self.assertEqual(len(extras["C"]), 2)

    def test_same_cause_relisting_folds(self):
        secondary, _ = find_episode_duplicates([
            rec("A", "TRAFFIC ACCIDENT MINOR", "07/16/2026 8:00 AM"),
            rec("B", "TRAFFIC ACCIDENT MINOR", "07/16/2026 8:06 AM"),
        ])
        self.assertEqual(secondary, {"B"})   # earliest report is primary

    def test_window_zero_disables_grouping(self):
        secondary, _ = find_episode_duplicates([
            rec("A", "TRAFFIC ACCIDENT MINOR", "07/16/2026 8:02 AM"),
            rec("B", "HIT AND RUN", "07/16/2026 8:03 AM"),
        ], window_min=0)
        self.assertEqual(secondary, set())

    def test_severity_ordering(self):
        order = [
            "TRAFFIC ACCIDENT FATALITY",
            "TRAFFIC ACCIDENT W/INJURIES",
            "HIT/RUN SUSPECT LEAVING",
            "TRAFFIC ACCIDENT MAJOR",
            "TRAFFIC ACCIDENT UNK INJURYS",
            "TRAFFIC ACCIDENT MINOR",
            "TRAFFIC ACCIDENT NO INJURI",
            "RESCUE SQUAD NEEDED",
            "STALLED VEHICLE",
        ]
        ranks = [cause_severity(c) for c in order]
        self.assertEqual(ranks, sorted(ranks, reverse=True))

    def test_parse_both_reported_formats(self):
        self.assertIsNotNone(parse_reported_any("07/16/2026 1:06 PM"))
        self.assertIsNotNone(parse_reported_any("12/13/2024 22:55"))
        self.assertIsNone(parse_reported_any("TRAFFIC CONTROL (aggregated) | first: x"))


if __name__ == "__main__":
    unittest.main()
