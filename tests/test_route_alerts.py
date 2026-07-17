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


    def test_drawn_path_skips_unlocated_whole_roads(self):
        now = datetime.now().replace(microsecond=0)
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            conn = sqlite3.connect(db)
            conn.execute(
                """CREATE TABLE incidents (incident_number TEXT PRIMARY KEY, location TEXT, cause TEXT,
                reported TEXT, assisting TEXT, latitude REAL, longitude REAL, created_at TEXT,
                weather_precip_prob REAL, weather_precip_in REAL)"""
            )
            rep = now.strftime("%m/%d/%Y %I:%M %p")
            rows = [
                ("R1", "3500 AMBASSADOR CAFFERY PKWY", "ACCIDENT", rep, "LPD", 30.2, -92.0, "", None, None),
                ("R2", "KALISTE SALOOM RD", "ROAD HAZARD", rep, "LPD", None, None, "", None, None),
            ]
            conn.executemany("INSERT INTO incidents VALUES (%s)" % ",".join("?" * 10), rows)
            conn.commit()
            conn.close()

            route = Route(
                index=1, name="Drawn only", corridors=set(), corridor_labels=[],
                depart_minutes=7 * 60, days={0, 1, 2, 3, 4},
                path=[(30.199, -92.001), (30.201, -91.999)], radius_m=250,
            )
            found = find_route_incidents(db, route, window_min=90, now=now)

        self.assertEqual([f["location"] for f in found], ["3500 AMBASSADOR CAFFERY PKWY"])

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


    def test_sends_scheduled_email_when_route_has_no_incidents(self):
        now = datetime(2026, 7, 13, 7, 12)
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            cfg = self._cfg_at()
            cfg.window_min = 5
            n = maybe_send_route_alerts(_Cfg(db), _FakeStore(), None, None,
                                        route_cfg=cfg, digest_cfg=_digest_cfg(),
                                        send=lambda c, h, s: sent.append((h, s)), now=now)
        self.assertEqual(n, 1)
        self.assertEqual(len(sent), 1)
        self.assertIn("route clear", sent[0][1])
        self.assertIn("Your route looks clear", sent[0][0])

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


class EpisodeDedupeTests(unittest.TestCase):
    def test_one_crash_logged_twice_is_one_alert_line(self):
        """HIT AND RUN + TRAFFIC ACCIDENT MINOR at the same spot minutes apart
        must email as ONE incident, labeled with the extra listing."""
        now = datetime.now().replace(microsecond=0)
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            conn = sqlite3.connect(db)
            conn.execute(
                """CREATE TABLE incidents (incident_number TEXT PRIMARY KEY, location TEXT, cause TEXT,
                reported TEXT, assisting TEXT, latitude REAL, longitude REAL, created_at TEXT,
                weather_precip_prob REAL, weather_precip_in REAL)"""
            )
            rep = lambda dt: dt.strftime("%m/%d/%Y %I:%M %p")
            conn.executemany("INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?)", [
                ("D1", "3500 AMBASSADOR CAFFERY PKWY", "TRAFFIC ACCIDENT MINOR",
                 rep(now - timedelta(minutes=12)), "LPD", 30.2, -92.0, "", None, None),
                ("D2", "3500 AMBASSADOR CAFFERY PKWY", "HIT AND RUN",
                 rep(now - timedelta(minutes=9)), "LPD", 30.2, -92.0, "", None, None),
            ])
            conn.commit()
            conn.close()
            found = find_route_incidents(db, _route(), window_min=90, now=now)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["cause"], "HIT AND RUN")     # severity wins
        self.assertEqual(found[0]["also"], ["TRAFFIC ACCIDENT MINOR"])
        self.assertEqual(set(found[0]["episode_ids"]), {"D1", "D2"})
        html = render_route_email(_route(), found, now, 90)
        self.assertIn("also logged as: Traffic Accident Minor", html)


