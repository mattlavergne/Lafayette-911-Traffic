"""Route-by-email: parsing mangled email bodies, self-only sender rules,
slot merge/delete, and the never-raises polling contract. Section-precise
path matching is also covered here. No IMAP/SMTP is ever touched."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

from lafayette911.daily_digest import DigestConfig
from lafayette911.route_alerts import (
    Route,
    dist_to_path_m,
    find_route_incidents,
    load_route_config,
    _parse_path,
)
from lafayette911.route_inbox import (
    apply_route_slots,
    parse_route_email,
    poll_route_inbox,
)


class _FakeStore:
    def __init__(self):
        self.meta = {}

    def _meta_get(self, k):
        return self.meta.get(k)

    def _meta_set(self, k, v):
        self.meta[k] = v


def _cfg():
    return DigestConfig(enabled=True, smtp_host="smtp.gmail.com", smtp_port=587,
                        smtp_user="me@gmail.com", smtp_pass="pw", mail_to="me@gmail.com",
                        mail_from="me@gmail.com", send_hour=7, map_url="")


# A north-south road at lng=-92.02; the commute uses only the MIDDLE third.
PATH = [(30.200, -92.020), (30.210, -92.020)]


class PathMathTests(unittest.TestCase):
    def test_parse_path(self):
        self.assertEqual(_parse_path("30.2,-92.0; 30.3,-92.1"), [(30.2, -92.0), (30.3, -92.1)])
        self.assertEqual(_parse_path("junk; 30.2,-92.0; 99,x"), [(30.2, -92.0)])
        self.assertEqual(_parse_path(""), [])

    def test_point_to_polyline_distance(self):
        # On the line → ~0; 0.001° lng east of it → ~96 m at this latitude.
        self.assertLess(dist_to_path_m(30.205, -92.020, PATH), 1)
        d = dist_to_path_m(30.205, -92.019, PATH)
        self.assertTrue(90 < d < 105, d)
        # Beyond the segment's END, distance grows (no infinite-line match):
        # 30.220 is 0.01° (~1.1 km) past the north end.
        self.assertGreater(dist_to_path_m(30.220, -92.020, PATH), 1000)


class SectionMatchingTests(unittest.TestCase):
    def _db(self, tmp, now):
        db = os.path.join(tmp, "t.sqlite")
        conn = sqlite3.connect(db)
        conn.execute(
            """CREATE TABLE incidents (incident_number TEXT PRIMARY KEY, location TEXT,
            cause TEXT, reported TEXT, assisting TEXT, latitude REAL, longitude REAL,
            created_at TEXT, weather_precip_prob REAL, weather_precip_in REAL)"""
        )
        rep = lambda dt: dt.strftime("%m/%d/%Y %I:%M %p")
        rows = [
            # INSIDE the driven section (30.205 on the line) → must match.
            ("A", "3500 AMBASSADOR CAFFERY PKWY", "ACCIDENT", rep(now - timedelta(minutes=9)), "", 30.205, -92.020, "", None, None),
            # SAME ROAD but 2+ km north of the section → must NOT match.
            ("B", "8000 AMBASSADOR CAFFERY PKWY", "ACCIDENT", rep(now - timedelta(minutes=7)), "", 30.230, -92.020, "", None, None),
            # Near the line but a different road name → matches (it's ON the
            # driven section geographically — road names don't gate located
            # incidents).
            ("C", "SETTLERS TRACE BLVD", "STALLED VEHICLE", rep(now - timedelta(minutes=20)), "", 30.207, -92.0205, "", None, None),
            # On-road but NOT YET GEOCODED → skipped; section cannot be proven.
            ("D", "AMBASSADOR CAFFERY PKWY", "ROAD HAZARD", rep(now - timedelta(minutes=5)), "", None, None, "", None, None),
            # Unlocated and NOT on a listed corridor → excluded.
            ("E", "JOHNSTON ST", "ACCIDENT", rep(now - timedelta(minutes=5)), "", None, None, "", None, None),
        ]
        conn.executemany("INSERT INTO incidents VALUES (%s)" % ",".join("?" * 10), rows)
        conn.commit()
        conn.close()
        return db

    def test_only_the_driven_section_matches(self):
        now = datetime.now().replace(microsecond=0)
        route = Route(index=1, name="To work", corridors={"AMBASSADOR CAFFERY PKWY"},
                      corridor_labels=["Ambassador Caffery Pkwy"], depart_minutes=440,
                      days={0, 1, 2, 3, 4}, path=PATH, radius_m=250)
        with tempfile.TemporaryDirectory() as tmp:
            found = find_route_incidents(self._db(tmp, now), route, 90, now=now)
        ids = [f["location"] for f in found]
        self.assertIn("3500 AMBASSADOR CAFFERY PKWY", ids)     # in-section
        self.assertNotIn("8000 AMBASSADOR CAFFERY PKWY", ids)  # same road, off-section!
        self.assertIn("SETTLERS TRACE BLVD", ids)              # geographically on the line
        self.assertNotIn("AMBASSADOR CAFFERY PKWY", ids)       # unlocated; no whole-road fallback
        self.assertNotIn("JOHNSTON ST", ids)
        by_loc = {f["location"]: f for f in found}
        self.assertFalse(by_loc["3500 AMBASSADOR CAFFERY PKWY"]["approx"])
        self.assertIsNotNone(by_loc["3500 AMBASSADOR CAFFERY PKWY"]["dist_m"])


class ParseEmailTests(unittest.TestCase):
    def test_parses_clean_body(self):
        body = ("LAF911_ROUTE_1_NAME=To work\n"
                "LAF911_ROUTE_1_PATH=30.20000,-92.02000; 30.21000,-92.02000\n"
                "LAF911_ROUTE_1_RADIUS_M=250\n"
                "LAF911_ROUTE_1_DEPART=07:20\n"
                "LAF911_ROUTE_1_DAYS=mon-fri\n")
        slots = parse_route_email(body)
        self.assertEqual(list(slots.keys()), ["1"])
        self.assertEqual(slots["1"]["DEPART"], "07:20")
        self.assertEqual(len(_parse_path(slots["1"]["PATH"])), 2)

    def test_survives_wrapping_quoting_and_signatures(self):
        # A mail client hard-wraps the PATH mid-value and quotes the message.
        body = ("Hi me,\n"
                "> LAF911_ROUTE_2_NAME=Home\n"
                "> LAF911_ROUTE_2_PATH=30.20000,-92.02000; 30.20500,-92.02000;\n"
                ">  30.21000,-92.02000; 30.21500,-92.02100\n"
                "> LAF911_ROUTE_2_DEPART=17:00\n"
                "\n"
                "Sent from my iPhone\n")
        slots = parse_route_email(body)
        self.assertEqual(len(_parse_path(slots["2"]["PATH"])), 4)
        self.assertEqual(slots["2"]["NAME"], "Home")

    def test_ignores_non_route_lines(self):
        self.assertEqual(parse_route_email("hello\nno config here\nx=y"), {})


class ApplyAndLoadTests(unittest.TestCase):
    def test_apply_then_load_and_delete(self):
        store = _FakeStore()
        summaries = apply_route_slots(store, {
            "1": {"NAME": "To work", "PATH": "30.20000,-92.02000; 30.21000,-92.02000",
                  "RADIUS_M": "250", "DEPART": "07:20", "DAYS": "mon-fri"},
        })
        self.assertTrue(any("saved" in s for s in summaries))
        cfg = load_route_config(store)
        self.assertTrue(cfg.enabled)          # mailbox routes imply enabled
        self.assertEqual(len(cfg.routes), 1)
        self.assertEqual(cfg.routes[0].name, "To work")
        self.assertEqual(len(cfg.routes[0].path), 2)

        # Broken slot is ignored with a warning, not saved.
        warn = apply_route_slots(store, {"3": {"NAME": "Nope", "DEPART": "bad"}})
        self.assertTrue(any("ignored" in s for s in warn))
        self.assertEqual(len(load_route_config(store).routes), 1)

        # Delete.
        summaries = apply_route_slots(store, {"1": {"DELETE": "true"}})
        self.assertTrue(any("deleted" in s for s in summaries))
        self.assertEqual(load_route_config(store).routes, [])

    def test_mailbox_slot_overrides_env_slot(self):
        env = {"LAF911_ROUTE_1_NAME": "Env route", "LAF911_ROUTE_1_CORRIDORS": "Johnston St",
               "LAF911_ROUTE_1_DEPART": "08:00"}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            store = _FakeStore()
            apply_route_slots(store, {"1": {"NAME": "Mail route", "CORRIDORS": "Pinhook Rd",
                                            "DEPART": "07:00", "DAYS": "daily"}})
            cfg = load_route_config(store)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(len(cfg.routes), 1)
        self.assertEqual(cfg.routes[0].name, "Mail route")
        self.assertEqual(cfg.routes[0].depart_minutes, 7 * 60)


class PollTests(unittest.TestCase):
    def test_accepts_self_rejects_strangers_and_confirms(self):
        import logging
        store = _FakeStore()
        sent = []
        msgs = [
            {"from": "stranger@evil.com", "subject": "LAF911 route",
             "body": "LAF911_ROUTE_1_NAME=Hacked\nLAF911_ROUTE_1_CORRIDORS=X St\nLAF911_ROUTE_1_DEPART=03:00"},
            {"from": "me@gmail.com", "subject": "LAF911 route",
             "body": "LAF911_ROUTE_1_NAME=To work\nLAF911_ROUTE_1_CORRIDORS=Pinhook Rd\nLAF911_ROUTE_1_DEPART=07:20"},
        ]
        n = poll_route_inbox(store, logging.getLogger("t"), digest_cfg=_cfg(),
                             fetch=lambda cfg: msgs, send=lambda c, h, s: sent.append((s, h)))
        self.assertEqual(n, 1)                              # stranger ignored
        cfg = load_route_config(store)
        self.assertEqual(cfg.routes[0].name, "To work")     # not "Hacked"
        self.assertEqual(len(sent), 1)                      # confirmation reply
        self.assertIn("route settings saved", sent[0][0])
        self.assertIn("To work", sent[0][1])

    def test_confirmation_escapes_html_and_includes_command_reference(self):
        """The route NAME comes from an email body; even though only the
        owner can send here, it must never land in the confirmation HTML
        unescaped. And every confirmation carries the full command syntax."""
        import logging
        store = _FakeStore()
        sent = []
        msgs = [{"from": "me@gmail.com", "subject": "LAF911 route",
                 "body": ("LAF911_ROUTE_1_NAME=<script>alert(1)</script>\n"
                          "LAF911_ROUTE_1_CORRIDORS=Pinhook Rd\n"
                          "LAF911_ROUTE_1_DEPART=07:20")}]
        n = poll_route_inbox(store, logging.getLogger("t"), digest_cfg=_cfg(),
                             fetch=lambda cfg: msgs, send=lambda c, h, s: sent.append(h))
        self.assertEqual(n, 1)
        html = sent[0]
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        # Full command reference present in every confirmation.
        for needle in ("Command reference", "_NAME=", "_PATH=", "_RADIUS_M=",
                       "_CORRIDORS=", "_DEPART=", "_DAYS=", "_DELETE=true"):
            self.assertIn(needle, html)

    def test_never_raises_and_backs_off(self):
        import logging
        store = _FakeStore()

        def boom(cfg):
            raise RuntimeError("imap down")

        n = poll_route_inbox(store, logging.getLogger("t"), digest_cfg=_cfg(),
                             fetch=boom, send=lambda *a: None)
        self.assertEqual(n, 0)
        self.assertGreater(float(store.meta["route_inbox_retry_epoch"]), 0)
        # While backed off, fetch is not even attempted.
        calls = []
        poll_route_inbox(store, logging.getLogger("t"), digest_cfg=_cfg(),
                         fetch=lambda cfg: calls.append(1) or [], send=lambda *a: None)
        self.assertEqual(calls, [])

    def test_disabled_by_env(self):
        os.environ["LAF911_ROUTE_INBOX"] = "off"
        try:
            n = poll_route_inbox(_FakeStore(), None, digest_cfg=_cfg(),
                                 fetch=lambda cfg: [{"from": "me@gmail.com", "subject": "LAF911",
                                                     "body": "LAF911_ROUTE_1_NAME=X"}],
                                 send=lambda *a: None)
        finally:
            os.environ.pop("LAF911_ROUTE_INBOX", None)
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
