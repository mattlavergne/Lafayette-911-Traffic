"""Hourly observed weather, the feed-check roll-up, agency change tracking,
the DOTD road import and their export to the page."""

import importlib.util
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

from lafayette911 import weather_log
from lafayette911.map_render import create_map_from_db
from lafayette911.map_template import render_map_html
from lafayette911.state_store import StateStore

_SPEC = importlib.util.spec_from_file_location(
    "build_road_stats",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "build_road_stats.py"),
)
road_stats = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(road_stats)

IEM = """station,valid,tmpf,p01i,vsby,sknt,wxcodes
LFT,2026-11-12 00:53,65.00,0.00,10.00,5.00,M
LFT,2026-11-12 01:20,64.00,0.05,3.00,9.00,-RA
LFT,2026-11-12 01:53,64.00,0.42,2.00,12.00,+RA
LFT,2026-11-12 02:53,63.00,T,0.50,3.00,FG
LFT,2026-11-12 03:53,M,M,M,M,M
"""


class WeatherLogTests(unittest.TestCase):
    def test_iem_rows_merge_to_one_row_per_hour(self):
        hours = weather_log.merge_hours(weather_log.parse_iem_csv(IEM))
        self.assertEqual(sorted(hours), ["2026-11-12T00", "2026-11-12T01", "2026-11-12T02", "2026-11-12T03"])
        one = hours["2026-11-12T01"]
        self.assertEqual(one["precip_in"], 0.42)        # the hour's total, not the special's 0.05
        self.assertEqual(one["visibility_mi"], 2.0)     # worst visibility
        self.assertIn("+RA", one["wx"])
        self.assertIsNone(hours["2026-11-12T03"]["precip_in"])

    def test_condition_codes(self):
        self.assertEqual(weather_log.condition_code(0.0, 10, ""), "d")
        self.assertEqual(weather_log.condition_code(0.05, 5, ""), "r")
        self.assertEqual(weather_log.condition_code(0.42, 2, "+RA"), "h")
        self.assertEqual(weather_log.condition_code(0.0, 10, "-RA"), "r")   # rain seen, nothing measured yet
        self.assertEqual(weather_log.condition_code(0.001, 0.5, "FG"), "f")
        self.assertEqual(weather_log.condition_code(None, None, ""), "-")

    def test_export_is_one_character_per_hour_with_gaps_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "db.sqlite")
            conn = sqlite3.connect(db)
            hours = weather_log.merge_hours(weather_log.parse_iem_csv(IEM))
            del hours["2026-11-12T02"]
            del hours["2026-11-12T03"]
            hours["2026-11-12T04"] = {"hour_local": "2026-11-12T04", "temp_f": 60, "precip_in": 0.0,
                                      "visibility_mi": 10, "wind_mph": 3, "wx": "", "source": "t"}
            self.assertEqual(weather_log.store_hours(conn, hours), 3)
            conn.close()
            self.assertEqual(weather_log.export_hours(db), {"start": "2026-11-12T00", "codes": "dh--d"})

    def test_archive_values_replace_live_ones_but_not_the_reverse(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(os.path.join(tmp, "db.sqlite"))
            row = {"hour_local": "2026-11-12T05", "temp_f": 60, "precip_in": 0.0, "visibility_mi": 10,
                   "wind_mph": 3, "wx": "", "source": "NWS"}
            weather_log.store_hours(conn, {row["hour_local"]: row})
            weather_log.store_hours(conn, {row["hour_local"]: dict(row, precip_in=0.2, source="NWS")})
            self.assertEqual(conn.execute("SELECT precip_in FROM weather_hourly").fetchone()[0], 0.0)
            weather_log.store_hours(conn, {row["hour_local"]: dict(row, precip_in=0.3, source="IEM ASOS")}, replace=True)
            self.assertEqual(conn.execute("SELECT precip_in, source FROM weather_hourly").fetchone(), (0.3, "IEM ASOS"))

    def test_nws_observation_without_precip_field_counts_as_dry(self):
        body = {"features": [{"properties": {
            "timestamp": "2026-11-13T15:53:00+00:00", "temperature": {"value": 16.0},
            "precipitationLastHour": {"value": None}, "visibility": {"value": 16093.44},
            "windSpeed": {"value": 18.5}, "presentWeather": []}}]}
        row = weather_log.parse_nws_observations(body)[0]
        self.assertEqual(row["precip_in"], 0.0)
        self.assertAlmostEqual(row["temp_f"], 60.8)
        self.assertAlmostEqual(row["visibility_mi"], 10.0)

    def test_update_never_raises_and_tries_once_per_hour(self):
        calls = []

        class Session:
            def get(self, *a, **k):
                calls.append(a[0])
                raise RuntimeError("network down")

        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(os.path.join(tmp, "db.sqlite"), os.path.join(tmp, "x.csv"))
            try:
                self.assertEqual(weather_log.maybe_update_weather_log(store, Session()), 0)
                first = len(calls)
                self.assertEqual(first, 2)                       # live feed + archive
                self.assertEqual(weather_log.maybe_update_weather_log(store, Session()), 0)
                self.assertEqual(len(calls), first)              # nothing more this hour
            finally:
                store.close()


class FeedCheckAndChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(os.path.join(self.tmp.name, "db.sqlite"), os.path.join(self.tmp.name, "x.csv"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_feed_checks_roll_up_to_one_row_per_hour(self):
        at = datetime(2026, 11, 13, 8, 5)
        for ok in (True, True, False):
            self.store.record_feed_check(ok, now=at)
        self.store.record_feed_check(True, now=datetime(2026, 11, 13, 9, 0))
        rows = self.store.conn.execute("SELECT hour_local, ok, failed FROM feed_checks ORDER BY 1").fetchall()
        self.assertEqual(rows, [("2026-11-13T08", 2, 1), ("2026-11-13T09", 1, 0)])

    def test_agencies_accumulate_and_each_addition_is_logged(self):
        inc = {"incident_number": "A1", "location": "100 MAIN ST/OAK", "cause": "TRAFFIC ACCIDENT MINOR",
               "reported": "11/13/2026 08:32", "assisting": "POLICE"}
        self.store.store_new_incidents([inc])
        self.assertEqual(self.store.record_assisting_changes([inc]), 0)
        self.assertEqual(self.store.record_assisting_changes([dict(inc, assisting="POLICE FIRE")]), 1)
        # A unit dropping off the listing still responded: nothing is removed.
        self.assertEqual(self.store.record_assisting_changes([dict(inc, assisting="FIRE")]), 0)
        self.assertEqual(
            self.store.conn.execute("SELECT assisting FROM incidents").fetchone()[0], "POLICE FIRE")
        self.assertEqual(
            self.store.conn.execute("SELECT field, old_value, new_value FROM incident_changes").fetchall(),
            [("assisting", "POLICE", "POLICE FIRE")])


class RoadStatsTests(unittest.TestCase):
    def test_divided_road_is_not_double_counted_and_ramps_are_dropped(self):
        roads = [
            {"RouteID": "999_LA 3073_1_1_010", "FromMeasure": 0.0, "ToMeasure": 2.0, "FullName": "Ambassador Caffery Pkwy"},
            {"RouteID": "999_LA 3073_2_2_010", "FromMeasure": 0.0, "ToMeasure": 2.0, "FullName": "Ambassador Caffery Pkwy"},
            {"RouteID": "999_LA 3073_1_A_010", "FromMeasure": 0.0, "ToMeasure": 0.3, "FullName": "Ambassador Caffery Pkwy"},
            {"RouteID": "055_OAK ST_1_1_010", "FromMeasure": 0.0, "ToMeasure": 1.0, "FullName": "Oak St"},
            {"RouteID": "055_PROPOSED RD_1_1_010", "FromMeasure": 0.0, "ToMeasure": 5.0, "FullName": "Proposed Rd Extension"},
        ]
        aadt = [
            {"RouteID": "999_LA 3073_1_1_010", "FromMeasure": 0.0, "ToMeasure": 1.0, "AADT": 40000.0},
            {"RouteID": "999_LA 3073_1_1_010", "FromMeasure": 1.0, "ToMeasure": 2.0, "AADT": 20000.0},
            {"RouteID": "055_OAK ST_1_1_010", "FromMeasure": 0.0, "ToMeasure": 0.2, "AADT": 900.0},
        ]
        stats = road_stats.build(roads, aadt)
        self.assertEqual(stats["AMBASSADOR CAFFERY PKWY"], [2.0, 60000])
        # Only a fifth of Oak St was counted: its traffic is unknown, not guessed.
        self.assertEqual(stats["OAK ST"], [1.0, 0])
        self.assertNotIn("PROPOSED RD EXT", stats)
        self.assertEqual(len(stats), 2)

    def test_shipped_table_matches_the_incident_road_ids(self):
        from lafayette911.map_render import _load_road_stats
        roads = _load_road_stats()
        for name in ("AMBASSADOR CAFFERY PKWY", "JOHNSTON ST", "I-10", "EVANGELINE THRUWAY"):
            self.assertIn(name, roads)
            self.assertGreater(roads[name][0], 3)
            self.assertGreater(roads[name][1], 100000)


class ExportTests(unittest.TestCase):
    def test_side_tables_are_written_and_page_reads_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, csv_path = os.path.join(tmp, "db.sqlite"), os.path.join(tmp, "x.csv")
            store = StateStore(db, csv_path)
            store.store_new_incidents([
                {"incident_number": "N%d" % i, "location": "%d00 JOHNSTON ST/OAK\n   LAFAYETTE, LA" % i,
                 "cause": "TRAFFIC ACCIDENT MINOR", "reported": "11/12/2026 08:%02d" % i, "assisting": "POLICE",
                 "latitude": 30.2 + i / 1000.0, "longitude": -92.02}
                for i in range(1, 13)
            ])
            store.record_feed_check(True, now=datetime(2026, 11, 12, 8, 0))
            weather_log.store_hours(store.conn, weather_log.merge_hours(weather_log.parse_iem_csv(IEM)))
            store.close()
            out = os.path.join(tmp, "traffic_data.js")
            create_map_from_db(db, os.path.join(tmp, "map.html"), out, os.path.join(tmp, "osm"))
            text = open(out, encoding="utf-8").read()
            self.assertIn('window.ROAD_STATS={"JOHNSTON ST":[', text)
            self.assertIn('window.WEATHER_HOURS={"start":"2026-11-12T00","codes":"dhf-"}', text)
            self.assertIn('window.FEED_HOURS={"2026-11-12":1}', text)
            self.assertIn("window.CHANGE_STATS={}", text)

    def test_page_has_the_new_analyses(self):
        html = render_map_html(30.22, -92.02, "traffic_data.js")
        for needle in ("function ivSecondary(", "function ivWeatherEffect(", "function ivSun(",
                       "function renderTodayNorm(", 'id="todayNorm"', "Most dangerous for the traffic they carry",
                       "Who responds", "Holidays, festivals and school", "Beyond crashes", "window.ROAD_STATS"):
            self.assertIn(needle, html)


if __name__ == "__main__":
    unittest.main()