class FollowupTests(unittest.TestCase):
    def _cfg(self):
        return RouteConfig(enabled=True, lead_min=10, window_min=90, followup_min=15, routes=[
            Route(index=1, name="To work", corridors={"AMBASSADOR CAFFERY PKWY"},
                  corridor_labels=["Ambassador Caffery"],
                  depart_minutes=_parse_hhmm("07:20"), days={0, 1, 2, 3, 4})
        ])

    def _add(self, db, num, cause, dt, location="3500 AMBASSADOR CAFFERY PKWY"):
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?)",
            (num, location, cause, dt.strftime("%m/%d/%Y %I:%M %p"), "LPD",
             30.2, -92.0, "", None, None),
        )
        conn.commit()
        conn.close()

    def test_new_incident_after_send_triggers_one_followup(self):
        now = datetime(2026, 7, 13, 7, 12)   # Monday; window [07:10, 07:20)
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            store = _FakeStore()
            send = lambda c, h, s: sent.append((h, s))
            n1 = maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=self._cfg(),
                                         digest_cfg=_digest_cfg(), send=send, now=now)
            self.assertEqual(n1, 1)

            # A brand-new crash appears (different spot on the same corridor —
            # a new record at R1's own address within the episode window would
            # correctly fold into the already-reported crash instead).
            later = datetime(2026, 7, 13, 7, 16)
            self._add(db, "NEW1", "TRAFFIC ACCIDENT MAJOR", datetime(2026, 7, 13, 7, 14),
                      location="5100 AMBASSADOR CAFFERY PKWY")
            n2 = maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=self._cfg(),
                                         digest_cfg=_digest_cfg(), send=send, now=later)
            self.assertEqual(n2, 1)
            self.assertTrue(sent[-1][1].startswith("🚨"))
            self.assertIn("NEW incident", sent[-1][0])
            self.assertIn("Traffic Accident Major", sent[-1][1].title())

            # Same state again → nothing new, no repeat.
            n3 = maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=self._cfg(),
                                         digest_cfg=_digest_cfg(), send=send,
                                         now=datetime(2026, 7, 13, 7, 20))
            self.assertEqual(n3, 0)
            self.assertEqual(len(sent), 2)

    def test_relisting_of_reported_crash_is_not_new(self):
        """The feed logging the SAME crash under a second cause after the
        departure email must NOT fire a follow-up — episode ids match."""
        now = datetime(2026, 7, 13, 7, 12)
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)   # R1 at 07:02 on Ambassador Caffery, emailed below
            store = _FakeStore()
            maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=self._cfg(),
                                    digest_cfg=_digest_cfg(),
                                    send=lambda c, h, s: sent.append(s), now=now)
            # Dispatch re-lists R1's crash as a hit-and-run 8 minutes later.
            self._add(db, "RELIST", "HIT AND RUN", datetime(2026, 7, 13, 7, 10))
            n = maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=self._cfg(),
                                        digest_cfg=_digest_cfg(),
                                        send=lambda c, h, s: sent.append(s),
                                        now=datetime(2026, 7, 13, 7, 16))
        self.assertEqual(n, 0)
        self.assertEqual(len(sent), 1)

    def test_followup_window_expires(self):
        now = datetime(2026, 7, 13, 7, 12)
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            store = _FakeStore()
            maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=self._cfg(),
                                    digest_cfg=_digest_cfg(),
                                    send=lambda c, h, s: sent.append(s), now=now)
            self._add(db, "NEW1", "TRAFFIC ACCIDENT MAJOR", datetime(2026, 7, 13, 7, 26))
            # 07:30 is 18 min after the 07:12 email → outside the 15-min watch.
            n = maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=self._cfg(),
                                        digest_cfg=_digest_cfg(),
                                        send=lambda c, h, s: sent.append(s),
                                        now=datetime(2026, 7, 13, 7, 30))
        self.assertEqual(n, 0)
        self.assertEqual(len(sent), 1)

    def test_followup_disabled_when_zero(self):
        now = datetime(2026, 7, 13, 7, 12)
        sent = []
        cfg = self._cfg()
        cfg.followup_min = 0
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite")
            _make_db(db, now)
            store = _FakeStore()
            maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=cfg,
                                    digest_cfg=_digest_cfg(),
                                    send=lambda c, h, s: sent.append(s), now=now)
            self._add(db, "NEW1", "TRAFFIC ACCIDENT MAJOR", datetime(2026, 7, 13, 7, 13))
            n = maybe_send_route_alerts(_Cfg(db), store, None, None, route_cfg=cfg,
                                        digest_cfg=_digest_cfg(),
                                        send=lambda c, h, s: sent.append(s),
                                        now=datetime(2026, 7, 13, 7, 14))
        self.assertEqual(n, 0)
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
