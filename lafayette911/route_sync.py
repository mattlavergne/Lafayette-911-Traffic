"""Keep commute routes in sync with the map page — no email needed.

The Pi never accepts inbound connections. Instead the Cloudflare Worker that
already fronts the map (``scripts/cloudflare_trafficmap_worker.js``) holds a
small passcode-protected settings document; the map page edits it, and this
module polls it once per service cycle:

  - the page saved a newer version  → apply it to the Pi's route store
  - an email changed routes locally → push them up so the page shows them
  - the Worker has nothing yet      → seed it with the Pi's current routes,
                                      so existing routes appear in the page

Every so often (and after applying) the Pi acks, so the page can show "Pi
last checked in 3 min ago · changes applied".

    LAF911_ROUTE_SYNC_URL=https://mattlavergne.com/trafficmap/api/routes
    LAF911_ROUTE_SYNC_TOKEN=<the same passcode as the Worker's ROUTES_TOKEN>

The page can also ask for a TEST email "as if it were <day, time>"; the Pi
runs it on its next check (see :func:`lafayette911.route_alerts.run_route_test`)
and reports the outcome back to the page.

Unset → does nothing. Never raises; failures back off for 15 minutes (reset
immediately when the URL or passcode changes).

Check the connection by hand (read-only; the secrets file is root-only, so
it is handed over with sudo cat):

    .venv/bin/python -m lafayette911.route_sync --check \
        --env-file <(sudo cat /etc/laf911-secrets.env)
"""

import hashlib
import json
import os
import time
from datetime import datetime
from typing import Dict

from lafayette911.route_alerts import MAIL_ROUTES_META_KEY, ROUTE_KV_KEYS, _route_from_kv

_VERSION_KEY = "route_sync_version"
_ACK_KEY = "route_sync_ack_epoch"
_RETRY_KEY = "route_sync_retry_epoch"
_RETRY_FP_KEY = "route_sync_retry_fp"
_TEST_KEY = "route_sync_last_test"
ACK_EVERY_S = 30 * 60
USER_AGENT = "Lafayette-911-Traffic Pi route sync"


def _sync_settings():
    url = os.getenv("LAF911_ROUTE_SYNC_URL", "").strip()
    token = os.getenv("LAF911_ROUTE_SYNC_TOKEN", "").strip()
    return (url, token) if url and token else (None, None)


def _settings_fp(url: str, token: str) -> str:
    return hashlib.sha256(("%s\n%s" % (url, token)).encode()).hexdigest()[:12]


def _backoff_remaining(store, url: str, token: str) -> float:
    """Seconds left in the failure backoff — zero if the URL or passcode
    changed since the failure (fixing the settings takes effect at once)."""
    try:
        if store._meta_get(_RETRY_FP_KEY) not in (None, _settings_fp(url, token)):
            return 0.0
        return max(0.0, float(store._meta_get(_RETRY_KEY) or 0) - time.time())
    except Exception:
        return 0.0


def _clean(routes) -> Dict[str, Dict[str, str]]:
    """Only well-formed, usable slots, with every known key present."""
    out: Dict[str, Dict[str, str]] = {}
    if not isinstance(routes, dict):
        return out
    for slot, kv in routes.items():
        if not str(slot).isdigit() or not isinstance(kv, dict):
            continue
        clean = {k: str(kv.get(k) or "") for k in ROUTE_KV_KEYS}
        if _route_from_kv(int(slot), clean) is not None:
            out[str(int(slot))] = clean
    return out


def _local_routes(store) -> Dict[str, Dict[str, str]]:
    """Effective routes on the Pi: env slots, overridden by stored ones."""
    try:
        stored = json.loads(store._meta_get(MAIL_ROUTES_META_KEY) or "{}")
    except Exception:
        stored = {}
    merged = {}
    for i in range(1, 21):
        env_kv = {k: os.getenv("LAF911_ROUTE_%d_%s" % (i, k), "") for k in ROUTE_KV_KEYS}
        if any(env_kv.values()):
            merged[str(i)] = env_kv
    if isinstance(stored, dict):
        merged.update(stored)
    return _clean(merged)


def _stored_routes(store) -> Dict[str, Dict[str, str]]:
    try:
        stored = json.loads(store._meta_get(MAIL_ROUTES_META_KEY) or "{}")
    except Exception:
        stored = {}
    return _clean(stored)


