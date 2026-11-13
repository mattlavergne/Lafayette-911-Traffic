"""Canonical-corridor normalization: every spelling of a road must group
under one corridor id, and intersections must credit BOTH roads."""

import unittest

from lafayette911.corridors import corridor_ids, normalize_corridor


class NormalizeCorridorTests(unittest.TestCase):
    def test_house_numbers_removed(self):
        self.assertEqual(normalize_corridor("3500 AMBASSADOR CAFFERY PKWY"),
                         "AMBASSADOR CAFFERY PKWY")
        self.assertEqual(normalize_corridor("120 W CONGRESS ST"), "W CONGRESS ST")

    def test_block_ranges_removed(self):
        self.assertEqual(normalize_corridor("3500-3600 JOHNSTON ST"), "JOHNSTON ST")
        self.assertEqual(normalize_corridor("100 BLK OF MOSS ST"), "MOSS ST")
        self.assertEqual(normalize_corridor("100 BLOCK OF MOSS ST"), "MOSS ST")

    def test_unit_qualifiers_removed(self):
        self.assertEqual(normalize_corridor("200 GUILBEAU RD APT 14"), "GUILBEAU RD")
        self.assertEqual(normalize_corridor("300 PINHOOK RD SUITE B"), "PINHOOK RD")
        self.assertEqual(normalize_corridor("400 BERTRAND DR UNIT 2"), "BERTRAND DR")
        self.assertEqual(normalize_corridor("500 CAMERON ST BLDG 3"), "CAMERON ST")
        self.assertEqual(normalize_corridor("600 SURREY ST LOT 9"), "SURREY ST")

    def test_suffix_normalization(self):
        self.assertEqual(normalize_corridor("JOHNSTON STREET"), "JOHNSTON ST")
        self.assertEqual(normalize_corridor("VEROT SCHOOL ROAD"), "VEROT SCHOOL RD")
        self.assertEqual(normalize_corridor("CAMELLIA BOULEVARD"), "CAMELLIA BLVD")
        self.assertEqual(normalize_corridor("LOUISIANA AVENUE"), "LOUISIANA AVE")
        self.assertEqual(normalize_corridor("AMBASSADOR CAFFERY PARKWAY"),
                         "AMBASSADOR CAFFERY PKWY")
        self.assertEqual(normalize_corridor("BERTRAND DRIVE"), "BERTRAND DR")

    def test_directional_prefix_normalization(self):
        self.assertEqual(normalize_corridor("WEST CONGRESS STREET"), "W CONGRESS ST")
        self.assertEqual(normalize_corridor("NORTH UNIVERSITY AVENUE"), "N UNIVERSITY AVE")

    def test_travel_direction_tokens_removed(self):
        self.assertEqual(normalize_corridor("NB AMBASSADOR CAFFERY PKWY"),
                         "AMBASSADOR CAFFERY PKWY")
        self.assertEqual(normalize_corridor("JOHNSTON ST SB"), "JOHNSTON ST")

    def test_highway_identifiers_preserved(self):
        self.assertEqual(normalize_corridor("I-10 E"), "I-10")
        self.assertEqual(normalize_corridor("I 10 WB"), "I-10")
        self.assertEqual(normalize_corridor("INTERSTATE 49"), "I-49")
        self.assertEqual(normalize_corridor("US 90"), "US 90")
        self.assertEqual(normalize_corridor("US HWY 90"), "US 90")
        self.assertEqual(normalize_corridor("LA 182"), "LA 182")
        self.assertEqual(normalize_corridor("LA HWY 182"), "LA 182")

    def test_alias_table(self):
        # Suffix-less common names route to their canonical corridor.
        self.assertEqual(normalize_corridor("4500 AMBASSADOR CAFFERY"),
                         "AMBASSADOR CAFFERY PKWY")
        self.assertEqual(normalize_corridor("AMB CAFFERY"), "AMBASSADOR CAFFERY PKWY")
        self.assertEqual(normalize_corridor("KALISTE SALOOM"), "KALISTE SALOOM RD")

    def test_empty_and_junk(self):
        self.assertEqual(normalize_corridor(""), "")
        self.assertEqual(normalize_corridor(None), "")
        self.assertEqual(normalize_corridor("3500"), "")
        self.assertEqual(normalize_corridor("MM 103"), "")

    def test_city_and_state_tags_are_not_corridors(self):
        """Regression: the feed sometimes appends (or IS) the municipality,
        and "LAFAYETTE LA" was ranking as the #1 "corridor" in analytics."""
        self.assertEqual(normalize_corridor("LAFAYETTE LA"), "")
        self.assertEqual(normalize_corridor("LAFAYETTE, LA"), "")
        self.assertEqual(normalize_corridor("LAFAYETTE"), "")
        self.assertEqual(normalize_corridor("YOUNGSVILLE LA"), "")
        self.assertEqual(normalize_corridor("LAFAYETTE PARISH"), "")
        # Trailing tags strip; the road remains and still groups.
        self.assertEqual(normalize_corridor("MOSS ST LAFAYETTE LA"), "MOSS ST")
        self.assertEqual(normalize_corridor("100 JOHNSTON STREET, LAFAYETTE, LA"), "JOHNSTON ST")
        self.assertEqual(normalize_corridor("INTERSTATE 10 LAFAYETTE LA"), "I-10")
        # Roads NAMED after places keep their names.
        self.assertEqual(normalize_corridor("LAFAYETTE ST"), "LAFAYETTE ST")
        self.assertEqual(normalize_corridor("YOUNGSVILLE HWY"), "YOUNGSVILLE HWY")

    def test_city_only_locations_yield_no_corridor_ids(self):
        self.assertEqual(corridor_ids("LAFAYETTE LA"), [])
        self.assertEqual(corridor_ids("MOSS ST LAFAYETTE LA & JOHNSTON ST"), ["MOSS ST", "JOHNSTON ST"])


