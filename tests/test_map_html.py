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
                    nws_tornado_watch INTEGER, road_type TEXT, created_at TEXT,
                    geocode_attempts INTEGER
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
            # Retired after 3 failed attempts: "unmappable", not "locating".
            conn.execute(
                "INSERT INTO incidents (incident_number, location, cause, reported, assisting,"
                " latitude, longitude, created_at, geocode_attempts)"
                " VALUES ('INC3', 'GIVEN UP LN', 'ACCIDENT', '01/17/2026 9:00 AM', '', NULL, NULL,"
                " '2026-01-17T09:05:00Z', 3)"
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
            # Pending incidents are exported so the feed can show them.
            self.assertIn("window.INCIDENTS_UNLOCATED_LIST=", datajs)
            self.assertIn("PENDING RD", datajs)
            # Retired incidents are counted separately and NOT listed as locating.
            self.assertIn("window.INCIDENTS_UNMAPPABLE_COUNT=1", datajs)
            self.assertNotIn("GIVEN UP LN", datajs)

            with open(map_path, encoding="utf-8") as handle:
                html = handle.read()
            self.assertIn('src="traffic_data.js"', html)
            self.assertIn('id="sidebar"', html)
            self.assertNotIn("__CENTER_LAT__", html)


class CsvUnlocatedTests(unittest.TestCase):
    def test_csv_render_surfaces_unlocated_incidents(self):
        """The CSV render path used to silently drop coordless rows without
        even counting them — the page never knew an incident was missing."""
        from lafayette911.map_render import create_map_from_csv

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            map_path = os.path.join(tmpdir, "traffic_map.html")
            datajs_path = os.path.join(tmpdir, "traffic_data.js")
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "location,cause,reported,assisting,incident_number,latitude,longitude\n"
                )
                handle.write(
                    "W CONGRESS ST,ACCIDENT,01/15/2026 8:30 AM,LPD,INC1,30.2241,-92.0198\n"
                )
                handle.write(
                    "306 ERASTE LANDRY RD,RESCUE SQUAD NEEDED,01/16/2026 9:00 AM,LFD,INC2,,\n"
                )

            create_map_from_csv(csv_path, map_path, datajs_path, os.path.join(tmpdir, "osm"))

            with open(datajs_path, encoding="utf-8") as handle:
                datajs = handle.read()
            self.assertIn("window.INCIDENTS_UNLOCATED_COUNT=1", datajs)
            self.assertIn("window.INCIDENTS_UNLOCATED_LIST=", datajs)
            self.assertIn("306 ERASTE LANDRY RD", datajs)
            self.assertIn("RESCUE SQUAD NEEDED", datajs)

    def test_csv_render_survives_empty_cells_anywhere(self):
        """Regression: pandas string-dtype columns yield pd.NA for empty CSV
        cells, and pd.NA in boolean context raises "boolean value of NA is
        ambiguous" — which crashed the whole render cycle in production.
        The observed crash site was _normalize_location(pd.NA) inside
        _collapse_traffic_control, i.e. a LOCATED row with an empty location
        cell, but every text column must tolerate empty cells."""
        from lafayette911.map_render import create_map_from_csv

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            map_path = os.path.join(tmpdir, "traffic_map.html")
            datajs_path = os.path.join(tmpdir, "traffic_data.js")
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "location,cause,reported,assisting,incident_number,latitude,longitude\n"
                )
                handle.write(
                    "W CONGRESS ST,ACCIDENT,01/15/2026 8:30 AM,LPD,INC1,30.2241,-92.0198\n"
                )
                # LOCATED rows with empty text cells (the production crash):
                handle.write(",TRAFFIC CONTROL,01/15/2026 9:30 AM,LPD,INC2,30.23,-92.03\n")
                handle.write("MOSS ST,,01/15/2026 10:00 AM,,INC3,30.22,-92.02\n")
                # Unlocated rows with empty cells.
                handle.write("PINHOOK RD,STALLED VEHICLE,01/16/2026 9:00 AM,,INC4,,\n")
                handle.write("VEROT SCHOOL RD,ACCIDENT,,,INC5,,\n")

            create_map_from_csv(csv_path, map_path, datajs_path, os.path.join(tmpdir, "osm"))

            with open(datajs_path, encoding="utf-8") as handle:
                datajs = handle.read()
            self.assertIn("window.INCIDENTS_UNLOCATED_COUNT=2", datajs)
            self.assertIn("PINHOOK RD", datajs)
            self.assertIn("VEROT SCHOOL RD", datajs)
            self.assertIn("MOSS ST", datajs)
            # pd.NA must never leak into the page as the string "<NA>".
            self.assertNotIn("<NA>", datajs)


class ConfigDefaultTests(unittest.TestCase):
    def test_geocode_defaults(self):
        from lafayette911.main import load_config

        saved = {}
        for key in ("LAF911_GEOCODE_MAX_REQUESTS_PER_24H", "LAF911_GEOCODE_RETRY_UNLOCATED_ENABLED"):
            saved[key] = os.environ.pop(key, None)
        try:
            cfg = load_config(base_dir="/tmp")
            self.assertEqual(cfg.geocode_max_requests_per_24h, 100)
            self.assertTrue(cfg.geocode_retry_unlocated_enabled)
        finally:
            for key, val in saved.items():
                if val is not None:
                    os.environ[key] = val


if __name__ == "__main__":
    unittest.main()
