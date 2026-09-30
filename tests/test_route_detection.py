"""Regression tests for missed route alerts, per-day departures, the route
map picture, and settings-only config emails. No real SMTP or network."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from lafayette911 import daily_digest
from lafayette911.daily_digest import DigestConfig
from lafayette911.route_alerts import (
    ROUTE_MAP_CID,
    Route,
    RouteConfig,
    _parse_hhmm,
    _route_from_kv,
    derive_path_corridors,
    find_route_incidents,
    maybe_send_route_alerts,
    render_route_email,
    run_route_test,
)
from lafayette911.route_inbox import apply_route_slots
from lafayette911.route_map_image import render_route_png

# ~1.1 km north-south drawn line along "Ambassador Caffery" at lng -92.02,
# exactly what the map's route builder emits: a PATH and no CORRIDORS.
PATH = [(30.200, -92.020), (30.205, -92.020), (30.210, -92.020)]


def _rep(dt):
    return dt.strftime("%m/%d/%Y %I:%M %p")


def _make_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE incidents (incident_number TEXT PRIMARY KEY, location TEXT, cause TEXT,
        reported TEXT, assisting TEXT, latitude REAL, longitude REAL, created_at TEXT,
        weather_precip_prob REAL, weather_precip_in REAL)"""
    )
    conn.executemany("INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def _history(old):
    """Weeks-old geocoded incidents: the route's own road spread along the
    line; a cross street only where it crosses (one point)."""
    return [
        ("H1", "1000 AMBASSADOR CAFFERY PKWY", "ACCIDENT", _rep(old), "", 30.2005, -92.0201, "", None, None),
        ("H2", "2000 AMBASSADOR CAFFERY PKWY", "ACCIDENT", _rep(old), "", 30.2050, -92.0199, "", None, None),
        ("H3", "3000 AMBASSADOR CAFFERY PKWY", "STALLED VEHICLE", _rep(old), "", 30.2095, -92.0200, "", None, None),
        ("H4", "KALISTE SALOOM RD AT AMBASSADOR CAFFERY PKWY", "ACCIDENT", _rep(old), "", 30.2050, -92.0200, "", None, None),
        ("H5", "200 KALISTE SALOOM RD", "ACCIDENT", _rep(old), "", 30.2051, -92.0190, "", None, None),
    ]


def _drawn_route(**kw):
    base = dict(index=1, name="To work", corridors=set(), corridor_labels=[],
                depart_minutes=_parse_hhmm("07:20"), days={0, 1, 2, 3, 4},
                path=list(PATH), radius_m=100)
    base.update(kw)
    return Route(**base)


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
    return DigestConfig(enabled=True, smtp_host="h", smtp_port=587, smtp_user="u", smtp_pass="p",
                        mail_to="u@test", mail_from="u@test", send_hour=7, map_url="")


class MissedAlertRegressionTests(unittest.TestCase):
    """The reported bug: a major crash on the route before the departure
    email, not yet placed on the map, and the email said 'route clear'."""

    def test_route_roads_are_learned_from_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, _history(datetime(2026, 8, 1, 8, 0)))
            roads = derive_path_corridors(db, PATH, 100)
        self.assertIn("AMBASSADOR CAFFERY PKWY", roads)
        self.assertNotIn("KALISTE SALOOM RD", roads)   # only crosses the line

    def test_unlocated_crash_on_route_road_is_reported(self):
        now = datetime(2026, 9, 28, 7, 10)
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, _history(datetime(2026, 8, 1, 8, 0)) + [
                ("X1", "AMBASSADOR CAFFERY PKWY AT CAMELLIA BLVD", "TRAFFIC ACCIDENT MAJOR",
                 _rep(now - timedelta(minutes=12)), "", None, None, "", None, None),
                ("X2", "KALISTE SALOOM RD", "ROAD HAZARD",
                 _rep(now - timedelta(minutes=8)), "", None, None, "", None, None),
            ])
            diag = []
            found = find_route_incidents(db, _drawn_route(), 90, now=now, diagnostics=diag)
        self.assertEqual([f["cause"] for f in found], ["TRAFFIC ACCIDENT MAJOR"])
        self.assertTrue(found[0]["approx"])
        verdicts = {d["location"]: d["verdict"] for d in diag}
        self.assertEqual(verdicts["KALISTE SALOOM RD"], "skipped")
        html = render_route_email(_drawn_route(), found, now, 90)
        self.assertNotIn("looks clear", html)
        self.assertIn("not on the map yet", html)

    def test_crash_located_after_departure_email_triggers_followup(self):
        """Not matchable at 07:10; geocoded at 07:35 → follow-up, because
        the watch now lasts 30 minutes by default."""
        send_at = datetime(2026, 9, 28, 7, 10)   # Monday
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            # No history, so the road can't be learned: truly unmatchable.
            _make_db(db, [("Y1", "5100 SOMEWHERE RD", "TRAFFIC ACCIDENT MAJOR",
                           _rep(send_at - timedelta(minutes=5)), "", None, None, "", None, None)])
            rcfg = RouteConfig(enabled=True, lead_min=10, window_min=90, routes=[_drawn_route()])
            self.assertEqual(rcfg.followup_min, 30)
            store = _FakeStore()
            send = lambda c, h, s, **kw: sent.append(s)
            maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=rcfg,
                                    digest_cfg=_digest_cfg(), send=send, now=send_at)
            self.assertIn("route clear", sent[0])
            conn = sqlite3.connect(db)
            conn.execute("UPDATE incidents SET latitude=30.205, longitude=-92.0201 WHERE incident_number='Y1'")
            conn.commit()
            conn.close()
            n = maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=rcfg,
                                        digest_cfg=_digest_cfg(), send=send,
                                        now=datetime(2026, 9, 28, 7, 35))
        self.assertEqual(n, 1)
        self.assertTrue(sent[-1].startswith("🚨"))


