"""Route alerts against the feed AS IT REALLY IS: 24-hour report times with
no AM/PM, multi-line "block road/cross street\\n city" locations, and the
collector's feed-visibility stamps. The earlier suites only ever used
"8:32 AM"-style times, which is how a parser that rejected every real
incident shipped unnoticed."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from lafayette911.daily_digest import DigestConfig, _reported_hour
from lafayette911.route_alerts import (
    Route,
    RouteConfig,
    _parse_reported_local,
    _subject_for,
    find_route_incidents,
    maybe_send_route_alerts,
    render_route_email,
)

PATH = [(30.200, -92.020), (30.205, -92.020), (30.210, -92.020)]
ROUTE = Route(index=1, name="To work", corridors=set(), corridor_labels=[],
              depart_minutes=7 * 60 + 20, days=set(range(7)), path=PATH, radius_m=100)
LOC = "3232 AMBASSADOR CAFFERY PKWY/CURRAN LN\n                        LAFAYETTE, LA"


def feed_time(dt):
    return dt.strftime("%m/%d/%Y %H:%M")          # exactly what the feed sends


def utc_iso(local_dt):
    return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Db:
    """A database shaped like the collector's, including visibility stamps."""

    def __init__(self, tmp):
        self.path = os.path.join(tmp, "db.sqlite")
        conn = sqlite3.connect(self.path)
        conn.execute(
            """CREATE TABLE incidents (incident_number TEXT PRIMARY KEY, location TEXT, cause TEXT,
            reported TEXT, assisting TEXT, latitude REAL, longitude REAL, created_at TEXT,
            weather_precip_prob REAL, weather_precip_in REAL,
            first_seen_at TEXT, last_seen_at TEXT)"""
        )
        conn.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT)")
        old = datetime.now() - timedelta(days=30)
        for i, lat in enumerate((30.2005, 30.2050, 30.2095)):
            conn.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("H%d" % i, "%d000 AMBASSADOR CAFFERY PKWY/X\n   LAFAYETTE, LA" % (i + 1),
                          "TRAFFIC ACCIDENT MINOR", feed_time(old), "", lat, -92.0200, "", None, None,
                          None, None))
        conn.commit()
        conn.close()

    def add(self, num, reported_dt, first_seen=None, last_seen=None, lat=30.2050, lng=-92.0201,
            loc=LOC, cause="TRAFFIC ACCIDENT MAJOR"):
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (num, loc, cause, feed_time(reported_dt), "POLICE", lat, lng, "", None, None,
                      utc_iso(first_seen) if first_seen else None,
                      utc_iso(last_seen) if last_seen else None))
        conn.commit()
        conn.close()

    def sweep(self, local_dt):
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT OR REPLACE INTO app_meta VALUES ('last_feed_sweep_at', ?)", (utc_iso(local_dt),))
        conn.commit()
        conn.close()


class TimestampFormatTests(unittest.TestCase):
    def test_24_hour_feed_time_parses(self):
        self.assertEqual(_parse_reported_local("11/13/2026 08:32"), datetime(2026, 11, 13, 8, 32))
        self.assertEqual(_parse_reported_local("11/13/2026 17:05"), datetime(2026, 11, 13, 17, 5))

    def test_12_hour_forms_still_parse(self):
        self.assertEqual(_parse_reported_local("11/13/2026 5:05 PM"), datetime(2026, 11, 13, 17, 5))
        self.assertEqual(_parse_reported_local("11/13/2026 12:10 AM"), datetime(2026, 11, 13, 0, 10))

    def test_date_without_time_is_not_a_time(self):
        self.assertIsNone(_parse_reported_local("11/13/2026"))

    def test_digest_hour_reads_24_hour_time(self):
        self.assertEqual(_reported_hour("11/13/2026 17:05"), 17)
        self.assertEqual(_reported_hour("11/13/2026 5:05 PM"), 17)


class LiveFeedMatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Db(self.tmp.name)
        self.now = datetime.now().replace(microsecond=0)

    def tearDown(self):
        self.tmp.cleanup()

    def find(self, **kw):
        return find_route_incidents(self.db.path, ROUTE, 90, now=self.now, **kw)

    def test_crash_in_real_feed_format_alerts(self):
        self.db.add("A", self.now - timedelta(minutes=12))
        found = self.find()
        self.assertEqual([f["cause"] for f in found], ["TRAFFIC ACCIDENT MAJOR"])
        self.assertEqual(found[0]["minutes_ago"], 12)
        self.assertNotIn("\n", found[0]["location"])

    def test_old_incident_still_in_feed_alerts(self):
        sweep = self.now - timedelta(minutes=2)
        self.db.add("A", self.now - timedelta(hours=3), first_seen=self.now - timedelta(hours=3),
                    last_seen=sweep)
        self.db.sweep(sweep)
        found = self.find()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["status"], "active")
        self.assertIn("still listed in the 911 feed", render_route_email(ROUTE, found, self.now, 90))

    def test_old_incident_gone_from_feed_does_not_alert(self):
        self.db.add("A", self.now - timedelta(hours=3), first_seen=self.now - timedelta(hours=3),
                    last_seen=self.now - timedelta(hours=2))
        self.db.sweep(self.now - timedelta(minutes=2))
        self.assertEqual(self.find(), [])

    def test_recent_incident_that_left_the_feed_is_listed_as_cleared(self):
        self.db.add("A", self.now - timedelta(minutes=40), first_seen=self.now - timedelta(minutes=40),
                    last_seen=self.now - timedelta(minutes=20))
        self.db.sweep(self.now - timedelta(minutes=2))
        found = self.find()
        self.assertEqual(found[0]["status"], "cleared")
        html = render_route_email(ROUTE, found, self.now, 90)
        self.assertIn("Your route looks clear", html)
        self.assertIn("likely cleared", html)
        self.assertIn("route clear", _subject_for(ROUTE, found, now=self.now))

    def test_stale_feed_is_not_reported_as_clear(self):
        self.db.sweep(self.now - timedelta(minutes=55))
        info = {}
        found = self.find(feed_info=info)
        self.assertTrue(info["stale"])
        self.assertEqual(info["age_min"], 55)
        html = render_route_email(ROUTE, found, self.now, 90, feed_info=info)
        self.assertIn("Couldn&#x27;t check your route", html)
        self.assertNotIn("looks clear", html)
        self.assertIn("couldn't check", _subject_for(ROUTE, found, now=self.now, feed_info=info))

    def test_fresh_feed_is_not_flagged(self):
        self.db.sweep(self.now - timedelta(minutes=4))
        info = {}
        self.find(feed_info=info)
        self.assertFalse(info["stale"])

    def test_building_pin_set_back_from_the_road_still_matches(self):
        # 150 m off the centre line, no cross street in the text.
        self.db.add("A", self.now - timedelta(minutes=5), lng=-92.02156,
                    loc="2500 AMBASSADOR CAFFERY PKWY")
        found = self.find()
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["cross_street"])

    def test_other_road_at_that_distance_does_not_match(self):
        self.db.add("A", self.now - timedelta(minutes=5), lng=-92.02156, loc="100 SOME OTHER ST")
        self.assertEqual(self.find(), [])

    def test_replay_uses_what_was_listed_at_that_moment(self):
        then = self.now - timedelta(days=2)
        self.db.add("A", then - timedelta(hours=2), first_seen=then - timedelta(hours=2),
                    last_seen=then + timedelta(minutes=30))
        self.db.sweep(self.now)
        found = find_route_incidents(self.db.path, ROUTE, 90, now=then)
        self.assertEqual([f["status"] for f in found], ["active"])


class _Store:
    def __init__(self):
        self.meta = {}

    def _meta_get(self, key):
        return self.meta.get(key)

    def _meta_set(self, key, value):
        self.meta[key] = value


class ScheduleWithRealFeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Db(self.tmp.name)
        self.store = _Store()
        self.sent = []
        self.cfg = DigestConfig(True, "smtp.example", 587, "u", "p", "to@example", "from@example", 7, "")
        # Departure 10 minutes from now, so "now" sits at the start of the window.
        now = datetime.now().replace(second=0, microsecond=0)
        depart = now + timedelta(minutes=10)
        if depart.date() != now.date():
            self.skipTest("too close to midnight")
        self.now = now
        self.route = Route(index=1, name="To work", corridors=set(), corridor_labels=[],
                           depart_minutes=depart.hour * 60 + depart.minute, days=set(range(7)),
                           path=PATH, radius_m=100)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cycle(self, now):
        class Config:
            db_path = self.db.path
            base_dir = self.tmp.name

        return maybe_send_route_alerts(
            Config, self.store, None, None,
            route_cfg=RouteConfig(True, 10, 90, 30, [self.route]), digest_cfg=self.cfg,
            send=lambda cfg, html, subject, images=None: self.sent.append((subject, html)), now=now)

    def test_departure_email_reports_the_crash(self):
        self.db.add("A", self.now - timedelta(minutes=8))
        self.assertEqual(self.run_cycle(self.now), 1)
        self.assertIn("1 on your route", self.sent[0][0])

    def test_followup_fires_for_a_crash_after_the_first_email(self):
        self.assertEqual(self.run_cycle(self.now), 1)
        self.assertIn("route clear", self.sent[0][0])
        later = self.now + timedelta(minutes=15)
        if later.date() != self.now.date() or later > datetime.now() + timedelta(minutes=9):
            # "later" must stay inside the live window of the real clock.
            later = self.now + timedelta(minutes=8)
        self.db.add("B", later - timedelta(minutes=3))
        self.assertEqual(self.run_cycle(later), 1)
        self.assertIn("NEW on your route", self.sent[1][0])
        # ...and only once.
        self.assertEqual(self.run_cycle(later + timedelta(minutes=1)), 0)


if __name__ == "__main__":
    unittest.main()
