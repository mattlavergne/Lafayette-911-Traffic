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


    def test_route_builder_does_not_emit_corridors_for_drawn_paths(self):
        html = render_map_html(30.2241, -92.0198, "traffic_data.js")
        self.assertIn("A drawn route is section-precise by PATH", html)
        self.assertIn("if (roadSet.length && rbPath.length < 2)", html)
        self.assertNotIn("id=\"rbRadius\"", html)
        self.assertNotIn("_RADIUS_M=", html)

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
                    geocode_attempts INTEGER, is_holiday INTEGER
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


class CsvEnrichmentExportTests(unittest.TestCase):
    def test_nws_and_enrichment_flags_survive_csv_render(self):
        """Regression: the CSV render path hardcoded the NWS alert / hour /
        day-of-week fields to None, so those filters matched nothing on
        CSV-rendered deployments even though the archive carried the columns."""
        from lafayette911.map_render import create_map_from_csv

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            datajs_path = os.path.join(tmpdir, "traffic_data.js")
            cols = (
                "location,cause,reported,assisting,incident_number,latitude,longitude,"
                "hour_of_day,day_of_week,is_school_day,is_holiday,"
                "nws_flash_flood_warning,nws_severe_thunderstorm_warning,nws_tornado_watch,road_type"
            )
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write(cols + "\n")
                handle.write(
                    "JOHNSTON ST,ACCIDENT,07/04/2026 8:30 AM,LPD,INC1,30.2241,-92.0198,"
                    "8,4,0,1,1,1,0,primary\n"
                )

            create_map_from_csv(csv_path, os.path.join(tmpdir, "m.html"), datajs_path, os.path.join(tmpdir, "osm"))
            datajs = open(datajs_path, encoding="utf-8").read()
            # The incident row must carry the flags (not None): flash-flood=1,
            # severe-storm=1, tornado=0, is_holiday=1 at their indices.
            import json, re
            arr = json.loads(re.search(r"window\.INCIDENTS_DATA=(\[.*?\]);", datajs).group(1))
            self.assertEqual(len(arr), 1)
            row = arr[0]
            self.assertEqual(row[17], 8)    # hour_of_day
            self.assertEqual(row[20], 1)    # nws_flash_flood_warning
            self.assertEqual(row[21], 1)    # nws_severe_thunderstorm_warning
            self.assertEqual(row[22], 0)    # nws_tornado_watch
            self.assertEqual(row[25], 1)    # is_holiday


class TrafficControlHistoryTests(unittest.TestCase):
    def test_tc_groups_carry_daily_history_and_never_hide_accidents(self):
        """Routine TRAFFIC CONTROL stays collapsed to ONE point per location
        (so daily school control can't flood the map), but now exports its
        per-day recurrence history at index 31 — and a real accident at the
        same location remains its own full row."""
        import json as _json
        import re

        from lafayette911.map_render import create_map_from_csv

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            datajs_path = os.path.join(tmpdir, "traffic_data.js")
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "location,cause,reported,assisting,incident_number,latitude,longitude\n"
                )
                # Same school, three TC events across two days.
                handle.write("100 SCHOOL RD,TRAFFIC CONTROL,01/15/2026 7:30 AM,LPD,T1,30.2241,-92.0198\n")
                handle.write("100 SCHOOL RD,TRAFFIC CONTROL,01/15/2026 3:00 PM,LPD,T2,30.2241,-92.0198\n")
                handle.write("100 SCHOOL RD,TRAFFIC CONTROL,01/16/2026 7:30 AM,LPD,T3,30.2241,-92.0198\n")
                # A real accident at the SAME location.
                handle.write("100 SCHOOL RD,ACCIDENT,01/16/2026 8:00 AM,LPD,A1,30.2241,-92.0198\n")

            create_map_from_csv(csv_path, os.path.join(tmpdir, "m.html"), datajs_path, os.path.join(tmpdir, "osm"))
            datajs = open(datajs_path, encoding="utf-8").read()
            arr = _json.loads(re.search(r"window\.INCIDENTS_DATA=(\[.*?\]);", datajs).group(1))

            tc = [r for r in arr if str(r[4]).upper() == "TRAFFIC CONTROL"]
            acc = [r for r in arr if str(r[4]).upper() == "ACCIDENT"]
            self.assertEqual(len(tc), 1)      # collapsed to one point
            self.assertEqual(len(acc), 1)     # the accident is NOT swallowed
            self.assertEqual(tc[0][7], 3)     # occurrence count preserved
            # Per-day history at index 31 (v4): 2 on the 15th, 1 on the 16th.
            self.assertEqual(tc[0][31], [["2026-01-15", 2], ["2026-01-16", 1]])
            # Non-TC rows carry the v4 fields (27-30) but no history slot.
            self.assertEqual(len(acc[0]), 31)


