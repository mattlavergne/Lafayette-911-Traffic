"""Web ↔ Pi route sync: seeding, applying page edits, pushing email edits,
conflicts, and never raising. The Worker is faked in-process (same protocol
as scripts/cloudflare_trafficmap_worker.js); no network."""

import json
import unittest
from unittest import mock

from lafayette911 import route_sync
from lafayette911.route_alerts import MAIL_ROUTES_META_KEY, load_route_config

URL = "https://example.test/trafficmap/api/routes"
TOKEN = "correct-horse-battery-staple"
PATH = "30.20000,-92.02000; 30.21000,-92.02000"


class _Store:
    def __init__(self, routes=None):
        self.meta = {}
        if routes is not None:
            self.meta[MAIL_ROUTES_META_KEY] = json.dumps(routes)

    def _meta_get(self, k):
        return self.meta.get(k)

    def _meta_set(self, k, v):
        self.meta[k] = v


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class _FakeWorker:
    """Minimal stand-in for the Worker's /api/routes protocol."""

    def __init__(self):
        self.doc = {"version": 0, "routes": {}}
        self.status = {}
        self.acks = []
        self.down = False

    def _auth(self, headers):
        return headers.get("Authorization") == "Bearer " + TOKEN

    def get(self, url, headers=None, timeout=None):
        if self.down:
            raise ConnectionError("offline")
        if not self._auth(headers):
            return _Resp(401, {})
        return _Resp(200, dict(self.doc, pi=self.status))

    def put(self, url, headers=None, timeout=None, data=None):
        body = json.loads(data)
        if body["base_version"] != self.doc["version"]:
            return _Resp(409, dict(self.doc))
        self.doc = {"version": self.doc["version"] + 1, "routes": body["routes"], "updated_by": body["by"]}
        return _Resp(200, dict(self.doc))

    def post(self, url, headers=None, timeout=None, data=None):
        assert url.endswith("/ack")
        self.acks.append(json.loads(data))
        self.status = {"applied_version": self.acks[-1]["version"]}
        return _Resp(200, self.status)

    def web_save(self, routes):
        self.doc = {"version": self.doc["version"] + 1, "routes": routes, "updated_by": "web"}


def _route(name, depart="07:20", **extra):
    kv = {"NAME": name, "PATH": PATH, "DEPART": depart, "DAYS": "mon-fri"}
    kv.update(extra)
    return kv


class RouteSyncTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict("os.environ", {"LAF911_ROUTE_SYNC_URL": URL, "LAF911_ROUTE_SYNC_TOKEN": TOKEN})
        p.start()
        self.addCleanup(p.stop)
        self.worker = _FakeWorker()

    def test_unconfigured_does_nothing(self):
        with mock.patch.dict("os.environ", {"LAF911_ROUTE_SYNC_URL": ""}):
            self.assertEqual(route_sync.sync_routes(_Store(), self.worker, None), "")

    def test_existing_routes_seed_the_page(self):
        store = _Store({"1": _route("To work")})
        self.assertEqual(route_sync.sync_routes(store, self.worker, None), "seeded")
        self.assertEqual(self.worker.doc["routes"]["1"]["NAME"], "To work")
        self.assertEqual(self.worker.acks[-1]["version"], 1)
        # Nothing changed → nothing to do (and no ack spam).
        self.assertEqual(route_sync.sync_routes(store, self.worker, None), "")
        self.assertEqual(len(self.worker.acks), 1)

    def test_page_edit_is_applied_on_the_pi(self):
        store = _Store({"2": _route("Home", "17:00")})
        route_sync.sync_routes(store, self.worker, None)
        self.worker.web_save({"2": _route("Home", "17:00", DEPART_FRI="12:00")})
        self.assertEqual(route_sync.sync_routes(store, self.worker, None), "applied")
        r = load_route_config(store).routes[0]
        self.assertEqual(r.depart_for(4), 12 * 60)
        self.assertEqual(r.depart_for(3), 17 * 60)
        self.assertTrue(self.worker.acks[-1]["applied"])

    def test_page_delete_removes_route(self):
        store = _Store({"1": _route("To work"), "2": _route("Home", "17:00")})
        route_sync.sync_routes(store, self.worker, None)
        self.worker.web_save({"2": self.worker.doc["routes"]["2"]})
        route_sync.sync_routes(store, self.worker, None)
        self.assertEqual([r.index for r in load_route_config(store).routes], [2])

    def test_email_edit_is_pushed_to_the_page(self):
        store = _Store({"1": _route("To work")})
        route_sync.sync_routes(store, self.worker, None)
        store.meta[MAIL_ROUTES_META_KEY] = json.dumps({"1": _route("To work", "07:45")})
        self.assertEqual(route_sync.sync_routes(store, self.worker, None), "pushed")
        self.assertEqual(self.worker.doc["routes"]["1"]["DEPART"], "07:45")
        self.assertEqual(self.worker.doc["updated_by"], "pi")

    def test_invalid_remote_slots_are_dropped(self):
        store = _Store({"1": _route("To work")})
        route_sync.sync_routes(store, self.worker, None)
        self.worker.web_save({"1": _route("To work"), "3": {"NAME": "no time or path"}})
        route_sync.sync_routes(store, self.worker, None)
        self.assertEqual(sorted(json.loads(store.meta[MAIL_ROUTES_META_KEY])), ["1"])

    def test_failures_never_raise_and_back_off(self):
        store = _Store({"1": _route("To work")})
        self.worker.down = True
        self.assertEqual(route_sync.sync_routes(store, self.worker, None), "error")
        self.worker.down = False
        self.assertEqual(route_sync.sync_routes(store, self.worker, None), "")   # backing off
        store.meta["route_sync_retry_epoch"] = "0"
        self.assertEqual(route_sync.sync_routes(store, self.worker, None), "seeded")

    def test_wrong_token_is_an_error_not_a_crash(self):
        with mock.patch.dict("os.environ", {"LAF911_ROUTE_SYNC_TOKEN": "nope"}):
            self.assertEqual(route_sync.sync_routes(_Store({"1": _route("W")}), self.worker, None), "error")


if __name__ == "__main__":
    unittest.main()