class CorridorIdsTests(unittest.TestCase):
    def test_required_grouping_examples(self):
        """The three spec examples must group under ONE Ambassador Caffery
        corridor, and the intersection must also credit Kaliste Saloom."""
        a = corridor_ids("3500 AMBASSADOR CAFFERY PKWY")
        b = corridor_ids("4500 AMBASSADOR CAFFERY")
        c = corridor_ids("AMBASSADOR CAFFERY PKWY AT KALISTE SALOOM RD")
        self.assertEqual(a, ["AMBASSADOR CAFFERY PKWY"])
        self.assertEqual(b, ["AMBASSADOR CAFFERY PKWY"])
        self.assertIn("AMBASSADOR CAFFERY PKWY", c)
        self.assertIn("KALISTE SALOOM RD", c)
        self.assertEqual(len(c), 2)

    def test_intersection_separators(self):
        for sep in (" AT ", " & ", "&", " / ", "/", " @ ", " NEAR ", " AND "):
            ids = corridor_ids("JOHNSTON ST" + sep + "BERTRAND DR")
            self.assertEqual(ids, ["JOHNSTON ST", "BERTRAND DR"], "sep=" + repr(sep))

    def test_same_road_twice_counts_once(self):
        self.assertEqual(corridor_ids("JOHNSTON ST & JOHNSTON ST"), ["JOHNSTON ST"])
        # Variant spellings of the same road also dedupe.
        self.assertEqual(
            corridor_ids("AMBASSADOR CAFFERY / AMBASSADOR CAFFERY PKWY"),
            ["AMBASSADOR CAFFERY PKWY"],
        )

    def test_highway_with_mile_marker(self):
        ids = corridor_ids("I-10 E & MILE MARKER 103")
        self.assertEqual(ids, ["I-10"])

    def test_empty(self):
        self.assertEqual(corridor_ids(""), [])
        self.assertEqual(corridor_ids(None), [])


class BroussardRoadTests(unittest.TestCase):
    def test_e_broussard_is_a_road_not_the_town(self):
        self.assertEqual(corridor_ids("600 E BROUSSARD/DUHON RD"), ["E BROUSSARD RD", "DUHON RD"])
        self.assertEqual(corridor_ids("EAST BROUSSARD ROAD"), ["E BROUSSARD RD"])
        self.assertEqual(corridor_ids("W BROUSSARD"), ["W BROUSSARD RD"])

    def test_town_tags_are_still_stripped(self):
        self.assertEqual(corridor_ids("BROUSSARD"), [])
        self.assertEqual(corridor_ids("123 MAIN ST BROUSSARD LA"), ["MAIN ST"])
        self.assertEqual(corridor_ids("200 W SCOTT"), [])


if __name__ == "__main__":
    unittest.main()


class LiveFeedFormatTests(unittest.TestCase):
    """The feed's real shape: '<block> <road>/<cross street>\\n <city>, LA'."""

    def test_parish_abbreviation_is_not_a_road(self):
        from lafayette911.corridors import corridor_ids
        ids = corridor_ids("1400 YOUNG ST/YOUNG\n                        LAF PARISH, LA")
        self.assertEqual(ids, ["YOUNG ST", "YOUNG"])
        self.assertEqual(corridor_ids("100 MAIN ST/\n   LAF PARISH, LA"), ["MAIN ST"])

    def test_highway_direction_tags_collapse(self):
        from lafayette911.corridors import normalize_corridor
        self.assertEqual(normalize_corridor("10300 W I-10"), "I-10")
        self.assertEqual(normalize_corridor("S I-49"), "I-49")
        self.assertEqual(normalize_corridor("5100 US HWY 90 EAST"), "US 90")
        self.assertEqual(normalize_corridor("I-10 SOUTH FRONTAGE RD"), "I-10 SOUTH FRONTAGE RD")

    def test_block_address_counts_once_on_its_own_road(self):
        from lafayette911.corridors import incident_corridors
        self.assertEqual(
            incident_corridors("1400 W PINHOOK RD/W BENDEL RD\n      LAFAYETTE, LA"), ["PINHOOK RD"])
        self.assertEqual(
            incident_corridors("3232 AMBASSADOR CAFFERY PKWY/AMBASSADOR CAFFERY BLVD\n LAFAYETTE, LA"),
            ["AMBASSADOR CAFFERY PKWY"])

    def test_true_intersection_still_counts_both_roads(self):
        from lafayette911.corridors import incident_corridors
        self.assertEqual(
            incident_corridors("AMBASSADOR CAFFERY PKWY AT KALISTE SALOOM RD"),
            ["AMBASSADOR CAFFERY PKWY", "KALISTE SALOOM RD"])
