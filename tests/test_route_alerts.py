"""Personal commute route alerts: config parsing, corridor matching with a
freshness window, email rendering, and the schedule/dedup/fail-safe rules.
No real SMTP is ever touched."""

import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta

from lafayette911.route_alerts import (
    DigestConfig,
    Route,
    RouteConfig,
    _parse_days,
    _parse_hhmm,
    find_route_incidents,
    load_route_config,
    maybe_send_route_alerts,
    render_route_email,
)


def _make_db(path, now):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE incidents (incident_number TEXT PRIMARY KEY, location TEXT, cause TEXT,
        reported TEXT, assisting TEXT, latitude REAL, longitude REAL, created_at TEXT,
        weather_precip_prob REAL, weather_precip_in REAL)"""
    )

    def rep(dt):
        return dt.strftime("%m/%d/%Y %I:%M %p")

    rows = [
        # On route (Ambassador Caffery), 10 min ago → included, on top (accident).
        ("R1", "3500 AMBASSADOR CAFFERY PKWY", "ACCIDENT", rep(now - timedelta(minutes=10)), "LPD", 30.2, -92.0, "", 80, 0.2),
        # On route (I-10) but 3 hours ago → excluded by freshness window.
        ("R2", "I-10 E", "ACCIDENT", rep(now - timedelta(hours=3)), "LPD", 30.2, -92.0, "", None, None),
        # On route (Kaliste Saloom), 40 min ago, hazard, unlocated → included.
        ("R3", "KALISTE SALOOM RD", "ROAD HAZARD", rep(now - timedelta(minutes=40)), "LPD", None, None, "", None, None),
        # NOT on route → excluded.
        ("R4", "JOHNSTON ST", "ACCIDENT", rep(now - timedelta(minutes=5)), "LPD", 30.2, -92.0, "", None, None),
    ]
    conn.executemany("INSERT INTO incidents VALUES (%s)" % ",".join("?" * 10), rows)
    conn.commit()
    conn.close()


def _route():
    return Route(
        index=1, name="To work",
        corridors={"AMBASSADOR CAFFERY PKWY", "KALISTE SALOOM RD", "I-10"},
        corridor_labels=["Ambassador Caffery", "Kaliste Saloom", "I-10"],
        depart_minutes=7 * 60 + 20, days={0, 1, 2, 3, 4},
    )


class ParseTests(unittest.TestCase):
    def test_hhmm(self):
        self.assertEqual(_parse_hhmm("07:20"), 440)
        self.assertEqual(_parse_hhmm("17:00"), 1020)
        self.assertIsNone(_parse_hhmm("nope"))
        self.assertIsNone(_parse_hhmm("25:00"))

    def test_days(self):
        self.assertEqual(_parse_days("mon-fri"), {0, 1, 2, 3, 4})
        self.assertEqual(_parse_days("mon,wed,fri"), {0, 2, 4})
        self.assertEqual(_parse_days("weekends"), {5, 6})
        self.assertEqual(_parse_days("daily"), set(range(7)))
        self.assertEqual(_parse_days("sat-sun"), {5, 6})
        self.assertEqual(_parse_days(""), {0, 1, 2, 3, 4})  # default weekdays

    def test_load_config_from_env(self):
        env = {
            "LAF911_ROUTE_ENABLED": "true",
            "LAF911_ROUTE_LEAD_MIN": "15",
            "LAF911_ROUTE_1_NAME": "To work",
            "LAF911_ROUTE_1_CORRIDORS": "Ambassador Caffery | 100 Kaliste Saloom Rd | I-10",
            "LAF911_ROUTE_1_DEPART": "07:20",
            "LAF911_ROUTE_1_DAYS": "mon-fri",
        }
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            cfg = load_route_config()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.lead_min, 15)
        self.assertEqual(len(cfg.routes), 1)
        r = cfg.routes[0]
        self.assertEqual(r.depart_minutes, 440)
        # Loosely typed roads normalize to canonical corridors.
        self.assertIn("AMBASSADOR CAFFERY PKWY", r.corridors)
        self.assertIn("KALISTE SALOOM RD", r.corridors)
        self.assertIn("I-10", r.corridors)


class MatchingTests(unittest.TestCase):
    def test_freshness_and_corridor_matching(self):
        now = datetime.now().replace(microsecond=0)
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            found = find_route_incidents(db, _route(), window_min=90, now=now)
        # R1 (accident, 10m) and R3 (hazard, 40m) match; R2 too old, R4 off route.
        self.assertEqual([f["location"] for f in found],
                         ["3500 AMBASSADOR CAFFERY PKWY", "KALISTE SALOOM RD"])
        self.assertEqual(found[0]["category"], "Accidents")   # severity first
        self.assertEqual(found[0]["minutes_ago"], 10)
        self.assertFalse(found[1]["located"])                 # unlocated still counts
        self.assertEqual(found[0]["matched"], ["AMBASSADOR CAFFERY PKWY"])

    def test_window_can_exclude_everything(self):
        now = datetime.now().replace(microsecond=0)
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            found = find_route_incidents(db, _route(), window_min=5, now=now)
        # Only R4 is <5min but it's off-route → nothing on route.
        self.assertEqual(found, [])


class RenderTests(unittest.TestCase):
    def test_incident_email(self):
        now = datetime.now().replace(microsecond=0)
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            found = find_route_incidents(db, _route(), 90, now=now)
        html = render_route_email(_route(), found, now, 90,
                                  alerts={"Flash Flood Warning": True}, map_url="https://x.example/")
        self.assertIn("active incident", html)
        self.assertIn("To work", html)
        self.assertIn("Ambassador Caffery", html)
        self.assertIn("min ago", html)
        self.assertIn("Flash Flood Warning", html)
        self.assertIn("call 911", html)
        self.assertNotIn("<img", html)  # email-safe

    def test_clear_email(self):
        html = render_route_email(_route(), [], datetime.now(), 90)
        self.assertIn("looks clear", html)
        self.assertIn("No 911 incidents", html)


class _FakeStore:
    def __init__(self):
        self.meta = {}

    def _meta_get(self, k):
        return self.meta.get(k)

    def _meta_set(self, k, v):
        self.meta[k] = v


class _Cfg:
    def __init__(self, db):
        self.db_path = db


def _digest_cfg():
    return DigestConfig(enabled=True, smtp_host="smtp.test", smtp_port=587,
                        smtp_user="u@test", smtp_pass="pw", mail_to="u@test",
                        mail_from="u@test", send_hour=7, map_url="")


class ScheduleTests(unittest.TestCase):
    def _cfg_at(self, depart="07:20", lead=10, days=None):
        return RouteConfig(enabled=True, lead_min=lead, window_min=90, routes=[
            Route(index=1, name="To work", corridors={"AMBASSADOR CAFFERY PKWY"},
                  corridor_labels=["Ambassador Caffery"],
                  depart_minutes=_parse_hhmm(depart),
                  days=days if days is not None else {0, 1, 2, 3, 4})
        ])

    def test_fires_in_window_once_per_day(self):
        # A Monday at 07:12 (departure 07:20, lead 10 → window [07:10, 07:20)).
        now = datetime(2026, 7, 13, 7, 12)  # Monday
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            store = _FakeStore()
            n1 = maybe_send_route_alerts(_Cfg(db), store, None, None,
                                         route_cfg=self._cfg_at(), digest_cfg=_digest_cfg(),
                                         send=lambda c, h, s: sent.append(s), now=now)
            n2 = maybe_send_route_alerts(_Cfg(db), store, None, None,
                                         route_cfg=self._cfg_at(), digest_cfg=_digest_cfg(),
                                         send=lambda c, h, s: sent.append(s), now=now)
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 0)          # deduped for the day
        self.assertEqual(len(sent), 1)

    def test_outside_window_and_wrong_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, datetime(2026, 7, 13, 8, 0))
            # 08:00 is past departure → no send.
            self.assertEqual(maybe_send_route_alerts(
                _Cfg(db), _FakeStore(), None, None, route_cfg=self._cfg_at(),
                digest_cfg=_digest_cfg(), send=lambda *a: None, now=datetime(2026, 7, 13, 8, 0)), 0)
            # Saturday, mon-fri route → no send even in the time window.
            self.assertEqual(maybe_send_route_alerts(
                _Cfg(db), _FakeStore(), None, None, route_cfg=self._cfg_at(),
                digest_cfg=_digest_cfg(), send=lambda *a: None, now=datetime(2026, 7, 18, 7, 12)), 0)

    def test_disabled_and_failure_backoff(self):
        now = datetime(2026, 7, 13, 7, 12)
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            cfg = self._cfg_at()
            cfg.enabled = False
            self.assertEqual(maybe_send_route_alerts(
                _Cfg(db), _FakeStore(), None, None, route_cfg=cfg,
                digest_cfg=_digest_cfg(), send=lambda *a: None, now=now), 0)

            store = _FakeStore()
            cfg.enabled = True

            def boom(c, h, s):
                raise RuntimeError("smtp down")

            for _ in range(3):
                maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=cfg,
                                        digest_cfg=_digest_cfg(), send=boom, now=now)
            calls = []
            maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=cfg,
                                    digest_cfg=_digest_cfg(),
                                    send=lambda *a: calls.append(1), now=now)
            self.assertEqual(calls, [])   # backed off after 3 failures


if __name__ == "__main__":
    unittest.main()
