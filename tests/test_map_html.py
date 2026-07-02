import os
import sqlite3
import tempfile
import unittest

from lafayette911.map_render import create_map_from_db
from lafayette911.map_template import render_map_html


class MapTemplateTests(unittest.TestCase):
    def test_tokens_are_substituted(self):
        html = render_map_html(30.2241, -92.0198, "traffic_data.js")
        self.assertNotIn("__CENTER_LAT__", html)
        self.assertNotIn("__CENTER_LNG__", html)
        self.assertNotIn("__YEAR_OPTIONS__", html)
        self.assertNotIn("__DAY_OPTIONS__", html)
        self.assertNotIn("__GENERATED_AT__", html)
        self.assertIn("30.224100", html)
        self.assertIn('src="traffic_data.js"', html)
        # __DATAJS_SRC__ appears in JS as a const too — must be substituted.
        self.assertNotIn("__DATAJS_SRC__", html)

    def test_core_ui_and_data_contract_present(self):
        html = render_map_html(30.2241, -92.0198, "traffic_data.js")
        for needle in (
            "<!DOCTYPE html>",
            'id="map"',
            'id="sidebar"',
            'id="roadSearch"',
            'id="causeSelect"',
            'id="chkPoints"',
            'id="legendChips"',
            'id="panelAnalytics"',
            'id="panelFeed"',
            "window.INCIDENTS_DATA",
            "IDX_CREATED_AT = 24",
            "leaflet@1.9.4",
            "api.weather.gov",
        ):
            self.assertIn(needle, html)

    def test_balanced_braces_in_script(self):
        # A coarse syntax sanity check on the embedded JS/CSS.
        html = render_map_html(30.0, -92.0, "traffic_data.js")
        self.assertEqual(html.count("{"), html.count("}"))
        self.assertEqual(html.count("("), html.count(")"))

    def test_create_map_from_db_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "incident_index.sqlite")
            map_path = os.path.join(tmpdir, "traffic_map.html")
            datajs_path = os.path.join(tmpdir, "traffic_data.js")
            osm_cache = os.path.join(tmpdir, "osm_cache")

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE incidents (
                    incident_number TEXT PRIMARY KEY,
                    location TEXT, cause TEXT, reported TEXT, assisting TEXT,
                    latitude REAL, longitude REAL,
                    weather_temp_f REAL, weather_precip_prob REAL, weather_precip_in REAL,
                    weather_wind_speed_mph REAL, weather_wind_gust_mph REAL,
                    weather_visibility_mi REAL, weather_sky_cover_pct REAL,
                    weather_observed_at TEXT, weather_source TEXT,
                    hour_of_day INTEGER, day_of_week INTEGER, is_school_day INTEGER,
                    nws_flash_flood_warning INTEGER, nws_severe_thunderstorm_warning INTEGER,
                    nws_tornado_watch INTEGER, road_type TEXT, created_at TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO incidents (incident_number, location, cause, reported, assisting,"
                " latitude, longitude, hour_of_day, day_of_week, is_school_day, created_at)"
                " VALUES ('INC1', 'W CONGRESS ST', 'ACCIDENT', '01/15/2026 8:30 AM', 'LPD',"
                " 30.2241, -92.0198, 8, 3, 1, '2026-01-15T08:35:00Z')"
            )
            conn.execute(
                "INSERT INTO incidents (incident_number, location, cause, reported, assisting,"
                " latitude, longitude, created_at)"
                " VALUES ('INC2', 'PENDING RD', 'VEHICLE FIRE', '01/16/2026 9:00 AM', '', NULL, NULL,"
                " '2026-01-16T09:05:00Z')"
            )
            conn.commit()
            conn.close()

            create_map_from_db(db_path, map_path, datajs_path, osm_cache)

            self.assertTrue(os.path.exists(map_path))
            self.assertTrue(os.path.exists(datajs_path))

            with open(datajs_path, encoding="utf-8") as handle:
                datajs = handle.read()
            self.assertIn("window.INCIDENTS_DATA=[", datajs)
            self.assertIn("W CONGRESS ST", datajs)
            self.assertIn("window.INCIDENTS_UNLOCATED_COUNT=1", datajs)

            with open(map_path, encoding="utf-8") as handle:
                html = handle.read()
            self.assertIn('src="traffic_data.js"', html)
            self.assertIn('id="sidebar"', html)
            self.assertNotIn("__CENTER_LAT__", html)


if __name__ == "__main__":
    unittest.main()
