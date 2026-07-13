"""Daily digest email: what happened in the last 24 hours.

The service (``main.py``) calls :func:`maybe_send_daily_digest` once per
cycle; the digest actually sends at most once per local day, at or after the
configured hour, and the "already sent today" marker lives in SQLite so
restarts can't double-send.

Privacy / credentials
---------------------
SMTP settings come ONLY from environment variables (systemd
``EnvironmentFile=`` on the Pi is the intended home). Nothing about the
mailbox — address, password, or provider — ever appears in this repository,
its logs, or the published map. See README "Daily digest email".

Design notes
------------
* Stats are computed straight from the SQLite archive, so they are correct
  across service restarts (only process uptime/cycle counters are
  in-memory, and they are labelled as such).
* The HTML is deliberately old-school email HTML: nested tables, inline
  styles, no external images or fonts (many clients block them), emoji and
  unicode block characters (▁▂▃▅▇) for the visuals. It renders well in
  Gmail, Apple Mail, and Outlook.
* ``python -m lafayette911.daily_digest --preview digest.html`` renders a
  digest from the local database without sending anything — use it to admire
  or debug the layout. ``--send`` sends immediately, ignoring the schedule.
"""

import html as _html
import os
import re
import smtplib
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate
from typing import Dict, List, Optional, Tuple

from lafayette911.corridors import corridor_ids

# ── categories (mirrors the web page's grouping, with emoji) ──────────────
_CATEGORIES: List[Tuple[str, str, str, Tuple[str, ...]]] = [
    # (label, emoji, bar color, keywords)
    ("Accidents", "🚗", "#ef4444", ("ACCIDENT", "CRASH", "COLLISION", "WRECK", "MVA", "MVC")),
    ("Fire", "🔥", "#f97316", ("FIRE", "SMOKE")),
    ("Hazards", "⚠️", "#eab308", ("HAZARD", "SPILL", "DEBRIS", "OBSTRUCT", "FLOOD", "TREE", "POWER LINE", "LINE DOWN", "ANIMAL")),
    ("Signals", "🚦", "#3b82f6", ("SIGNAL", "TRAFFIC CONTROL", "TRAFFIC LIGHT", "MALFUNCTION")),
    ("Stalled", "🛑", "#a855f7", ("STALL", "DISABLED", "STUCK", "ABANDONED")),
    ("Medical / rescue", "🚑", "#10b981", ("MEDICAL", "RESCUE", "INJURY", "AMBULANCE", "OVERDOSE")),
]

_AGENCIES: List[Tuple[str, str, str]] = [
    ("Police", "👮", r"POLICE|\bLPD\b"),
    ("Sheriff", "🤠", r"SHERIFF|\bLPSO\b"),
    ("Fire dept.", "🚒", r"FIRE|\bLFD\b"),
    ("EMS", "🚑", r"\bEMS\b|AMBULANCE|ACADIAN|\bMEDIC"),
]

_SPARK = "▁▂▃▄▅▆▇█"

_REPORTED_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})\s*(AM|PM)", re.I)


def categorize(cause: str) -> str:
    c = str(cause or "").upper()
    for label, _emoji, _color, keywords in _CATEGORIES:
        if any(k in c for k in keywords):
            return label
    return "Other"


def _parse_created_at(value: str) -> Optional[datetime]:
    s = str(value or "").strip().replace("Z", "+00:00")
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        # Compare naïvely in UTC: created_at is written as UTC.
        return dt.replace(tzinfo=None)
    except ValueError:
        return None


def _reported_hour(reported: str) -> Optional[int]:
    m = _REPORTED_RE.search(str(reported or ""))
    if not m:
        return None
    hh = int(m.group(4)) % 12
    if m.group(6).upper() == "PM":
        hh += 12
    return hh


# ── configuration ──────────────────────────────────────────────────────────
@dataclass
class DigestConfig:
    enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    mail_to: str
    mail_from: str
    send_hour: int       # local hour of day (0-23) to send at/after
    map_url: str         # optional public map link for the footer


