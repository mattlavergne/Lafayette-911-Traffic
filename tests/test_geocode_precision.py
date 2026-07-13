"""Geocoder precision: Google's area fallbacks (city centroid, road
midpoints) must never become map points, and historical fallback pile-ups
get repaired once."""

import os
import tempfile
import unittest

from lafayette911.collector import (
    _acceptable_geocode_precision,
    _filter_geocode_results,
)
from lafayette911.state_store import StateStore

# Downtown Lafayette locality centroid — where every unresolvable address
# used to land (the Rue Vermilion / S Buchanan pile-up).
CITY_CENTROID = (30.2240897, -92.0198427)

LAFAYETTE_COMPONENT = [{"long_name": "Lafayette", "types": ["locality", "political"]}]


class PrecisionRuleTests(unittest.TestCase):
    def test_precise_results_accepted(self):
        self.assertTrue(_acceptable_geocode_precision("ROOFTOP", ["street_address"]))
        self.assertTrue(_acceptable_geocode_precision("RANGE_INTERPOLATED", ["street_address"]))
        self.assertTrue(_acceptable_geocode_precision("GEOMETRIC_CENTER", ["intersection"]))
        # Schools/businesses report GEOMETRIC_CENTER but are real places.
        self.assertTrue(_acceptable_geocode_precision("GEOMETRIC_CENTER", ["establishment", "point_of_interest", "school"]))
        # Old cache entries carry no metadata: benefit of the doubt.
        self.assertTrue(_acceptable_geocode_precision("", []))
        self.assertTrue(_acceptable_geocode_precision(None, None))

    def test_area_fallbacks_rejected(self):
        # The city-centroid fallback: EVERY unresolvable address landed here.
        self.assertFalse(_acceptable_geocode_precision("APPROXIMATE", ["locality", "political"]))
        self.assertFalse(_acceptable_geocode_precision("APPROXIMATE", ["postal_code"]))
        self.assertFalse(_acceptable_geocode_precision("APPROXIMATE", ["neighborhood", "political"]))
        # A whole-road midpoint is not a specific address.
        self.assertFalse(_acceptable_geocode_precision("GEOMETRIC_CENTER", ["route"]))
        self.assertFalse(_acceptable_geocode_precision("APPROXIMATE", []))


class FilterIntegrationTests(unittest.TestCase):
    def _inc(self, loc, lt, types):
        return {
            "location": loc, "latitude": CITY_CENTROID[0], "longitude": CITY_CENTROID[1],
            "address_components": list(LAFAYETTE_COMPONENT),
            "geo_location_type": lt, "geo_types": types,
        }

    def test_locality_fallback_rejected_and_cache_scrubbed(self):
        cache = {"HWY 90": CITY_CENTROID, "1200 W CONGRESS ST": CITY_CENTROID}
        incidents = [
            # Unresolvable highway ref → locality fallback → must be dropped.
            self._inc("HWY 90", "APPROXIMATE", ["locality", "political"]),
            # Real street address at the same coords → kept.
            self._inc("1200 W CONGRESS ST", "ROOFTOP", ["street_address"]),
            # Whole-road midpoint → dropped.
            self._inc("EVANGELINE THWY", "GEOMETRIC_CENTER", ["route"]),
        ]
        out = _filter_geocode_results(incidents, location_cache=cache)
        self.assertIsNone(out[0]["latitude"])
        self.assertEqual(out[1]["latitude"], CITY_CENTROID[0])
        self.assertIsNone(out[2]["latitude"])
        # Rejected coords must leave the cycle cache so later duplicates
        # don't silently reuse them; accepted ones stay.
        self.assertNotIn("HWY 90", cache)
        self.assertIn("1200 W CONGRESS ST", cache)
        # Metadata never leaks into storage.
        for inc in out:
            self.assertNotIn("geo_location_type", inc)
            self.assertNotIn("geo_types", inc)
            self.assertNotIn("address_components", inc)

    def test_cache_hits_without_metadata_still_pass_bounds_check(self):
        inc = {"location": "MOSS ST", "latitude": CITY_CENTROID[0], "longitude": CITY_CENTROID[1]}
        out = _filter_geocode_results([inc])
        self.assertEqual(out[0]["latitude"], CITY_CENTROID[0])


class FallbackPurgeTests(unittest.TestCase):
    def test_purges_shared_points_once_and_spares_real_clusters(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(os.path.join(tmp, "t.sqlite"), os.path.join(tmp, "t.csv"))
            try:
                def insert(num, loc, lat, lng, attempts=1):
                    store.conn.execute(
                        "INSERT INTO incidents (incident_number, location, cause, reported,"
                        " assisting, latitude, longitude, created_at, geocode_attempts)"
                        " VALUES (?, ?, 'ACCIDENT', '', '', ?, ?, '2026-01-01T00:00:00Z', ?)",
                        (num, loc, lat, lng, attempts),
                    )

                # 9 DIFFERENT addresses piled on the city centroid → fallback.
                for i in range(9):
                    insert("F%d" % i, "BAD ADDR %d" % i, *CITY_CENTROID)
                # A school: 20 incidents, but all the SAME location → genuine.
                for i in range(20):
                    insert("S%d" % i, "100 SCHOOL RD", 30.19, -92.05)
                # An intersection with 3 spelling variants → genuine.
                for i, v in enumerate(["A ST & B ST", "B ST & A ST", "A ST AT B ST"]):
                    insert("X%d" % i, v, 30.18, -92.04)
                store.conn.commit()

                purged = store.purge_shared_fallback_coordinates(min_distinct_locations=8)
                self.assertEqual(len(purged), 1)
                lat, lng, distinct, n = purged[0]
                self.assertEqual((round(lat, 7), round(lng, 7)),
                                 (round(CITY_CENTROID[0], 7), round(CITY_CENTROID[1], 7)))
                self.assertEqual(distinct, 9)
                self.assertEqual(n, 9)

                # The fallback rows are re-queued (coords gone, attempts reset)…
                requeued = store.conn.execute(
                    "SELECT COUNT(*) FROM incidents WHERE latitude IS NULL AND geocode_attempts = 0"
                ).fetchone()[0]
                self.assertEqual(requeued, 9)
                # …while the school and intersection keep their coordinates.
                kept = store.conn.execute(
                    "SELECT COUNT(*) FROM incidents WHERE latitude IS NOT NULL"
                ).fetchone()[0]
                self.assertEqual(kept, 23)

                # Second run is a no-op (restart-safe).
                self.assertEqual(store.purge_shared_fallback_coordinates(min_distinct_locations=8), [])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
