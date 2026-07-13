"""Daily digest: stats from the archive, HTML rendering, and the
once-per-day / fail-safe sending rules. No real SMTP is ever touched."""

import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta

from lafayette911.daily_digest import (
    DigestConfig,
    categorize,
    collect_digest_stats,
    maybe_send_daily_digest,
    render_digest_html,
)


def _make_db(path, now):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE incidents (
            incident_number TEXT PRIMARY KEY,
            location TEXT, cause TEXT, reported TEXT, assisting TEXT,
            latitude REAL, longitude REAL, created_at TEXT,
            geocode_attempts INTEGER, hour_of_day INTEGER,
            weather_precip_prob REAL, weather_precip_in REAL,
            nws_flash_flood_warning INTEGER, nws_severe_thunderstorm_warning INTEGER,
            nws_tornado_watch INTEGER
        )
        """
    )
    conn.execute("CREATE TABLE geocode_api_requests (id INTEGER PRIMARY KEY, requested_at_epoch INTEGER)")
    conn.execute(
        "CREATE TABLE geocode_failed_locations (location_key TEXT PRIMARY KEY,"
        " attempts INTEGER, last_attempt_epoch INTEGER)"
    )

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = [
        # In-window: 2 accidents (1 located, 1 pending), 1 fire (located),
        # 1 stalled retired after 3 attempts.
        ("I1", "3500 AMBASSADOR CAFFERY PKWY", "ACCIDENT", "07/13/2026 8:30 AM", "POLICE",
         30.2, -92.0, iso(now - timedelta(hours=2)), 0, 8, 80.0, 0.2, 1, 0, 0),
        ("I2", "JOHNSTON ST", "ACCIDENT", "07/13/2026 9:30 AM", "SHERIFF FIRE",
         None, None, iso(now - timedelta(hours=3)), 1, 9, None, None, None, None, None),
        ("I3", "AMBASSADOR CAFFERY PKWY AT KALISTE SALOOM RD", "VEHICLE FIRE", "07/13/2026 1:15 PM", "FIRE",
         30.21, -92.01, iso(now - timedelta(hours=5)), 0, 13, None, None, 0, 0, 0),
        ("I4", "NOWHERE LN", "STALLED VEHICLE", "07/13/2026 2:00 PM", "",
         None, None, iso(now - timedelta(hours=6)), 3, 14, None, None, None, None, None),
        # Previous window (24-48h ago): 2 incidents.
        ("P1", "MOSS ST", "ACCIDENT", "07/12/2026 8:30 AM", "POLICE",
         30.2, -92.0, iso(now - timedelta(hours=30)), 0, 8, None, None, 0, 0, 0),
        ("P2", "PINHOOK RD", "ACCIDENT", "07/12/2026 9:30 AM", "POLICE",
         30.2, -92.0, iso(now - timedelta(hours=31)), 0, 9, None, None, 0, 0, 0),
    ]
    conn.executemany("INSERT INTO incidents VALUES (%s)" % ",".join("?" * 15), rows)
    conn.execute("INSERT INTO geocode_api_requests (requested_at_epoch) VALUES (?)", (int(time.time()) - 100,))
    conn.execute("INSERT INTO geocode_api_requests (requested_at_epoch) VALUES (?)", (int(time.time()) - 90000,))
    conn.execute(
        "INSERT INTO geocode_failed_locations VALUES ('nowhere ln', 3, ?)", (int(time.time()) - 50,)
    )
    conn.commit()
    conn.close()


class CollectStatsTests(unittest.TestCase):
    def test_stats_windows_and_accounting(self):
        now = datetime.utcnow()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            s = collect_digest_stats(db, now=now)

        self.assertEqual(s["new_total"], 4)
        self.assertEqual(s["prev_total"], 2)
        self.assertEqual(s["by_category"]["Accidents"], 2)
        self.assertEqual(s["by_category"]["Fire"], 1)
        self.assertEqual(s["by_category"]["Stalled"], 1)
        self.assertEqual(s["by_hour"][8], 1)
        self.assertEqual(s["by_hour"][13], 1)
        # Corridors are canonical: both Ambassador Caffery spellings group,
        # and the intersection also credits Kaliste Saloom.
        cors = dict(s["top_corridors"])
        self.assertEqual(cors["AMBASSADOR CAFFERY PKWY"], 2)
        self.assertEqual(cors["KALISTE SALOOM RD"], 1)
        # Agencies (multi-agency incident counts toward each).
        self.assertEqual(s["agencies"]["Police"], 1)
        self.assertEqual(s["agencies"]["Sheriff"], 1)
        self.assertEqual(s["agencies"]["Fire dept."], 2)
        # Geocode accounting.
        g = s["geocode"]
        self.assertEqual(g["located_24h"], 2)
        self.assertEqual(g["pending_24h"], 1)
        self.assertEqual(g["retired_24h"], 1)
        self.assertEqual(g["api_calls_24h"], 1)   # the 25h-old call is outside
        self.assertEqual(g["queue_now"], 1)
        self.assertEqual(g["blacklist_total"], 1)
        self.assertEqual(s["weather"]["rain_flagged"], 1)
        self.assertEqual(s["weather"]["nws_flagged"], 1)
        self.assertEqual(s["archive"]["total"], 6)

    def test_categorize(self):
        self.assertEqual(categorize("VEHICLE ACCIDENT"), "Accidents")
        self.assertEqual(categorize("TRAFFIC CONTROL"), "Signals")
        self.assertEqual(categorize("banana"), "Other")


class RenderTests(unittest.TestCase):
    def test_html_contains_the_story(self):
        now = datetime.utcnow()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            s = collect_digest_stats(db, now=now)
        html = render_digest_html(
            s,
            service_info={"started_at_epoch": time.time() - 90000, "cycles_completed": 288,
                          "errors_24h": 1, "last_cycle_seconds": 3.2, "rss_bytes": 80 * 1048576},
            map_url="https://example.github.io/map/",
        )
        for needle in (
            "Daily Digest", "🚨", "🚗 Accidents", "🔥 Fire", "🛣️", "📍 Geocoding",
            "⚙️ Service health", "Service uptime", "1d 1h", "288",
            "Open the live map", "call 911", "▁",  # sparkline block char
        ):
            self.assertIn(needle, html)
        # Email-safe: no external resources at all.
        self.assertNotIn("<img", html)
        self.assertNotIn("http-equiv", html)

    def test_render_without_service_info(self):
        now = datetime.utcnow()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            s = collect_digest_stats(db, now=now)
        html = render_digest_html(s, service_info=None)
        self.assertIn("Archive size", html)
        self.assertNotIn("Service uptime", html)


class _FakeStore:
    def __init__(self):
        self.meta = {}

    def _meta_get(self, k):
        return self.meta.get(k)

    def _meta_set(self, k, v):
        self.meta[k] = v


class _FakeConfig:
    def __init__(self, db_path):
        self.db_path = db_path


class MaybeSendTests(unittest.TestCase):
    def _cfg(self, **kw):
        base = dict(enabled=True, smtp_host="smtp.test", smtp_port=587,
                    smtp_user="u@test", smtp_pass="pw", mail_to="u@test",
                    mail_from="u@test", send_hour=0, map_url="")
        base.update(kw)
        return DigestConfig(**base)

    def test_sends_once_per_day(self):
        import logging
        now = datetime.utcnow()
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            store, config = _FakeStore(), _FakeConfig(db)
            logger = logging.getLogger("digest-test")
            fake_send = lambda cfg, html, subject: sent.append(subject)

            ok1 = maybe_send_daily_digest(config, store, {}, logger,
                                          digest_cfg=self._cfg(), send=fake_send)
            ok2 = maybe_send_daily_digest(config, store, {}, logger,
                                          digest_cfg=self._cfg(), send=fake_send)
        self.assertTrue(ok1)
        self.assertFalse(ok2)          # deduped by app_meta date
        self.assertEqual(len(sent), 1)
        self.assertIn("Lafayette Traffic", sent[0])

    def test_disabled_and_failure_backoff(self):
        import logging
        now = datetime.utcnow()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            store, config = _FakeStore(), _FakeConfig(db)
            logger = logging.getLogger("digest-test")

            self.assertFalse(maybe_send_daily_digest(
                config, store, {}, logger, digest_cfg=self._cfg(enabled=False)))

            def boom(cfg, html, subject):
                raise RuntimeError("smtp down")

            # Three failures, then it stops trying for the day.
            for _ in range(3):
                self.assertFalse(maybe_send_daily_digest(
                    config, store, {}, logger, digest_cfg=self._cfg(), send=boom))
            calls = []
            self.assertFalse(maybe_send_daily_digest(
                config, store, {}, logger, digest_cfg=self._cfg(),
                send=lambda *a: calls.append(1)))
            self.assertEqual(calls, [])   # backoff engaged; send not attempted

    def test_never_raises(self):
        import logging
        store = _FakeStore()
        config = _FakeConfig("/nonexistent/nope.sqlite")
        logger = logging.getLogger("digest-test")
        # Broken DB path → swallowed, returns False, failure recorded.
        self.assertFalse(maybe_send_daily_digest(
            config, store, {}, logger, digest_cfg=self._cfg(),
            send=lambda *a: None))


if __name__ == "__main__":
    unittest.main()