def load_digest_config() -> DigestConfig:
    user = os.getenv("LAF911_DIGEST_SMTP_USER", "")
    return DigestConfig(
        enabled=os.getenv("LAF911_DIGEST_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"},
        smtp_host=os.getenv("LAF911_DIGEST_SMTP_HOST", "smtp.gmail.com"),
        smtp_port=int(os.getenv("LAF911_DIGEST_SMTP_PORT", "587") or 587),
        smtp_user=user,
        smtp_pass=os.getenv("LAF911_DIGEST_SMTP_PASS", ""),
        mail_to=os.getenv("LAF911_DIGEST_TO", user),
        mail_from=os.getenv("LAF911_DIGEST_FROM", user),
        send_hour=int(os.getenv("LAF911_DIGEST_HOUR", "7") or 7),
        map_url=os.getenv("LAF911_MAP_URL", ""),
    )


# ── stats ──────────────────────────────────────────────────────────────────
def collect_digest_stats(db_path: str, now: Optional[datetime] = None) -> Dict:
    """Everything the email reports, computed from the SQLite archive.

    Windows are on ``created_at`` (when WE collected the incident), which is
    exactly what "data collected in the past 24 hours" means.
    """
    now = now or datetime.utcnow()
    w_start = now - timedelta(hours=24)
    p_start = now - timedelta(hours=48)

    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        rows = conn.execute(
            """
            SELECT location, cause, reported, assisting, latitude, created_at,
                   geocode_attempts, hour_of_day,
                   weather_precip_prob, weather_precip_in,
                   nws_flash_flood_warning, nws_severe_thunderstorm_warning, nws_tornado_watch
            FROM incidents
            """
        ).fetchall()

        new_rows, prev_count = [], 0
        for r in rows:
            dt = _parse_created_at(r[5])
            if dt is None:
                continue
            if dt >= w_start:
                new_rows.append(r)
            elif dt >= p_start:
                prev_count += 1

        by_cat: Dict[str, int] = {}
        by_hour = [0] * 24
        corridors: Dict[str, int] = {}
        agencies: Dict[str, int] = {}
        located = pending = retired = 0
        rain_flagged = nws_flagged = 0
        for loc, cause, reported, assisting, lat, _created, attempts, hour_of_day, pprob, pin, f1, f2, f3 in new_rows:
            by_cat[categorize(cause)] = by_cat.get(categorize(cause), 0) + 1
            hh = hour_of_day if isinstance(hour_of_day, int) and 0 <= hour_of_day < 24 else _reported_hour(reported)
            if hh is not None:
                by_hour[hh] += 1
            for cid in corridor_ids(loc):
                corridors[cid] = corridors.get(cid, 0) + 1
            a_text = str(assisting or "").upper()
            for label, emoji, pattern in _AGENCIES:
                if re.search(pattern, a_text):
                    agencies[label] = agencies.get(label, 0) + 1
            if lat is not None:
                located += 1
            elif (attempts or 0) >= 3:
                retired += 1
            else:
                pending += 1
            if (pprob is not None and pprob >= 20) or (pin is not None and pin > 0.005):
                rain_flagged += 1
            if any(v == 1 for v in (f1, f2, f3)):
                nws_flagged += 1

        api_calls = conn.execute(
            "SELECT COUNT(*) FROM geocode_api_requests WHERE requested_at_epoch >= ?",
            (int(time.time()) - 86400,),
        ).fetchone()[0]
        blacklist_total = conn.execute("SELECT COUNT(*) FROM geocode_failed_locations").fetchone()[0]
        blacklist_active_24h = conn.execute(
            "SELECT COUNT(*) FROM geocode_failed_locations WHERE last_attempt_epoch >= ?",
            (int(time.time()) - 86400,),
        ).fetchone()[0]
        queue_now = conn.execute(
            "SELECT COUNT(*) FROM incidents WHERE latitude IS NULL AND COALESCE(geocode_attempts, 0) < 3"
        ).fetchone()[0]
        unmappable_total = conn.execute(
            "SELECT COUNT(*) FROM incidents WHERE latitude IS NULL AND COALESCE(geocode_attempts, 0) >= 3"
        ).fetchone()[0]
        archive_total = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        archive_located = conn.execute("SELECT COUNT(*) FROM incidents WHERE latitude IS NOT NULL").fetchone()[0]
    finally:
        conn.close()

    return {
        "generated_at_utc": now,
        "new_total": len(new_rows),
        "prev_total": prev_count,
        "by_category": by_cat,
        "by_hour": by_hour,
        "top_corridors": sorted(corridors.items(), key=lambda e: -e[1])[:5],
        "agencies": agencies,
        "geocode": {
            "located_24h": located,
            "pending_24h": pending,
            "retired_24h": retired,
            "api_calls_24h": api_calls,
            "queue_now": queue_now,
            "blacklist_total": blacklist_total,
            "blacklist_active_24h": blacklist_active_24h,
            "unmappable_total": unmappable_total,
        },
        "weather": {"rain_flagged": rain_flagged, "nws_flagged": nws_flagged},
        "archive": {"total": archive_total, "located": archive_located},
    }


