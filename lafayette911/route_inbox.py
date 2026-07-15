"""Configure commute routes BY EMAIL — zero Pi configuration.

The Pi already has Gmail credentials for the daily digest, and a Gmail App
Password works for IMAP (reading) exactly like SMTP (sending). So the route
builder in the web page composes a config email; you send it **from the same
Gmail account the Pi uses, to itself**; the service notices it within a
cycle, saves the route into SQLite, and replies with a confirmation. Nothing
to edit over SSH, ever.

Message format (what the builder generates — plain env-style lines):

    Subject: LAF911 route
    Body:
        LAF911_ROUTE_1_NAME=To work
        LAF911_ROUTE_1_PATH=30.22410,-92.01984; 30.22800,-92.02100; ...
        LAF911_ROUTE_1_RADIUS_M=250
        LAF911_ROUTE_1_CORRIDORS=Ambassador Caffery Pkwy | Kaliste Saloom Rd
        LAF911_ROUTE_1_DEPART=07:20
        LAF911_ROUTE_1_DAYS=mon-fri

A body line ``LAF911_ROUTE_1_DELETE=true`` removes slot 1 instead.

Safety rules:
- Only UNSEEN messages whose subject contains ``LAF911`` are considered.
- Only messages **from the account itself** (the digest From/To address) are
  accepted; anything else is ignored and logged. (Header spoofing is
  theoretically possible, but Gmail's DMARC enforcement makes it a
  non-concern for a hobby mailbox — and the blast radius is "your own route
  alerts change".)
- Mail clients hard-wrap long lines, so the parser rejoins continuation
  lines (a traced PATH easily exceeds 78 characters).
- Polling failures back off to hourly and never touch the collection loop.
"""

import html as _html
import os
import re
import time
from typing import Dict, List, Optional

from lafayette911.daily_digest import DigestConfig, load_digest_config, send_digest
from lafayette911.route_alerts import (
    MAIL_ROUTES_META_KEY,
    ROUTE_KV_KEYS,
    _fmt_depart,
    _route_from_kv,
)

_KV_LINE = re.compile(r"^LAF911_ROUTE_(\d{1,2})_([A-Z_]+)\s*=\s*(.*)$")

# Every confirmation email carries the full command syntax, so the owner
# never has to dig for it when editing a route from their phone.
COMMANDS_REFERENCE_HTML = (
    '<div style="background:#f6f7f9;border:1px solid #e7e9ee;border-radius:12px;'
    'padding:12px 14px;margin-top:14px;">'
    '<div style="font-size:11px;font-weight:800;color:#8a919e;text-transform:uppercase;'
    'letter-spacing:0.06em;padding-bottom:6px;">📖 Command reference</div>'
    '<div style="font-size:12px;color:#5c6470;padding-bottom:8px;">Email these lines to this '
    'address (subject must contain <b>LAF911</b>, and it only listens to messages from itself). '
    'Slots run 1–20; to-work and home are usually 1 and 2. The easiest way to build the '
    'PATH line is the route icon in the map app.</div>'
    '<pre style="margin:0;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;'
    'line-height:1.7;color:#171a21;white-space:pre-wrap;">'
    'LAF911_ROUTE_&lt;n&gt;_NAME=To work            display name for the alert\n'
    'LAF911_ROUTE_&lt;n&gt;_PATH=lat,lng; lat,lng   drawn route (section-precise)\n'
    'LAF911_ROUTE_&lt;n&gt;_RADIUS_M=100            match distance around the path (m)\n'
    'LAF911_ROUTE_&lt;n&gt;_CORRIDORS=Road A | Road B   whole-road matching (no path)\n'
    'LAF911_ROUTE_&lt;n&gt;_DEPART=07:20            departure, 24-hour local time\n'
    'LAF911_ROUTE_&lt;n&gt;_DAYS=mon-fri            or: daily, weekends, mon,wed,fri\n'
    'LAF911_ROUTE_&lt;n&gt;_DELETE=true             remove slot n entirely</pre>'
    '</div>'
)


def _derive_imap_host(smtp_host: str) -> str:
    host = str(smtp_host or "").strip()
    if host.startswith("smtp."):
        return "imap." + host[len("smtp."):]
    return os.getenv("LAF911_IMAP_HOST", "imap.gmail.com")