class MetaAndCorridorExportTests(unittest.TestCase):
    def test_meta_file_and_corridor_field(self):
        """Every render writes traffic_meta.json (version hash, timestamp,
        count, schema) and appends canonical corridor ids at row index 26."""
        import json as _json
        import re

        from lafayette911.map_render import DATA_SCHEMA_VERSION, create_map_from_csv

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "traffic_incidents.csv")
            datajs_path = os.path.join(tmpdir, "traffic_data.js")
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "location,cause,reported,assisting,incident_number,latitude,longitude\n"
                )
                handle.write(
                    "3500 AMBASSADOR CAFFERY PKWY,ACCIDENT,01/15/2026 8:30 AM,POLICE,I1,30.2241,-92.0198\n"
                )
                handle.write(
                    "AMBASSADOR CAFFERY PKWY AT KALISTE SALOOM RD,ACCIDENT,01/15/2026 9:30 AM,POLICE FIRE,I2,30.21,-92.03\n"
                )

            create_map_from_csv(csv_path, os.path.join(tmpdir, "m.html"), datajs_path, os.path.join(tmpdir, "osm"))

            # Corridor ids ride at index 26 without disturbing earlier fields.
            datajs = open(datajs_path, encoding="utf-8").read()
            arr = _json.loads(re.search(r"window\.INCIDENTS_DATA=(\[.*?\]);", datajs).group(1))
            by_loc = {row[3]: row for row in arr}
            self.assertEqual(
                by_loc["3500 AMBASSADOR CAFFERY PKWY"][26], ["AMBASSADOR CAFFERY PKWY"]
            )
            self.assertEqual(
                sorted(by_loc["AMBASSADOR CAFFERY PKWY AT KALISTE SALOOM RD"][26]),
                ["AMBASSADOR CAFFERY PKWY", "KALISTE SALOOM RD"],
            )

            meta_path = os.path.join(tmpdir, "traffic_meta.json")
            self.assertTrue(os.path.exists(meta_path))
            meta = _json.load(open(meta_path, encoding="utf-8"))
            self.assertEqual(meta["schema_version"], DATA_SCHEMA_VERSION)
            self.assertEqual(meta["incident_count"], 2)
            self.assertTrue(meta["data_version"])
            self.assertIn("generated_at", meta)

            # An unchanged re-render must NOT dirty the meta file (that would
            # force a pointless Pages publish every cycle).
            before = open(meta_path, encoding="utf-8").read()
            create_map_from_csv(csv_path, os.path.join(tmpdir, "m.html"), datajs_path, os.path.join(tmpdir, "osm"))
            self.assertEqual(open(meta_path, encoding="utf-8").read(), before)


class PublicReleaseFileTests(unittest.TestCase):
    def test_license_and_notices_exist(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        lic = open(os.path.join(root, "LICENSE"), encoding="utf-8").read()
        self.assertIn("MIT License", lic)
        self.assertIn("2026", lic)
        notices = open(os.path.join(root, "THIRD_PARTY_NOTICES.md"), encoding="utf-8").read()
        for needle in ("Leaflet", "OpenStreetMap", "CARTO", "National Weather Service", "GitHub Pages"):
            self.assertIn(needle, notices)

    def test_page_carries_disclaimers_and_attribution(self):
        html = render_map_html(30.2241, -92.0198, "traffic_data.js")
        for needle in (
            "aboutModal",
            "not affiliated with or",
            "call 911",
            "openstreetmap.org/copyright",
            "carto.com/attribution",
        ):
            self.assertIn(needle, html)


class BasemapTileTests(unittest.TestCase):
    """CARTO now watermarks unkeyed tiles with "API KEY REQUIRED" (and still
    answers 200, so the page cannot detect it), so the default basemap must be
    keyless OpenStreetMap."""

    def test_default_build_is_keyless_openstreetmap(self):
        saved = os.environ.pop("LAF911_CARTO_API_KEY", None)
        try:
            html = render_map_html(30.2241, -92.0198, "traffic_data.js")
        finally:
            if saved is not None:
                os.environ["LAF911_CARTO_API_KEY"] = saved
        self.assertNotIn("__CARTO_API_KEY__", html)
        self.assertIn('const CARTO_KEY = "";', html)
        self.assertIn("https://tile.openstreetmap.org/{z}/{x}/{y}.png", html)

    def test_carto_key_is_used_when_configured(self):
        html = render_map_html(30.2241, -92.0198, "traffic_data.js", carto_api_key="Key-123_x.y")
        self.assertIn('const CARTO_KEY = "Key-123_x.y";', html)
        self.assertIn("basemaps.cartocdn.com", html)

    def test_malformed_carto_key_is_dropped_not_injected(self):
        html = render_map_html(30.2241, -92.0198, "traffic_data.js", carto_api_key='"; alert(1); //')
        self.assertIn('const CARTO_KEY = "";', html)
        self.assertNotIn("alert(1)", html)


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