class PerDayDepartureTests(unittest.TestCase):
    def _run(self, now):
        route = _route_from_kv(2, {"NAME": "Home", "PATH": "30.2,-92.02; 30.21,-92.02",
                                   "DEPART": "17:00", "DEPART_FRI": "12:00", "DAYS": "mon-fri"})
        rcfg = RouteConfig(enabled=True, lead_min=10, window_min=90, routes=[route])
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, [])
            maybe_send_route_alerts(_Cfg(db), _FakeStore(), None, None, route_cfg=rcfg,
                                    digest_cfg=_digest_cfg(),
                                    send=lambda c, h, s, **kw: sent.append(h), now=now)
        return sent

    def test_friday_uses_override(self):
        self.assertEqual(len(self._run(datetime(2026, 10, 2, 11, 52))), 1)   # Friday
        self.assertIn("leaving ~12:00 PM", self._run(datetime(2026, 10, 2, 11, 52))[0])
        self.assertEqual(self._run(datetime(2026, 10, 2, 16, 52)), [])      # not at 5 on Friday

    def test_other_days_unchanged(self):
        self.assertEqual(self._run(datetime(2026, 10, 1, 11, 52)), [])      # Thursday noon
        self.assertEqual(len(self._run(datetime(2026, 10, 1, 16, 52))), 1)  # Thursday 5 PM