def parse_route_email(body: str) -> Dict[str, Dict[str, str]]:
    """Extract per-slot route settings from a (possibly mangled) email body.

    Handles reply-quoting ('> '), hard-wrapped values (continuation lines are
    appended to the previous key), and ignores every non-LAF911 line
    (signatures, disclaimers, the works). Returns {"1": {"NAME": ..., ...}}.
    """
    slots: Dict[str, Dict[str, str]] = {}
    last: Optional[tuple] = None  # (slot, key)
    for raw_line in str(body or "").splitlines():
        line = raw_line.strip()
        line = re.sub(r"^(?:>\s*)+", "", line)  # strip reply-quoting
        if not line:
            last = None
            continue
        m = _KV_LINE.match(line)
        if m:
            slot, key, value = m.group(1), m.group(2), m.group(3).strip()
            if key in ROUTE_KV_KEYS or key == "DELETE":
                slots.setdefault(slot, {})[key] = value
                last = (slot, key)
            else:
                last = None
        elif last is not None and "=" not in line:
            # Continuation of a hard-wrapped value (e.g. a long PATH).
            slot, key = last
            slots[slot][key] = (slots[slot][key] + " " + line).strip()
        else:
            last = None
    return slots


def apply_route_slots(store, slots: Dict[str, Dict[str, str]]) -> List[str]:
    """Merge parsed slots into the stored mailbox routes. Returns
    human-readable summaries for the confirmation email."""
    import json

    try:
        current = json.loads(store._meta_get(MAIL_ROUTES_META_KEY) or "{}")
        if not isinstance(current, dict):
            current = {}
    except Exception:
        current = {}

    summaries: List[str] = []
    for slot, kv in sorted(slots.items(), key=lambda e: int(e[0])):
        if str(kv.get("DELETE", "")).strip().lower() in {"1", "true", "yes"}:
            if slot in current:
                del current[slot]
                summaries.append("🗑️ Route %s deleted" % slot)
            else:
                summaries.append("Route %s was not set — nothing to delete" % slot)
            continue
        clean = {k: str(kv.get(k) or "") for k in ROUTE_KV_KEYS}
        route = _route_from_kv(int(slot), clean)
        if route is None:
            summaries.append("⚠️ Route %s ignored — needs a departure time plus a drawn path or road list" % slot)
            continue
        current[slot] = clean
        bits = ["departs %s" % _fmt_depart(route.depart_minutes)]
        if route.path:
            bits.append("%d waypoints (section-matched within %d m)" % (len(route.path), route.radius_m))
        if route.corridor_labels:
            bits.append("roads: %s" % ", ".join(route.corridor_labels[:6]))
        summaries.append("✅ Route %s '%s' saved — %s" % (slot, route.name, "; ".join(bits)))

    store._meta_set(MAIL_ROUTES_META_KEY, json.dumps(current, separators=(",", ":")))
    return summaries


def _extract_text(msg) -> str:
    """Best-effort text/plain body of an email.message.Message."""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload is not None:
                        return payload.decode(part.get_content_charset() or "utf-8", "replace")
            return ""
        payload = msg.get_payload(decode=True)
        if payload is None:
            return str(msg.get_payload() or "")
        return payload.decode(msg.get_content_charset() or "utf-8", "replace")
    except Exception:
        return ""


def fetch_unseen_route_emails(cfg: DigestConfig) -> List[Dict[str, str]]:
    """Unseen 'LAF911' messages from the mailbox (fetching marks them seen).

    Returns [{"from": addr, "subject": s, "body": text}, ...]. Raises on
    connection/auth trouble — the caller owns backoff.
    """
    import email
    import email.utils
    import imaplib

    host = os.getenv("LAF911_IMAP_HOST") or _derive_imap_host(cfg.smtp_host)
    port = int(os.getenv("LAF911_IMAP_PORT", "993") or 993)
    out: List[Dict[str, str]] = []
    conn = imaplib.IMAP4_SSL(host, port)
    try:
        conn.login(cfg.smtp_user, cfg.smtp_pass)
        conn.select("INBOX")
        typ, data = conn.search(None, "UNSEEN", "SUBJECT", '"LAF911"')
        if typ != "OK" or not data or not data[0]:
            return out
        for num in data[0].split():
            typ2, msg_data = conn.fetch(num, "(RFC822)")  # marks \Seen
            if typ2 != "OK" or not msg_data or msg_data[0] is None:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            out.append({
                "from": email.utils.parseaddr(msg.get("From", ""))[1].strip().lower(),
                "subject": str(msg.get("Subject", "")),
                "body": _extract_text(msg),
            })
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return out


