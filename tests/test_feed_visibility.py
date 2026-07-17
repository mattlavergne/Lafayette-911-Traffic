import json
import os
import re
import sqlite3
import tempfile
import unittest

from lafayette911.collector import (
    _classify_geocode_confidence,
    _filter_geocode_results,
)
from lafayette911.map_render import create_map_from_db
from lafayette911.state_store import StateStore


def _incident(num, location="W CONGRESS ST", cause="ACCIDENT",
              reported="01/15/2026 8:30 AM", **extra):
    inc = {
        "incident_number": num,
        "location": location,
        "cause": cause,
        "reported": reported,
        "assisting": "LPD",
    }
    inc.update(extra)
    return inc


class FeedVisibilityStoreTests(unittest.TestCase):
    """first_seen_at / last_seen_at / observation_count track how long an
    incident stays VISIBLE in the public feed — never response/clearance."""

    def _store(self, tmpdir):
        return StateStore(
            os.path.join(tmpdir, "incident_index.sqlite"),
            os.path.join(tmpdir, "traffic_incidents.csv"),
        )

    def test_insert_stamps_first_observation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(tmpdir)
            store.store_new_incidents([_incident("I1", latitude=30.2, longitude=-92.0,
                                                 geocode_confidence="precise")])
            row = store.conn.execute(
                "SELECT first_seen_at, last_seen_at, observation_count, geocode_confidence "
                "FROM incidents WHERE incident_number='I1'"
            ).fetchone()
            self.assertIsNotNone(row[0])
            self.assertEqual(row[0], row[1])       # first sighting == last sighting
            self.assertEqual(row[2], 1)            # the discovering sweep counts
            self.assertEqual(row[3], "precise")
            store.close()

    def test_touch_increments_and_flags_active_set_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(tmpdir)
            feed = [_incident("I1")]
            store.store_new_incidents(feed)

            touched, changed = store.touch_feed_observations(feed)
            self.assertEqual(touched, 1)
            self.assertTrue(changed)               # first sweep: no prior signature

            touched, changed = store.touch_feed_observations(feed)
            self.assertEqual(touched, 1)
            self.assertFalse(changed)              # same incidents listed → no re-render

            touched, changed = store.touch_feed_observations([])
            self.assertEqual(touched, 0)
            self.assertTrue(changed)               # incident left the feed → re-render

            row = store.conn.execute(
                "SELECT observation_count FROM incidents WHERE incident_number='I1'"
            ).fetchone()
            self.assertEqual(row[0], 3)            # insert + two sweeps that listed it
            self.assertIsNotNone(store.get_last_feed_sweep_at())
            store.close()

    def test_touch_matches_reformatted_feed_entries_via_content_index(self):
        """Feed formatting jitter changes the synthesized incident_number; the
        content index must still count the sighting against the stored row."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(tmpdir)
            store.store_new_incidents([_incident("I1")])
            jittered = _incident("DIFFERENT-ID", location="w  congress st")
            touched, _ = store.touch_feed_observations([jittered])
            self.assertEqual(touched, 1)
            store.close()

    def test_touch_backfills_first_seen_from_created_at(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(tmpdir)
            store.store_new_incidents([_incident("I1")])
            store.conn.execute(
                "UPDATE incidents SET first_seen_at=NULL, last_seen_at=NULL, "
                "observation_count=NULL, created_at='2026-01-01T00:00:00Z'"
            )
            store.conn.commit()
            store.touch_feed_observations([_incident("I1")])
            row = store.conn.execute(
                "SELECT first_seen_at, observation_count FROM incidents "
                "WHERE incident_number='I1'"
            ).fetchone()
            self.assertEqual(row[0], "2026-01-01T00:00:00Z")
            self.assertEqual(row[1], 1)
            store.close()


class GeocodeConfidenceTests(unittest.TestCase):
    def test_classification_levels(self):
        self.assertEqual(_classify_geocode_confidence("ROOFTOP", ["street_address"]), "precise")
        self.assertEqual(_classify_geocode_confidence("RANGE_INTERPOLATED", []), "precise")
        self.assertEqual(_classify_geocode_confidence("GEOMETRIC_CENTER", ["intersection"]), "intersection")
        self.assertEqual(_classify_geocode_confidence("GEOMETRIC_CENTER", ["establishment", "school"]), "place")
        self.assertEqual(_classify_geocode_confidence("GEOMETRIC_CENTER", ["plus_code"]), "approximate")

    def test_filter_attaches_confidence_and_cached_label(self):
        fresh = _incident("I1", latitude=30.2241, longitude=-92.0198,
                          address_components=[{"long_name": "Lafayette"}],
                          geo_location_type="ROOFTOP", geo_types=["street_address"])
        cached = _incident("I2", latitude=30.2241, longitude=-92.0198)
        rejected = _incident("I3", latitude=30.2241, longitude=-92.0198,
                             address_components=[{"long_name": "Lafayette"}],
                             geo_location_type="APPROXIMATE", geo_types=["locality"])
        _filter_geocode_results([fresh, cached, rejected])
        self.assertEqual(fresh.get("geocode_confidence"), "precise")
        self.assertEqual(cached.get("geocode_confidence"), "cached")
        self.assertIsNone(rejected.get("latitude"))
        self.assertNotIn("geocode_confidence", rejected)
        # Google metadata never leaks past the filter.
        for inc in (fresh, cached, rejected):
            self.assertNotIn("geo_location_type", inc)
            self.assertNotIn("geo_types", inc)


class SchemaV4ExportTests(unittest.TestCase):
    def test_db_export_carries_v4_fields_and_meta_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "incident_index.sqlite")
            store = StateStore(db_path, os.path.join(tmpdir, "traffic_incidents.csv"))
            feed = [_incident("I1", latitude=30.2241, longitude=-92.0198,
                              geocode_confidence="intersection")]
            store.store_new_incidents(feed)
            store.touch_feed_observations(feed)
            store.close()

            map_path = os.path.join(tmpdir, "m.html")
            datajs_path = os.path.join(tmpdir, "traffic_data.js")
            create_map_from_db(db_path, map_path, datajs_path, os.path.join(tmpdir, "osm"))

            datajs = open(datajs_path, encoding="utf-8").read()
            arr = json.loads(re.search(r"window\.INCIDENTS_DATA=(\[.*?\]);", datajs).group(1))
            self.assertEqual(len(arr), 1)
            row = arr[0]
            self.assertEqual(len(row), 31)
            self.assertEqual(row[27], "intersection")   # geocode confidence
            self.assertEqual(row[28], 0)                # visible minutes (single sweep)
            self.assertEqual(row[29], 1)                # listed in the latest sweep
            self.assertEqual(row[30], 2)                # insert + one touch sweep

            from lafayette911.map_render import DATA_SCHEMA_VERSION
            meta = json.load(open(os.path.join(tmpdir, "traffic_meta.json"), encoding="utf-8"))
            self.assertEqual(meta["schema_version"], DATA_SCHEMA_VERSION)

    def test_db_export_marks_departed_incidents_inactive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "incident_index.sqlite")
            store = StateStore(db_path, os.path.join(tmpdir, "traffic_incidents.csv"))
            gone = _incident("I1", latitude=30.2241, longitude=-92.0198)
            still = _incident("I2", location="JOHNSTON ST", reported="01/15/2026 9:00 AM",
                              latitude=30.21, longitude=-92.02)
            store.store_new_incidents([gone, still])
            store.touch_feed_observations([gone, still])
            store.touch_feed_observations([still])      # I1 left the feed
            store.close()

            datajs_path = os.path.join(tmpdir, "traffic_data.js")
            create_map_from_db(db_path, os.path.join(tmpdir, "m.html"), datajs_path,
                               os.path.join(tmpdir, "osm"))
            datajs = open(datajs_path, encoding="utf-8").read()
            arr = json.loads(re.search(r"window\.INCIDENTS_DATA=(\[.*?\]);", datajs).group(1))
            by_loc = {row[3]: row for row in arr}
            self.assertEqual(by_loc["W CONGRESS ST"][29], 0)
            self.assertEqual(by_loc["JOHNSTON ST"][29], 1)

    def test_older_db_without_v4_columns_still_renders(self):
        """Pointing the renderer at a pre-v4 DB copy must degrade gracefully
        (columns are added on the fly), never silently render an empty map."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "old.sqlite")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE incidents (incident_number TEXT PRIMARY KEY, location TEXT,"
                " cause TEXT, reported TEXT, assisting TEXT, latitude REAL, longitude REAL,"
                " created_at TEXT, geocode_attempts INTEGER, hour_of_day INTEGER,"
                " day_of_week INTEGER, is_school_day INTEGER, nws_flash_flood_warning INTEGER,"
                " nws_severe_thunderstorm_warning INTEGER, nws_tornado_watch INTEGER,"
                " road_type TEXT, is_holiday INTEGER,"
                " weather_temp_f REAL, weather_precip_prob REAL, weather_precip_in REAL,"
                " weather_wind_speed_mph REAL, weather_wind_gust_mph REAL,"
                " weather_visibility_mi REAL, weather_sky_cover_pct REAL,"
                " weather_observed_at TEXT, weather_source TEXT)"
            )
            conn.execute(
                "INSERT INTO incidents (incident_number, location, cause, reported, assisting,"
                " latitude, longitude, created_at) VALUES ('I1', 'W CONGRESS ST', 'ACCIDENT',"
                " '01/15/2026 8:30 AM', 'LPD', 30.2241, -92.0198, '2026-01-15T08:35:00Z')"
            )
            conn.commit()
            conn.close()

            datajs_path = os.path.join(tmpdir, "traffic_data.js")
            create_map_from_db(db_path, os.path.join(tmpdir, "m.html"), datajs_path,
                               os.path.join(tmpdir, "osm"))
            datajs = open(datajs_path, encoding="utf-8").read()
            arr = json.loads(re.search(r"window\.INCIDENTS_DATA=(\[.*?\]);", datajs).group(1))
            self.assertEqual(len(arr), 1)
            self.assertIsNone(arr[0][27])   # no confidence recorded
            self.assertEqual(arr[0][29], 0) # never observed in a sweep → not active


if __name__ == "__main__":
    unittest.main()