def _http_error(what: str, resp) -> str:
    """Readable reason for a failed call. The Worker itself only answers
    200/401/409/503, so anything else — above all 403 — came from Cloudflare's
    security layer (Bot Fight Mode, a WAF rule…) before the Worker ran."""
    status = getattr(resp, "status_code", "?")
    msg = "%s → HTTP %s" % (what, status)
    if status == 401:
        return msg + " (passcode mismatch: LAF911_ROUTE_SYNC_TOKEN must equal the Worker's ROUTES_TOKEN)"
    if status == 503:
        return msg + " (the Worker is missing its ROUTES_KV binding or ROUTES_TOKEN secret)"
    headers = getattr(resp, "headers", None) or {}
    try:
        body = str(getattr(resp, "text", "") or "")[:2000]
    except Exception:
        body = ""
    cf = headers.get("cf-mitigated") or headers.get("Cf-Mitigated") or ""
    if status in (403, 429) or cf or "cloudflare" in body.lower():
        return msg + (" (blocked by Cloudflare security before reaching the Worker%s — "
                      "use the Worker's workers.dev address for LAF911_ROUTE_SYNC_URL, or "
                      "allow it under Security → Bots; see README)" % (", cf-mitigated=" + cf if cf else ""))
    return msg


def sync_routes(store, session, logger, config=None, run_test=None) -> str:
    """One sync pass. Returns what happened ("", "applied", "pushed",
    "seeded", "error") — for tests and logs. Never raises. ``config`` (the
    app Config) enables test-email requests from the page."""
    from lafayette911.utils import log_event

    url, token = _sync_settings()
    if not url or session is None:
        return ""
    if _backoff_remaining(store, url, token) > 0:
        return ""

    headers = {"Authorization": "Bearer " + token, "User-Agent": USER_AGENT,
               "Content-Type": "application/json"}
    outcome = ""
    try:
        resp = session.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(_http_error("GET " + url, resp))
        doc = resp.json()
        remote_version = int(doc.get("version") or 0)
        remote = _clean(doc.get("routes") or {})
        applied = int(store._meta_get(_VERSION_KEY) or 0)

        def push(routes, base):
            r = session.put(url, headers=headers, timeout=15,
                            data=json.dumps({"routes": routes, "base_version": base, "by": "pi"}))
            if r.status_code == 409:
                return None   # the page saved in between; apply it next cycle
            if r.status_code != 200:
                raise RuntimeError(_http_error("PUT", r))
            return int(r.json().get("version") or 0)

        if remote_version == 0:
            local = _local_routes(store)
            if local:
                new_version = push(local, 0)
                if new_version:
                    store._meta_set(_VERSION_KEY, str(new_version))
                    outcome = "seeded"
        elif remote_version != applied:
            # The page saved something new: it is now the truth. (A version
            # BELOW ours means the Worker's store was reset; adopt it too.)
            store._meta_set(MAIL_ROUTES_META_KEY, json.dumps(remote, separators=(",", ":")))
            store._meta_set(_VERSION_KEY, str(remote_version))
            outcome = "applied"
        elif _stored_routes(store) != remote:
            # A config email changed routes on the Pi since the last sync.
            new_version = push(_stored_routes(store), remote_version)
            if new_version:
                store._meta_set(_VERSION_KEY, str(new_version))
                outcome = "pushed"

        test = doc.get("test") if isinstance(doc.get("test"), dict) else None
        tested = False
        if (config is not None and test and test.get("id") and not test.get("result")
                and store._meta_get(_TEST_KEY) != test["id"]):
            # Mark first: a test that crashes the Pi must never loop.
            store._meta_set(_TEST_KEY, test["id"])
            tested = True
            try:
                at = datetime.strptime(str(test.get("at")), "%Y-%m-%dT%H:%M")
                if run_test is None:
                    from lafayette911.route_alerts import run_route_test as run_test
                result = run_test(config, store, session, logger, int(test.get("slot")), at)
            except Exception as exc:
                result = {"ok": False, "error": str(exc)[:300]}
            session.post(url.rstrip("/") + "/test/result", headers=headers, timeout=15,
                         data=json.dumps({"id": test["id"], "result": result}))
            try:
                log_event(logger, "route_test", slot=test.get("slot"), at=test.get("at"),
                          emailed=result.get("emailed"), error=result.get("error"))
            except Exception:
                pass

        last_ack = float(store._meta_get(_ACK_KEY) or 0)
        if outcome or tested or time.time() - last_ack >= ACK_EVERY_S:
            session.post(url.rstrip("/") + "/ack", headers=headers, timeout=15,
                         data=json.dumps({"version": int(store._meta_get(_VERSION_KEY) or 0),
                                          "applied": outcome == "applied"}))
            store._meta_set(_ACK_KEY, str(time.time()))
        store._meta_set(_RETRY_KEY, "0")
        if outcome:
            try:
                log_event(logger, "route_sync", outcome=outcome, version=store._meta_get(_VERSION_KEY))
            except Exception:
                pass
        return outcome
    except Exception as exc:
        try:
            store._meta_set(_RETRY_KEY, str(time.time() + 900))
            store._meta_set(_RETRY_FP_KEY, _settings_fp(url, token))
            log_event(logger, "route_sync_error", error=str(exc))
        except Exception:
            pass
        return "error"