class TestModeTests(unittest.TestCase):
    def test_test_email_is_labelled_and_leaves_schedule_alone(self):
        route = _route_from_kv(2, {"NAME": "Home", "PATH": "30.2,-92.02; 30.21,-92.02",
                                   "DEPART": "17:00", "DEPART_FRI": "12:00", "DAYS": "mon-fri"})
        rcfg = RouteConfig(enabled=True, lead_min=10, window_min=90, routes=[route])
        sent = []
        store = _FakeStore()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, [("Z1", "5100 SOMEWHERE RD", "TRAFFIC ACCIDENT MAJOR", _rep(datetime(2026, 10, 2, 11, 40)),
                           "", 30.205, -92.0201, "", None, None)])
            out = run_route_test(_Cfg(db), store, None, None, 2, datetime(2026, 10, 2, 11, 50),
                                 route_cfg=rcfg, digest_cfg=_digest_cfg(),
                                 send=lambda c, h, s, **kw: sent.append((h, s)))
        self.assertTrue(out["emailed"])
        self.assertEqual(out["incidents"], 1)
        self.assertIn("11:50 AM for a 12:00 PM departure", out["schedule"])
        self.assertTrue(sent[0][1].startswith("🧪 TEST"))
        self.assertIn("Test email", sent[0][0])
        self.assertIn("leaving ~12:00 PM", sent[0][0])
        self.assertEqual(store.meta, {})     # no dedup/schedule state touched

    def test_unknown_route_and_off_day(self):
        route = _route_from_kv(2, {"NAME": "Home", "PATH": "30.2,-92.02; 30.21,-92.02",
                                   "DEPART": "17:00", "DAYS": "mon-fri"})
        rcfg = RouteConfig(enabled=True, lead_min=10, window_min=90, routes=[route])
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, [])
            missing = run_route_test(_Cfg(db), _FakeStore(), None, None, 5, datetime(2026, 10, 3, 9, 0),
                                     route_cfg=rcfg, digest_cfg=_digest_cfg(), send=lambda *a, **k: None)
            saturday = run_route_test(_Cfg(db), _FakeStore(), None, None, 2, datetime(2026, 10, 3, 9, 0),
                                      route_cfg=rcfg, digest_cfg=_digest_cfg(), send=lambda *a, **k: None)
        self.assertFalse(missing["ok"])
        self.assertIn("isn't one of this route's days", saturday["schedule"])


class SettingsOnlyEmailTests(unittest.TestCase):
    def test_depart_fri_line_edits_saved_route(self):
        store = _FakeStore()
        apply_route_slots(store, {"2": {"NAME": "Home", "PATH": "30.2,-92.02; 30.21,-92.02",
                                        "DEPART": "17:00", "DAYS": "mon-fri"}})
        summaries = apply_route_slots(store, {"2": {"DEPART_FRI": "12:00"}})
        self.assertIn("Fris at 12:00 PM", summaries[0])
        import json
        saved = json.loads(store.meta["mail_routes_v1"])["2"]
        self.assertEqual(saved["PATH"], "30.2,-92.02; 30.21,-92.02")   # kept
        self.assertEqual(saved["DEPART"], "17:00")
        self.assertEqual(saved["DEPART_FRI"], "12:00")

    def test_email_with_path_still_replaces(self):
        store = _FakeStore()
        apply_route_slots(store, {"1": {"PATH": "30.2,-92.02; 30.21,-92.02", "DEPART": "07:20",
                                        "DEPART_MON": "06:00"}})
        apply_route_slots(store, {"1": {"PATH": "30.3,-92.02; 30.31,-92.02", "DEPART": "07:30"}})
        import json
        saved = json.loads(store.meta["mail_routes_v1"])["1"]
        self.assertEqual(saved["DEPART_MON"], "")


class RouteMapPictureTests(unittest.TestCase):
    def test_png_renders_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = render_route_png(PATH, [{"lat": 30.205, "lng": -92.0201}], cache_dir=tmp, session=None)
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_email_embeds_picture_inline(self):
        now = datetime(2026, 9, 28, 7, 12)
        rcfg = RouteConfig(enabled=True, lead_min=10, window_min=90, routes=[_drawn_route()])
        captured = []

        class _SMTP:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def ehlo(self):
                pass

            def starttls(self):
                pass

            def login(self, *a):
                pass

            def send_message(self, msg):
                captured.append(msg)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(daily_digest.smtplib, "SMTP", _SMTP):
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, [])
            maybe_send_route_alerts(_Cfg(db), _FakeStore(), None, None, route_cfg=rcfg,
                                    digest_cfg=_digest_cfg(), send=daily_digest.send_digest, now=now)
        self.assertEqual(len(captured), 1)
        raw = captured[0].as_string()
        self.assertIn("cid:" + ROUTE_MAP_CID, raw)
        self.assertIn("Content-ID: <%s>" % ROUTE_MAP_CID, raw)
        self.assertIn("image/png", raw)


if __name__ == "__main__":
    unittest.main()