# ── host (Raspberry Pi) vitals ─────────────────────────────────────────────
def collect_system_info() -> Dict:
    """Best-effort host stats: uptime/boot time (power-outage evidence),
    CPU temperature, disk headroom, load. Every probe is optional — anything
    unreadable is simply omitted from the email."""
    info: Dict = {}
    try:
        with open("/proc/uptime") as handle:
            up = float(handle.read().split()[0])
        info["uptime_seconds"] = up
        info["booted_at_epoch"] = time.time() - up
    except Exception:
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as handle:
            milli = int(handle.read().strip())
        if 0 < milli < 150000:
            info["cpu_temp_c"] = milli / 1000.0
    except Exception:
        pass
    try:
        import shutil
        usage = shutil.disk_usage("/")
        info["disk_free_bytes"] = usage.free
        info["disk_total_bytes"] = usage.total
    except Exception:
        pass
    try:
        info["load_1m"] = os.getloadavg()[0]
    except Exception:
        pass
    return info


# ── rendering ──────────────────────────────────────────────────────────────
def _fmt_uptime(seconds: float) -> str:
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return "%dd %dh %dm" % (d, h, m)
    if h:
        return "%dh %dm" % (h, m)
    return "%dm" % m


def _sparkline(values: List[int]) -> str:
    peak = max(values) if values and max(values) > 0 else 1
    return "".join(_SPARK[min(len(_SPARK) - 1, int(round((v / peak) * (len(_SPARK) - 1))))] for v in values)


def _bar_row(label: str, count: int, total: int, color: str) -> str:
    pct = int(round((count / total) * 100)) if total else 0
    width = max(3, pct) if count else 0
    e = _html.escape
    return (
        '<tr>'
        '<td style="padding:5px 8px 5px 0;font-size:13px;white-space:nowrap;">%s</td>'
        '<td style="width:100%%;padding:5px 0;">'
        '<div style="background:#eef0f4;border-radius:6px;height:12px;">'
        '<div style="background:%s;border-radius:6px;height:12px;width:%d%%;"></div>'
        '</div></td>'
        '<td style="padding:5px 0 5px 10px;font-size:13px;font-weight:700;text-align:right;white-space:nowrap;">%s'
        '<span style="color:#8a919e;font-weight:500;"> · %d%%</span></td>'
        '</tr>'
    ) % (e(label), color, width, "{:,}".format(count), pct)