class _ReadOnlyStore:
    """app_meta reader for --check: never writes to the service's database."""

    def __init__(self, db_path: str):
        import sqlite3
        self.conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)

    def _meta_get(self, key):
        try:
            row = self.conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None
        except Exception:
            return None


def _load_env_file(path: str) -> None:
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def check(db_path: str, session) -> int:
    """Print a plain-English diagnosis of the Pi ↔ page connection."""
    url, token = _sync_settings()
    print("Route sync check")
    if not url:
        print("  ✗ LAF911_ROUTE_SYNC_URL / LAF911_ROUTE_SYNC_TOKEN not set "
              "(did you pass --env-file, and are both lines in it?)")
        return 1
    print("  URL:      %s" % url)
    print("  passcode: %d characters, ends …%s" % (len(token), token[-4:]))
    store = None
    try:
        store = _ReadOnlyStore(db_path)
        wait = _backoff_remaining(store, url, token)
        if wait > 0:
            print("  ⏳ the service is backing off after an error; next try in %d min" % (wait // 60 + 1))
    except Exception as exc:
        print("  (couldn't read the Pi's database at %s: %s)" % (db_path, exc))
    headers = {"Authorization": "Bearer " + token, "User-Agent": USER_AGENT}
    try:
        resp = session.get(url, headers=headers, timeout=15)
    except Exception as exc:
        print("  ✗ couldn't reach it: %s" % exc)
        return 1
    if resp.status_code != 200:
        print("  ✗ %s" % _http_error("GET", resp))
        return 1
    doc = resp.json()
    routes = _clean(doc.get("routes") or {})
    print("  ✓ connected — the page has %d route(s), version %s" % (len(routes), doc.get("version")))
    for slot, kv in sorted(routes.items(), key=lambda e: int(e[0])):
        print("      %s: %s" % (slot, kv.get("NAME")))
    pi = doc.get("pi") or {}
    print("  Pi last checked in: %s" % (pi.get("seen_at") or "never"))
    if store is not None:
        local = _local_routes(store)
        print("  routes on this Pi: %s" % (", ".join("%s: %s" % (k, v.get("NAME")) for k, v in sorted(local.items()))
                                        or "none"))
        applied = int(store._meta_get(_VERSION_KEY) or 0)
        version = int(doc.get("version") or 0)
        if version == 0:
            nxt = "upload this Pi's routes to the page" if local else "nothing (no routes anywhere yet)"
        elif version != applied:
            nxt = "apply the page's version %d" % version
        else:
            nxt = "nothing — in sync" if _stored_routes(store) == routes else "upload an emailed change"
        print("  next service check will: %s" % nxt)
        if not pi.get("seen_at"):
            print("  → if the service is running with these same settings, that happens within ~5 min.")
    return 0


if __name__ == "__main__":
    import argparse

    import requests

    from lafayette911.config import load_config

    parser = argparse.ArgumentParser(description="LAF911 route sync tools")
    parser.add_argument("--check", action="store_true", help="diagnose the connection (read-only)")
    parser.add_argument("--env-file", help="read settings from this env file first")
    args = parser.parse_args()
    if args.env_file:
        _load_env_file(args.env_file)
    if not args.check:
        parser.print_help()
        raise SystemExit(0)
    raise SystemExit(check(load_config().db_path, requests.Session()))
