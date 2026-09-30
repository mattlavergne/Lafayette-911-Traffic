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

Unset → does nothing. Never raises; failures back off for 15 minutes.
"""

import json
import os
import time
from typing import Dict

from lafayette911.route_alerts import MAIL_ROUTES_META_KEY, ROUTE_KV_KEYS, _route_from_kv

_VERSION_KEY = "route_sync_version"
_ACK_KEY = "route_sync_ack_epoch"
_RETRY_KEY = "route_sync_retry_epoch"
ACK_EVERY_S = 30 * 60
USER_AGENT = "Lafayette-911-Traffic Pi route sync"


def _sync_settings():
    url = os.getenv("LAF911_ROUTE_SYNC_URL", "").strip()
    token = os.getenv("LAF911_ROUTE_SYNC_TOKEN", "").strip()
    return (url, token) if url and token else (None, None)


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


def sync_routes(store, session, logger) -> str:
    """One sync pass. Returns what happened ("", "applied", "pushed",
    "seeded", "error") — for tests and logs. Never raises."""
    from lafayette911.utils import log_event

    url, token = _sync_settings()
    if not url or session is None:
        return ""
    try:
        if time.time() < float(store._meta_get(_RETRY_KEY) or 0):
            return ""
    except Exception:
        pass

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

        last_ack = float(store._meta_get(_ACK_KEY) or 0)
        if outcome or time.time() - last_ack >= ACK_EVERY_S:
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
            log_event(logger, "route_sync_error", error=str(exc))
        except Exception:
            pass
        return "error"