def _stat_cell(value: str, label: str, hint: str = "") -> str:
    e = _html.escape
    return (
        '<td align="center" style="background:#f6f7f9;border-radius:14px;padding:12px 6px;" title="%s">'
        '<div style="font-size:22px;font-weight:800;color:#171a21;">%s</div>'
        '<div style="font-size:10.5px;color:#8a919e;text-transform:uppercase;letter-spacing:0.05em;padding-top:2px;">%s</div>'
        '</td>'
    ) % (e(hint), e(value), e(label))


def render_digest_html(stats: Dict, service_info: Optional[Dict] = None, map_url: str = "",
                       system_info: Optional[Dict] = None) -> str:
    e = _html.escape
    n = stats["new_total"]
    prev = stats["prev_total"]
    delta_txt = ""
    if prev > 0:
        pct = ((n - prev) / prev) * 100.0
        arrow, color = ("▲", "#ef4444") if pct >= 0 else ("▼", "#10b981")
        delta_txt = '<span style="color:%s;font-weight:700;">%s %.0f%%</span> vs the previous 24h (%d)' % (
            color, arrow, abs(pct), prev)
    g = stats["geocode"]
    when_local = time.strftime("%A, %B %-d %Y · %-I:%M %p", time.localtime())

    # Category table (always show every category, largest first).
    cat_rows = ""
    cats_sorted = sorted(_CATEGORIES, key=lambda c: -stats["by_category"].get(c[0], 0))
    for label, emoji, color, _kw in cats_sorted:
        cat_rows += _bar_row("%s %s" % (emoji, label), stats["by_category"].get(label, 0), n, color)
    if stats["by_category"].get("Other"):
        cat_rows += _bar_row("📋 Other", stats["by_category"]["Other"], n, "#8a919e")

    corridor_rows = ""
    for i, (cid, count) in enumerate(stats["top_corridors"]):
        corridor_rows += (
            '<tr><td style="padding:4px 8px 4px 0;color:#8a919e;font-size:12px;">%d</td>'
            '<td style="padding:4px 0;font-size:13px;">%s</td>'
            '<td style="padding:4px 0 4px 10px;font-size:13px;font-weight:700;text-align:right;">%d</td></tr>'
        ) % (i + 1, e(cid.title()), count)
    if not corridor_rows:
        corridor_rows = '<tr><td style="padding:4px 0;color:#8a919e;font-size:13px;">No located incidents in the window.</td></tr>'

    agency_bits = []
    for label, emoji, _p in _AGENCIES:
        if stats["agencies"].get(label):
            agency_bits.append("%s %s <b>%d</b>" % (emoji, e(label), stats["agencies"][label]))
    agency_line = " &nbsp;·&nbsp; ".join(agency_bits) if agency_bits else "No agency data in the window."

    spark = _sparkline(stats["by_hour"])
    peak_hour = stats["by_hour"].index(max(stats["by_hour"])) if max(stats["by_hour"]) > 0 else None
    peak_txt = ""
    if peak_hour is not None:
        ampm = "AM" if peak_hour < 12 else "PM"
        h12 = peak_hour % 12 or 12
        peak_txt = ' &nbsp;<span style="color:#8a919e;">peak %d %s · %d</span>' % (h12, ampm, max(stats["by_hour"]))

    weather_line = ""
    if stats["weather"]["rain_flagged"] or stats["weather"]["nws_flagged"]:
        weather_line = (
            '<tr><td colspan="3" style="padding:6px 0 0 0;font-size:12.5px;color:#5c6470;">'
            '🌧️ %d with rain in the forecast at report time &nbsp;·&nbsp; 📢 %d during an active NWS alert'
            '</td></tr>' % (stats["weather"]["rain_flagged"], stats["weather"]["nws_flagged"]))

    # Service health (live numbers only when the service itself sends).
    health_rows = ""
    def hrow(emoji, label, value):
        return ('<tr><td style="padding:4px 8px 4px 0;font-size:13px;white-space:nowrap;">%s %s</td>'
                '<td style="padding:4px 0;font-size:13px;font-weight:600;text-align:right;">%s</td></tr>'
                ) % (emoji, e(label), e(str(value)))
    if system_info and system_info.get("uptime_seconds") is not None:
        up = system_info["uptime_seconds"]
        boot_str = time.strftime("%b %-d, %-I:%M %p", time.localtime(system_info["booted_at_epoch"]))
        health_rows += hrow("🖥️", "Pi uptime", "%s (up since %s)" % (_fmt_uptime(up), boot_str))
        if up < 86400:
            # A reboot inside the reporting window usually means a power
            # outage or an update — call it out loudly.
            health_rows += ('<tr><td colspan="2" style="padding:4px 0;font-size:12.5px;'
                            'color:#b45309;font-weight:600;">⚡ The Pi rebooted within the last 24 hours '
                            '(power outage or update?)</td></tr>')
    if service_info:
        svc_up = time.time() - service_info.get("started_at_epoch", time.time())
        health_rows += hrow("⏱️", "Service uptime", _fmt_uptime(svc_up))
        if svc_up < 86400 and not (system_info and (system_info.get("uptime_seconds") or 0) < 86400):
            health_rows += ('<tr><td colspan="2" style="padding:4px 0;font-size:12.5px;'
                            'color:#b45309;font-weight:600;">♻️ The service restarted within the last '
                            '24 hours (the Pi itself stayed up — likely a code update or crash-restart)</td></tr>')
        health_rows += hrow("🔄", "Cycles this run", "{:,}".format(service_info.get("cycles_completed", 0)))
        health_rows += hrow("⚡", "Last cycle", "%.1f s" % service_info.get("last_cycle_seconds", 0.0))
        errs = service_info.get("errors_24h", 0)
        health_rows += hrow("✅" if errs == 0 else "❗", "Cycle errors (24h)", errs)
        rss = service_info.get("rss_bytes") or 0
        if rss:
            health_rows += hrow("🧠", "Memory (RSS)", "%.0f MB" % (rss / 1048576.0))
    if system_info:
        if system_info.get("cpu_temp_c") is not None:
            t = system_info["cpu_temp_c"]
            health_rows += hrow("🌡️" if t < 70 else "🥵", "CPU temperature", "%.1f °C" % t)
        if system_info.get("disk_free_bytes") is not None:
            free_gb = system_info["disk_free_bytes"] / 1073741824.0
            pct_free = 100.0 * system_info["disk_free_bytes"] / max(1, system_info.get("disk_total_bytes", 1))
            health_rows += hrow("💾" if pct_free >= 10 else "🚨", "Disk free",
                                "%.1f GB (%.0f%%)" % (free_gb, pct_free))
        if system_info.get("load_1m") is not None:
            health_rows += hrow("⚖️", "Load average (1 min)", "%.2f" % system_info["load_1m"])
    health_rows += hrow("🗄️", "Archive size", "{:,} incidents ({:,} mapped)".format(
        stats["archive"]["total"], stats["archive"]["located"]))

    geo_rows = (
        hrow("✅", "Geocoded on arrival", g["located_24h"]) +
        hrow("⏳", "Awaiting geocoding (new)", g["pending_24h"]) +
        hrow("🚫", "Given up (bad addresses, new)", g["retired_24h"]) +
        hrow("📡", "Google API calls used (24h)", g["api_calls_24h"]) +
        hrow("📥", "Queue waiting for budget", g["queue_now"]) +
        hrow("🧾", "Known-bad address list", "{:,} ({} touched in 24h)".format(g["blacklist_total"], g["blacklist_active_24h"]))
    )

    map_link = ""
    if map_url:
        map_link = ('<a href="%s" style="display:inline-block;background:#2f6fed;color:#ffffff;'
                    'text-decoration:none;font-weight:700;font-size:13px;padding:10px 22px;'
                    'border-radius:999px;">Open the live map →</a>') % e(map_url)

    def section(title, body):
        return ('<div style="background:#ffffff;border:1px solid #e7e9ee;border-radius:16px;'
                'padding:14px 16px;margin-top:12px;">'
                '<div style="font-size:11px;font-weight:800;color:#8a919e;text-transform:uppercase;'
                'letter-spacing:0.07em;padding-bottom:6px;">%s</div>%s</div>') % (title, body)

    return """<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#eef0f4;font-family:-apple-system,'SF Pro Text',Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#171a21;">
<table role="presentation" width="100%%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:22px 10px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%%;">
  <tr><td style="background:linear-gradient(135deg,#1d4ed8,#7c3aed);border-radius:20px 20px 0 0;padding:20px 22px;">
    <div style="font-size:19px;font-weight:800;color:#ffffff;">🚨 Lafayette Traffic — Daily Digest</div>
    <div style="font-size:12px;color:rgba(255,255,255,0.85);padding-top:3px;">%(when)s · data collected in the past 24 hours</div>
  </td></tr>
  <tr><td style="background:#f9fafb;border:1px solid #e7e9ee;border-top:none;border-radius:0 0 20px 20px;padding:16px 18px 20px 18px;">

    <table role="presentation" width="100%%" cellpadding="0" cellspacing="8"><tr>
      %(tile_new)s
      %(tile_mapped)s
      %(tile_queue)s
      %(tile_api)s
    </tr></table>
    <div style="font-size:12.5px;color:#5c6470;padding:2px 4px 0 4px;">%(delta)s</div>

    %(cats)s
    %(hours)s
    %(corridors)s
    %(agencies)s
    %(geocoding)s
    %(health)s

    <div style="text-align:center;padding:18px 0 4px 0;">%(map_link)s</div>
    <div style="font-size:10.5px;color:#8a919e;text-align:center;line-height:1.5;padding-top:10px;">
      Sent by your Raspberry Pi's Lafayette 911 Traffic service.<br>
      Unofficial hobby project — not affiliated with any agency. Data can be delayed or incorrect.<br>
      In an emergency, call 911.
    </div>
  </td></tr>
</table>
</td></tr></table>
</body></html>""" % {
        "when": e(when_local),
        "tile_new": _stat_cell("{:,}".format(n), "new incidents", "collected in the past 24 hours"),
        "tile_mapped": _stat_cell("{:,}".format(g["located_24h"]), "mapped", "geocoded successfully"),
        "tile_queue": _stat_cell("{:,}".format(g["queue_now"]), "in queue", "waiting for geocode budget"),
        "tile_api": _stat_cell("{:,}".format(g["api_calls_24h"]), "api calls", "Google Geocoding calls in 24h"),
        "delta": delta_txt,
        "cats": section("📊 By category", '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + cat_rows + weather_line + "</table>"),
        "hours": section("⏰ By hour (12a → 11p)",
                         '<div style="font-family:Menlo,Consolas,monospace;font-size:17px;letter-spacing:1px;color:#2f6fed;">%s</div>'
                         '<div style="font-size:11.5px;color:#8a919e;padding-top:3px;">each bar = one hour of the day%s</div>' % (spark, peak_txt)),
        "corridors": section("🛣️ Top corridors", '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + corridor_rows + "</table>"),
        "agencies": section("👥 Responding agencies", '<div style="font-size:13px;">%s</div>'
                            '<div style="font-size:10.5px;color:#8a919e;padding-top:4px;">One incident can involve several agencies.</div>' % agency_line),
        "geocoding": section("📍 Geocoding", '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + geo_rows + "</table>"),
        "health": section("⚙️ Service health", '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + health_rows + "</table>"),
        "map_link": map_link,
    }


# ── sending ────────────────────────────────────────────────────────────────
def send_digest(cfg: DigestConfig, html: str, subject: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.mail_from
    msg["To"] = cfg.mail_to
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(
        "Your daily Lafayette Traffic digest is an HTML email — "
        "open it in an HTML-capable mail client."
    )
    msg.add_alternative(html, subtype="html")

    if cfg.smtp_port == 465:
        with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
            smtp.login(cfg.smtp_user, cfg.smtp_pass)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(cfg.smtp_user, cfg.smtp_pass)
            smtp.send_message(msg)


def _subject_for(stats: Dict) -> str:
    return "🚨 Lafayette Traffic: %d incident%s in the past 24h (%s)" % (
        stats["new_total"], "" if stats["new_total"] == 1 else "s",
        time.strftime("%b %-d"),
    )


def maybe_send_daily_digest(config, store, service_info: Dict, logger,
                            digest_cfg: Optional[DigestConfig] = None,
                            send=send_digest) -> bool:
    """Send today's digest if it's due. Never raises; returns True on send.

    Dedupe lives in the app_meta table (survives restarts). After three
    failed attempts in one day it stops retrying until tomorrow, so a broken
    password can't spam the log every cycle forever.
    """
    from lafayette911.utils import log_event

    cfg = digest_cfg or load_digest_config()
    if not cfg.enabled:
        return False
    try:
        local = time.localtime()
        today = time.strftime("%Y-%m-%d", local)
        if local.tm_hour < cfg.send_hour:
            return False
        if store._meta_get("digest_last_sent_date") == today:
            return False
        if (store._meta_get("digest_failed_date") == today
                and int(store._meta_get("digest_failed_count") or 0) >= 3):
            return False
        if not (cfg.smtp_host and cfg.smtp_user and cfg.smtp_pass and cfg.mail_to):
            log_event(logger, "digest_skipped", reason="missing_smtp_config")
            store._meta_set("digest_failed_date", today)
            store._meta_set("digest_failed_count", "3")
            return False

        stats = collect_digest_stats(config.db_path)
        html = render_digest_html(stats, service_info=service_info, map_url=cfg.map_url,
                                  system_info=collect_system_info())
        send(cfg, html, _subject_for(stats))
        store._meta_set("digest_last_sent_date", today)
        store._meta_set("digest_failed_count", "0")
        log_event(logger, "digest_sent", new_incidents=stats["new_total"], to_domain=cfg.mail_to.split("@")[-1])
        return True
    except Exception as exc:
        try:
            today = time.strftime("%Y-%m-%d")
            prev = int(store._meta_get("digest_failed_count") or 0) if store._meta_get("digest_failed_date") == today else 0
            store._meta_set("digest_failed_date", today)
            store._meta_set("digest_failed_count", str(prev + 1))
            log_event(logger, "digest_error", error=str(exc), attempt=prev + 1)
        except Exception:
            pass
        return False


# ── CLI: preview or send immediately ───────────────────────────────────────
if __name__ == "__main__":
    import argparse

    from lafayette911.config import load_config

    parser = argparse.ArgumentParser(description="Lafayette 911 Traffic daily digest email")
    parser.add_argument("--preview", metavar="FILE", help="write the digest HTML to FILE (no email sent)")
    parser.add_argument("--send", action="store_true", help="send the digest NOW (ignores schedule and dedupe)")
    args = parser.parse_args()

    app_cfg = load_config()
    stats = collect_digest_stats(app_cfg.db_path)
    cfg = load_digest_config()
    html_out = render_digest_html(stats, service_info=None, map_url=cfg.map_url,
                                  system_info=collect_system_info())

    if args.preview:
        with open(args.preview, "w", encoding="utf-8") as handle:
            handle.write(html_out)
        print("wrote %s (%d incidents in the last 24h)" % (args.preview, stats["new_total"]))
    if args.send:
        if not (cfg.smtp_user and cfg.smtp_pass and cfg.mail_to):
            raise SystemExit("Set LAF911_DIGEST_SMTP_USER / LAF911_DIGEST_SMTP_PASS / LAF911_DIGEST_TO first.")
        send_digest(cfg, html_out, _subject_for(stats))
        print("sent to %s" % cfg.mail_to)
    if not args.preview and not args.send:
        parser.print_help()