def poll_route_inbox(store, logger, digest_cfg: Optional[DigestConfig] = None,
                     fetch=fetch_unseen_route_emails, send=send_digest) -> int:
    """Check the mailbox for route-config emails; apply and confirm.

    Called once per service cycle. Never raises. Returns the number of
    config emails processed. Disabled with LAF911_ROUTE_INBOX=off.
    """
    from lafayette911.utils import log_event

    if os.getenv("LAF911_ROUTE_INBOX", "").strip().lower() in {"off", "false", "0", "no"}:
        return 0
    cfg = digest_cfg or load_digest_config()
    if not (cfg.smtp_host and cfg.smtp_user and cfg.smtp_pass and cfg.mail_to):
        return 0

    # Backoff: after a failure, stay quiet for an hour instead of hammering
    # IMAP (and the log) every cycle.
    try:
        retry_at = float(store._meta_get("route_inbox_retry_epoch") or 0)
    except Exception:
        retry_at = 0
    if time.time() < retry_at:
        return 0

    allowed = {a.strip().lower() for a in (cfg.mail_to, cfg.smtp_user, cfg.mail_from) if a}
    processed = 0
    try:
        for message in fetch(cfg):
            sender = str(message.get("from") or "").strip().lower()
            if sender not in allowed:
                try:
                    log_event(logger, "route_inbox_ignored", sender_domain=sender.split("@")[-1] if "@" in sender else "?")
                except Exception:
                    pass
                continue
            slots = parse_route_email(message.get("body") or "")
            if not slots:
                continue
            summaries = apply_route_slots(store, slots)
            processed += 1
            try:
                log_event(logger, "route_inbox_applied", slots=sorted(slots.keys()))
            except Exception:
                pass
            # Confirmation reply so you know it took — sent to the same box.
            # Summaries include the route NAME from the email; escape it even
            # though only the owner can send here (defense in depth).
            try:
                items = "".join("<li>%s</li>" % _html.escape(s) for s in summaries)
                body_html = ("<html><body style=\"font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
                             "font-size:14px;color:#171a21;\"><p>🧭 <b>Route settings received.</b></p><ul>"
                             + items +
                             "</ul><p style=\"font-size:12px;color:#8a919e;\">Alerts arrive shortly before each departure. "
                             "An email replaces only the slots it mentions.</p>"
                             + COMMANDS_REFERENCE_HTML + "</body></html>")
                send(cfg, body_html, "🧭 LAF911: route settings saved")
            except Exception:
                pass
        store._meta_set("route_inbox_retry_epoch", "0")
    except Exception as exc:
        try:
            store._meta_set("route_inbox_retry_epoch", str(time.time() + 3600))
            log_event(logger, "route_inbox_error", error=str(exc))
        except Exception:
            pass
    return processed


if __name__ == "__main__":
    import argparse

    from lafayette911.config import load_config
    from lafayette911.state_store import StateStore

    parser = argparse.ArgumentParser(description="LAF911 route-config mailbox tools")
    parser.add_argument("--check", action="store_true", help="poll the mailbox once, apply any route emails")
    parser.add_argument("--list", action="store_true", help="show routes currently configured by email")
    args = parser.parse_args()

    app_cfg = load_config()
    store = StateStore(app_cfg.db_path, app_cfg.csv_path)
    try:
        if args.check:
            import logging
            n = poll_route_inbox(store, logging.getLogger("route-inbox"))
            print("processed %d route email(s)" % n)
        if args.list:
            import json
            raw = store._meta_get(MAIL_ROUTES_META_KEY) or "{}"
            data = json.loads(raw)
            if not data:
                print("no routes configured by email")
            for slot, kv in sorted(data.items(), key=lambda e: int(e[0])):
                print("route %s: %s" % (slot, json.dumps(kv, indent=2)))
        if not args.check and not args.list:
            parser.print_help()
    finally:
        store.close()
