"""Personal commute route alerts.

Shortly before your usual departure, email the CURRENT 911 incidents on the
roads you drive, plus active NWS weather alerts — so you know about the
accident on Ambassador Caffery before you pull out of the driveway.

What this is (and honestly isn't)
---------------------------------
There is no free, keyless source of Google/Waze-style live traffic *speed*
(road-flow) data. What this uses instead is arguably better targeted for a
commute: the actual 911 incident feed (accidents, hazards, stalls dispatched
on your route) filtered to a freshness window, plus the free NWS alerts we
already pull. A paid traffic-flow provider can be layered on later via
:func:`fetch_traffic_flow` without touching the rest of this module.

"Current" caveat: the feed reports when an incident STARTED, never when it
cleared. So a minor accident may already be gone. We therefore (a) only
include incidents inside ``LAF911_ROUTE_WINDOW_MIN`` minutes, and (b) label
each with how long ago it was reported, so you can judge.

Config & privacy
----------------
Routes come from environment variables OR from config emails saved by
:mod:`lafayette911.route_inbox` (the zero-Pi-configuration path; email slots
override env slots and implicitly enable the feature). Nothing personal —
your addresses, routes, times, or email — ever touches this repository or
the published map. The SMTP settings are shared with the daily digest
(``LAF911_DIGEST_SMTP_*`` / ``LAF911_DIGEST_TO``).

    LAF911_ROUTE_ENABLED=true
    LAF911_ROUTE_LEAD_MIN=10           # email this many minutes before departure
    LAF911_ROUTE_WINDOW_MIN=90         # only incidents newer than this
    LAF911_ROUTE_FOLLOWUP_MIN=30       # keep watching this long after the email;
                                       # NEW incidents trigger a follow-up alert
                                       # (0 disables; max 120)
    LAF911_ROUTE_1_NAME=To work
    LAF911_ROUTE_1_CORRIDORS=Ambassador Caffery | Kaliste Saloom | I-10
    LAF911_ROUTE_1_DEPART=07:20
    LAF911_ROUTE_1_DAYS=mon-fri
    LAF911_ROUTE_2_NAME=Home
    LAF911_ROUTE_2_CORRIDORS=I-10 | Ambassador Caffery | Johnston St
    LAF911_ROUTE_2_DEPART=17:00
    LAF911_ROUTE_2_DEPART_FRI=12:00    # optional per-day departure override
    LAF911_ROUTE_2_DAYS=mon-fri

CLI:  python -m lafayette911.route_alerts --preview out.html [--route 1]
      python -m lafayette911.route_alerts --send   [--route 1]   # send now
      python -m lafayette911.route_alerts --diagnose [--route 1] [--at "2026-09-28 07:10"]
            # explain which incidents matched / were skipped, and why
"""

import html as _html
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from lafayette911.corridors import corridor_ids, normalize_corridor
from lafayette911.daily_digest import (
    DigestConfig,
    _REPORTED_RE,
    categorize,
    load_digest_config,
    send_digest,
)

# Category → (emoji, severity rank). Lower rank sorts first (accidents on top).
_CAT_META = {
    "Accidents": ("🚗", 0),
    "Fire": ("🔥", 1),
    "Hazards": ("⚠️", 2),
    "Stalled": ("🛑", 3),
    "Signals": ("🚦", 4),
    "Medical / rescue": ("🚑", 1),
    "Other": ("📋", 5),
}

_DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class Route:
    index: int
    name: str
    corridors: Set[str]           # canonical corridor ids
    corridor_labels: List[str]    # display order as entered
    depart_minutes: int           # minutes since local midnight
    days: Set[int]                # weekday ints, 0=Mon … 6=Sun
    # Optional traced path: [(lat, lng), ...]. When present, incidents must
    # have coordinates and match by distance to this line (section-precise)
    # instead of by road name. Unlocated incidents are skipped because they
    # cannot be proven to be on the selected road section.
    path: List = field(default_factory=list)
    radius_m: int = 100
    # Per-weekday departure overrides ({4: 720} = Fridays at 12:00), from
    # DEPART_MON … DEPART_SUN. Days without one use depart_minutes.
    depart_overrides: Dict[int, int] = field(default_factory=dict)

    def depart_for(self, weekday: int) -> int:
        return self.depart_overrides.get(weekday, self.depart_minutes)


@dataclass
class RouteConfig:
    enabled: bool
    lead_min: int
    window_min: int
    # After the departure email goes out, keep watching the route for this
    # many minutes; anything NEW that appears gets a follow-up alert (you may
    # already be driving). 0 disables. LAF911_ROUTE_FOLLOWUP_MIN overrides.
    followup_min: int = 30
    routes: List[Route] = field(default_factory=list)


def _parse_hhmm(value: str) -> Optional[int]:
    try:
        h, m = str(value).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except Exception:
        pass
    return None


def _parse_days(value: str) -> Set[int]:
    """Parse 'mon-fri', 'mon,wed,fri', 'daily', 'weekends' → {0..6}."""
    s = str(value or "").strip().lower()
    if s in ("daily", "everyday", "all"):
        return set(range(7))
    if not s:
        return {0, 1, 2, 3, 4}   # sensible default: weekdays
    if s in ("weekdays", "weekday"):
        return {0, 1, 2, 3, 4}
    if s in ("weekends", "weekend"):
        return {5, 6}
    out: Set[int] = set()
    for part in s.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a in _DAY_NAMES and b in _DAY_NAMES:
                ia, ib = _DAY_NAMES.index(a), _DAY_NAMES.index(b)
                rng = range(ia, ib + 1) if ia <= ib else list(range(ia, 7)) + list(range(0, ib + 1))
                out.update(rng)
        elif part in _DAY_NAMES:
            out.add(_DAY_NAMES.index(part))
    return out or {0, 1, 2, 3, 4}


def _split_corridors(value: str) -> List[str]:
    # '|' is the primary separator (road names may contain nothing weird, but
    # never a pipe); fall back to newlines. Commas are NOT split on — some
    # future alias could contain one.
    raw = str(value or "")
    parts = [p.strip() for p in raw.replace("\n", "|").split("|")]
    return [p for p in parts if p]


def _parse_path(value: str) -> List:
    """Parse 'lat,lng; lat,lng; …' into [(lat, lng), ...]; junk points drop."""
    out = []
    for part in str(value or "").split(";"):
        bits = part.strip().split(",")
        if len(bits) != 2:
            continue
        try:
            lat, lng = float(bits[0]), float(bits[1])
        except ValueError:
            continue
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            out.append((lat, lng))
    return out


def _pt_seg_dist_m(plat, plng, alat, alng, blat, blng) -> float:
    """Distance in meters from point P to segment AB (equirectangular — plenty
    accurate at city scale)."""
    import math
    lat0 = math.radians((alat + blat + plat) / 3.0)
    kx = 111320.0 * math.cos(lat0)   # meters per degree of longitude
    ky = 110540.0                    # meters per degree of latitude
    px, py = plng * kx, plat * ky
    ax, ay = alng * kx, alat * ky
    bx, by = blng * kx, blat * ky
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        t = 0.0
    else:
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5


def dist_to_path_m(lat: float, lng: float, path: List) -> float:
    """Shortest distance from a point to a polyline, in meters."""
    if not path:
        return float("inf")
    if len(path) == 1:
        return _pt_seg_dist_m(lat, lng, path[0][0], path[0][1], path[0][0], path[0][1])
    return min(
        _pt_seg_dist_m(lat, lng, a[0], a[1], b[0], b[1])
        for a, b in zip(path, path[1:])
    )


_DEPART_DAY_KEYS = tuple("DEPART_" + d.upper() for d in _DAY_NAMES)
ROUTE_KV_KEYS = ("NAME", "CORRIDORS", "PATH", "RADIUS_M", "DEPART", "DAYS") + _DEPART_DAY_KEYS

# app_meta key holding routes configured BY EMAIL (see route_inbox.py) — the
# zero-Pi-configuration path. Slots stored here override the same env slot.
MAIL_ROUTES_META_KEY = "mail_routes_v1"


def _route_from_kv(i: int, kv: Dict[str, str]) -> Optional[Route]:
    """Build one Route from a {NAME, CORRIDORS, PATH, RADIUS_M, DEPART, DAYS}
    mapping (values as raw strings). Returns None when the slot is empty or
    unusable."""
    name = str(kv.get("NAME") or "").strip()
    corr = str(kv.get("CORRIDORS") or "")
    path = _parse_path(kv.get("PATH") or "")
    if not name and not corr and len(path) < 2:
        return None
    labels = _split_corridors(corr)
    canon = set()
    for label in labels:
        cids = corridor_ids(label) or ([normalize_corridor(label)] if normalize_corridor(label) else [])
        canon.update(c for c in cids if c)
    try:
        radius_m = int(str(kv.get("RADIUS_M") or "100").strip() or 100)
    except ValueError:
        radius_m = 100
    depart = _parse_hhmm(kv.get("DEPART") or "")
    if depart is None or (not canon and len(path) < 2):
        return None
    overrides = {}
    for day_idx, key in enumerate(_DEPART_DAY_KEYS):
        t = _parse_hhmm(kv.get(key) or "")
        if t is not None:
            overrides[day_idx] = t
    return Route(
        index=i,
        name=name or ("Route %d" % i),
        corridors=canon,
        corridor_labels=labels,
        depart_minutes=depart,
        days=_parse_days(kv.get("DAYS") or "mon-fri"),
        path=path,
        radius_m=max(50, min(2000, radius_m)),
        depart_overrides=overrides,
    )


def _load_mail_routes(store) -> Dict[str, Dict[str, str]]:
    """Routes saved from config emails: {"1": {"NAME": ..., ...}, ...}."""
    if store is None:
        return {}
    try:
        import json
        raw = store._meta_get(MAIL_ROUTES_META_KEY)
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_route_config(store=None) -> RouteConfig:
    """Effective route config: env slots, overridden per-slot by any route
    configured BY EMAIL (stored in SQLite by route_inbox). Mailbox routes
    also implicitly enable the feature — that's the zero-Pi-config path."""
    enabled = os.getenv("LAF911_ROUTE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    lead = int(os.getenv("LAF911_ROUTE_LEAD_MIN", "10") or 10)
    window = int(os.getenv("LAF911_ROUTE_WINDOW_MIN", "90") or 90)
    try:
        followup = max(0, min(120, int(os.getenv("LAF911_ROUTE_FOLLOWUP_MIN", "30") or 30)))
    except ValueError:
        followup = 30
    mail_routes = _load_mail_routes(store)
    routes: List[Route] = []
    for i in range(1, 21):
        kv = {k: os.getenv("LAF911_ROUTE_%d_%s" % (i, k), "") for k in ROUTE_KV_KEYS}
        mkv = mail_routes.get(str(i))
        if isinstance(mkv, dict) and any(str(mkv.get(k) or "").strip() for k in ROUTE_KV_KEYS):
            kv = {k: str(mkv.get(k) or "") for k in ROUTE_KV_KEYS}
        route = _route_from_kv(i, kv)
        if route is not None:
            routes.append(route)
    if mail_routes:
        enabled = True
    return RouteConfig(enabled=enabled, lead_min=lead, window_min=window,
                       followup_min=followup, routes=routes)


def _parse_reported_local(reported: str) -> Optional[datetime]:
    """Parse the feed's 'MM/DD/YYYY HH:MM AM/PM' as a naïve LOCAL datetime."""
    m = _REPORTED_RE.search(str(reported or ""))
    if not m:
        return None
    hh = int(m.group(4)) % 12
    if m.group(6).upper() == "PM":
        hh += 12
    try:
        return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)), hh, int(m.group(5)))
    except ValueError:
        return None


class _PathIndex:
    """A drawn line in local meters with a coarse grid over its segments, so
    projecting thousands of historical incidents onto it stays cheap on a Pi."""

    def __init__(self, path: List, near_m: float, cell_m: float = 250.0):
        import math
        self.lat0 = math.radians(sum(p[0] for p in path) / len(path))
        self.kx = 111320.0 * math.cos(self.lat0)
        self.ky = 110540.0
        self.pts = [(p[1] * self.kx, p[0] * self.ky) for p in path]
        self.cell = cell_m
        self.near = near_m
        self.segs = []      # (ax, ay, dx, dy, len2, along_at_a)
        self.grid: Dict = {}
        along = 0.0
        for i, ((ax, ay), (bx, by)) in enumerate(zip(self.pts, self.pts[1:])):
            dx, dy = bx - ax, by - ay
            self.segs.append((ax, ay, dx, dy, dx * dx + dy * dy, along))
            along += (dx * dx + dy * dy) ** 0.5
            x0, x1 = int((min(ax, bx) - near_m) // cell_m), int((max(ax, bx) + near_m) // cell_m)
            y0, y1 = int((min(ay, by) - near_m) // cell_m), int((max(ay, by) + near_m) // cell_m)
            for cx in range(x0, x1 + 1):
                for cy in range(y0, y1 + 1):
                    self.grid.setdefault((cx, cy), []).append(i)
        self.length = along

    def project(self, lat: float, lng: float):
        """(distance to line, distance along line) in meters, or None when
        the point is farther than ``near_m`` from every segment."""
        px, py = lng * self.kx, lat * self.ky
        best = None
        for i in self.grid.get((int(px // self.cell), int(py // self.cell)), ()):
            ax, ay, dx, dy, len2, along = self.segs[i]
            t = 0.0 if len2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len2))
            d = ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5
            if d <= self.near and (best is None or d < best[0]):
                best = (d, along + t * len2 ** 0.5)
        return best


# Dispatch often writes a crash at an intersection as "block number + cross
# street" ("600 E BROUSSARD/DUHON RD"), which geocodes to that block of the
# FIRST road — up to a block or two from the intersection itself. When such a
# location names a road the route runs along, it matches within this wider
# distance instead of the route's own radius.
_CROSS_STREET_RADIUS_M = 400
_INTERSECTION_TEXT = re.compile(r"/|&|@|\bAT\b|\bNEAR\b")


def _is_intersection(location: str) -> bool:
    return bool(_INTERSECTION_TEXT.search(str(location or "").upper()))


# A road counts as "driven" when past incidents on it line up along at least
# this much of the drawn line. A cross street only touches the line at one
# point (span ≈ 0), so it never qualifies.
_ROAD_MIN_SPAN_M = 400.0
_path_roads_cache: Dict = {}


def derive_path_corridors(db_path: str, path: List, radius_m: int) -> Set[str]:
    """Roads a drawn route actually runs ALONG, learned from history.

    Drawn routes carry no road names, so an incident that has not been placed
    on the map yet (geocoding pending, budget spent, intersection-only
    address) cannot be distance-tested. Without road names it used to vanish
    silently — the "route clear" email went out with a crash on your road.

    Every previously geocoded incident near the line is projected onto it; a
    road whose incidents spread along >= ``_ROAD_MIN_SPAN_M`` of the line (or
    a third of a short route) is one you drive. No map download needed — the
    incident history is the gazetteer. Cached per path for the day.
    """
    import sqlite3

    if len(path) < 2:
        return set()
    key = (db_path, tuple(path), int(radius_m), datetime.now().strftime("%Y-%m-%d"))
    if key in _path_roads_cache:
        return _path_roads_cache[key]
    near_m = max(150, int(radius_m))
    pad = near_m / 100000.0 * 1.5   # degrees, generous
    lats = [p[0] for p in path]
    lngs = [p[1] for p in path]
    index = _PathIndex(path, near_m)
    min_span = min(_ROAD_MIN_SPAN_M, index.length / 3.0)
    spans: Dict[str, List[float]] = {}
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        try:
            rows = conn.execute(
                "SELECT location, latitude, longitude FROM incidents"
                " WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?",
                (min(lats) - pad, max(lats) + pad, min(lngs) - pad, max(lngs) + pad),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return set()
    for loc, lat, lng in rows:
        if lat is None or lng is None:
            continue
        hit = index.project(float(lat), float(lng))
        if hit is None:
            continue
        along = hit[1]
        for cid in corridor_ids(loc):
            lo_hi = spans.setdefault(cid, [along, along])
            lo_hi[0] = min(lo_hi[0], along)
            lo_hi[1] = max(lo_hi[1], along)
    roads = {cid for cid, (lo, hi) in spans.items() if hi - lo >= min_span}
    if len(_path_roads_cache) > 32:
        _path_roads_cache.clear()
    _path_roads_cache[key] = roads
    return roads


def route_roads(db_path: str, route: Route) -> Set[str]:
    """Road names that identify this route: typed corridors, plus (for a
    drawn route) the roads derived from its path."""
    roads = set(route.corridors)
    if route.path and len(route.path) >= 2:
        roads |= derive_path_corridors(db_path, route.path, route.radius_m)
    return roads


def find_route_incidents(db_path: str, route: Route, window_min: int,
                         now: Optional[datetime] = None,
                         diagnostics: Optional[List[Dict]] = None) -> List[Dict]:
    """Current incidents on the route, within the freshness window.

    Section-precise when the route carries a traced ``path``: located
    incidents must be within ``route.radius_m`` of the line, so an accident
    five miles down a road you only briefly use does NOT match.

    Incidents not yet placed on the map can't be distance-tested. For a drawn
    route they still match when their road is one the route runs along (see
    :func:`route_roads`) and are flagged ``approx`` so the email says "may be
    outside your section" — a possible false alarm beats a false all-clear.
    Without a path (roads-only config), everything matches by corridor.
    Sorted by severity, then most-recent first.

    ``diagnostics``, when given, collects one entry per fresh incident with
    the verdict and reason (used by ``--diagnose``).
    """
    import sqlite3

    from lafayette911.episodes import find_episode_duplicates

    now = now or datetime.now()
    cutoff = now - timedelta(minutes=window_min)
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        rows = conn.execute(
            "SELECT location, cause, reported, latitude, longitude,"
            " weather_precip_prob, weather_precip_in, incident_number FROM incidents"
        ).fetchall()
    finally:
        conn.close()

    # One crash is often logged twice within minutes (reclassification,
    # hit-and-run + accident type). Fold those into one alert line — and one
    # follow-up identity — instead of emailing the same event twice.
    fresh = [r for r in rows
             if (lambda d: d is not None and d >= cutoff)(_parse_reported_local(r[2]))]
    secondary_ids, extras_by_primary = find_episode_duplicates([
        {"id": str(r[7] or ""), "location": r[0], "cause": r[1],
         "reported": r[2], "located": r[3] is not None and r[4] is not None}
        for r in fresh
    ])

    drawn = bool(route.path and len(route.path) >= 2)
    roads = route_roads(db_path, route) if drawn else route.corridors

    def note(loc, cause, dt, verdict, reason, dist=None):
        if diagnostics is not None:
            diagnostics.append({"location": str(loc or "").strip(), "cause": str(cause or "").strip(),
                                "reported_dt": dt, "verdict": verdict, "reason": reason,
                                "dist_m": dist})

    out: List[Dict] = []
    for loc, cause, reported, lat, lng, pprob, pin, inum in rows:
        dt = _parse_reported_local(reported)
        if dt is None or dt < cutoff or dt > now + timedelta(minutes=5):
            continue
        if str(inum or "") in secondary_ids:
            note(loc, cause, dt, "merged", "duplicate listing of another incident")
            continue
        located = lat is not None and lng is not None
        approx = False
        cross_street = False
        dist_m = None
        loc_roads = corridor_ids(loc)
        if drawn:
            if located:
                dist_m = dist_to_path_m(float(lat), float(lng), route.path)
                if dist_m > route.radius_m:
                    cross = sorted(set(loc_roads) & roads) if _is_intersection(loc) else []
                    if not cross or dist_m > max(_CROSS_STREET_RADIUS_M, 2 * route.radius_m):
                        note(loc, cause, dt, "skipped", "%d m from your line (limit %d m)" % (dist_m, route.radius_m), dist_m)
                        continue
                    cross_street = True
                matched = sorted(set(loc_roads) & roads) or loc_roads[:1]
            else:
                matched = sorted(set(loc_roads) & roads)
                if not matched:
                    note(loc, cause, dt, "skipped", "not located yet, and not on a road this route runs along")
                    continue
                approx = True
        else:
            matched = sorted(set(loc_roads) & route.corridors)
            if not matched:
                note(loc, cause, dt, "skipped", "not on one of your roads")
                continue
        if approx:
            reason = "not located yet — on %s" % ", ".join(matched)
        elif cross_street:
            reason = ("intersection with %s, placed %d m from your line (intersection addresses "
                      "are placed approximately)" % (", ".join(matched), dist_m))
        elif dist_m is not None:
            reason = "%d m from your line" % dist_m
        else:
            reason = "on %s" % ", ".join(matched)
        note(loc, cause, dt, "ALERT", reason, dist_m)
        cat = categorize(cause)
        emoji, rank = _CAT_META.get(cat, ("📋", 5))
        extras = extras_by_primary.get(str(inum or ""), [])
        out.append({
            "location": str(loc or "").strip(),
            "cause": str(cause or "").strip(),
            "category": cat,
            "emoji": emoji,
            "rank": rank,
            "reported_dt": dt,
            "minutes_ago": int((now - dt).total_seconds() // 60),
            "matched": matched,
            "located": located,
            "approx": approx,
            "cross_street": cross_street,
            "dist_m": None if dist_m is None else int(dist_m),
            "lat": float(lat) if located else None,
            "lng": float(lng) if located else None,
            "rain": (pprob is not None and pprob >= 20) or (pin is not None and pin > 0.005),
            # Duplicate feed listings folded into this line, and the full id
            # set of the episode — the follow-up watcher keys on these so a
            # re-listing of an already-emailed crash is never "new".
            "also": [x["cause"] for x in extras],
            "episode_ids": [str(inum or "")] + [x["id"] for x in extras],
        })
    out.sort(key=lambda r: (r["rank"], r["minutes_ago"]))
    return out


def fetch_traffic_flow(route: Route):  # pragma: no cover - optional hook
    """Placeholder for an OPTIONAL paid traffic-flow provider (TomTom / HERE /
    Google). Return None today; wire a keyed provider here later to enrich the
    email with segment speeds. Kept deliberately separate so the free path
    never depends on it."""
    return None


# ── email rendering (email-safe inline HTML, matches the digest style) ──────
def _fmt_ago(minutes: int) -> str:
    if minutes <= 0:
        return "just now"
    if minutes < 60:
        return "%d min ago" % minutes
    h, m = divmod(minutes, 60)
    return "%dh %dm ago" % (h, m) if m else "%dh ago" % h


def _fmt_depart(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    ampm = "AM" if h < 12 else "PM"
    return "%d:%02d %s" % (h % 12 or 12, m, ampm)


def render_route_email(route: Route, incidents: List[Dict], now: datetime,
                       window_min: int, alerts: Optional[Dict] = None,
                       map_url: str = "", followup: bool = False,
                       map_cid: str = "", test_note: str = "") -> str:
    e = _html.escape
    if route.corridor_labels:
        route_line = " → ".join(e(c.title()) for c in route.corridor_labels)
    else:
        route_line = "your drawn route"
    if route.path and len(route.path) >= 2:
        route_line += ' <span style="color:#b7bcc5;">· section-matched on your drawn route</span>'

    if followup:
        # Post-departure watch: only NEW incidents since the departure email,
        # sent while the reader may already be behind the wheel.
        header_emoji = "🚨"
        headline = "%d NEW incident%s on your route" % (len(incidents), "" if len(incidents) == 1 else "s")
        head_bg = "#b91c1c"
    elif incidents:
        header_emoji = "🚧"
        headline = "%d active incident%s on your route" % (len(incidents), "" if len(incidents) == 1 else "s")
        head_bg = "#b45309"
    else:
        header_emoji = "✅"
        headline = "Your route looks clear"
        head_bg = "#047857"

    test_html = ""
    if test_note:
        test_html = ('<div style="background:#fef9c3;border:1px solid #facc15;border-radius:12px;'
                     'padding:10px 14px;margin:0 0 12px 0;font-size:12.5px;color:#713f12;line-height:1.5;">'
                     '🧪 <b>Test email</b> — ' + e(test_note) + '</div>')

    alert_html = ""
    if alerts:
        active = [name for name, on in alerts.items() if on]
        if active:
            alert_html = ('<div style="background:#fff7ed;border:1px solid #fdba74;border-radius:12px;'
                          'padding:10px 14px;margin:0 0 12px 0;font-size:13px;color:#7c2d12;">'
                          '📢 <b>NWS alert:</b> ' + e(", ".join(active)) + '</div>')

    window_txt = str(int(window_min))
    rows_html = ""
    if incidents:
        for inc in incidents:
            loc_txt = inc["location"].title() if inc["location"] else "Unknown location"
            on_roads = ", ".join(c.title() for c in inc["matched"])
            badges = ""
            if inc.get("approx"):
                badges += (' <span style="font-size:10.5px;color:#b45309;">(not on the map yet — '
                           'somewhere on this road, may be outside your section)</span>')
            elif inc.get("cross_street"):
                badges += (' <span style="font-size:10.5px;color:#b45309;">(reported at an intersection '
                           'with your road — the map pin for these is approximate)</span>')
            elif not inc["located"]:
                badges += ' <span style="font-size:10.5px;color:#8a919e;">(locating…)</span>'
            if inc["rain"]:
                badges += ' 🌧️'
            if inc.get("also"):
                badges += (' <span style="font-size:10.5px;color:#8a919e;">(also logged as: ' +
                           e(", ".join(c.title() for c in inc["also"])) + ')</span>')
            ago_color = "#b45309" if inc["minutes_ago"] <= 30 else "#8a919e"
            rows_html += (
                '<tr><td style="padding:9px 0;border-bottom:1px solid #eef0f4;">'
                '<div style="font-size:14px;font-weight:700;color:#171a21;">' + inc["emoji"] + " " +
                e(inc["cause"] or inc["category"]) +
                '<span style="float:right;font-weight:600;color:' + ago_color + ';font-size:12.5px;">' +
                e(_fmt_ago(inc["minutes_ago"])) + '</span></div>'
                '<div style="font-size:12.5px;color:#5c6470;padding-top:2px;">' + e(loc_txt) + badges + '</div>'
                '<div style="font-size:11px;color:#8a919e;padding-top:1px;">on ' + e(on_roads) +
                ((' · ~%d ft from your line' % (inc["dist_m"] * 3.281)) if inc.get("dist_m") is not None else '') + '</div>'
                '</td></tr>'
            )
        note = ('Reported AFTER your departure email — sent because it may affect the drive '
                'you are on right now. Do not read this while driving.'
                if followup else
                'Incidents reported within the last ' + window_txt + ' minutes. The feed shows when an '
                'incident was reported, not when it clears — minor ones are often already cleared.')
        body = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + rows_html + '</table>'
                '<div style="font-size:11px;color:#8a919e;padding-top:10px;line-height:1.5;">' +
                note + ' Drive safely and defer to what you see on the road.</div>')
    else:
        body = ('<div style="text-align:center;padding:18px 6px;">'
                '<div style="font-size:40px;">🛣️</div>'
                '<div style="font-size:14px;color:#5c6470;padding-top:6px;">'
                'No 911 incidents reported on your route in the last ' + window_txt + ' minutes.</div>'
                '<div style="font-size:11px;color:#8a919e;padding-top:6px;">'
                'Absence of a report isn&#39;t a guarantee the road is clear — drive safely.</div></div>')

    route_img = ""
    if map_cid:
        route_img = ('<div style="padding:0 0 12px 0;">'
                     '<img src="cid:' + e(map_cid) + '" width="560" alt="Map of your route" '
                     'style="display:block;width:100%;max-width:560px;height:auto;border-radius:12px;'
                     'border:1px solid #e7e9ee;"></div>'
                     '<div style="font-size:11px;color:#8a919e;padding:0 0 12px 0;margin-top:-6px;">'
                     '🟢 start · 🏁 end · 🔴 reported incident (not-yet-located ones are listed but not drawn)</div>')

    map_btn = ""
    if map_url:
        map_btn = ('<div style="text-align:center;padding:16px 0 2px 0;">'
                   '<a href="' + e(map_url) + '" style="display:inline-block;background:#2f6fed;color:#fff;'
                   'text-decoration:none;font-weight:700;font-size:13px;padding:10px 22px;border-radius:999px;">'
                   'Open the live map →</a></div>')

    when = now.strftime("%A, %B %-d · %-I:%M %p")
    # Token replacement (not %-formatting) so inline CSS "100%" can't collide.
    tmpl = """<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#eef0f4;font-family:-apple-system,'SF Pro Text',Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#171a21;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:22px 10px;">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
  <tr><td style="background:__HEAD_BG__;border-radius:20px 20px 0 0;padding:18px 22px;">
    <div style="font-size:18px;font-weight:800;color:#fff;">__HEMOJI__ __HEADLINE__</div>
    <div style="font-size:12.5px;color:rgba(255,255,255,0.9);padding-top:3px;">__ROUTE_NAME__ · leaving ~__DEPART__ · __WHEN__</div>
  </td></tr>
  <tr><td style="background:#f9fafb;border:1px solid #e7e9ee;border-top:none;border-radius:0 0 20px 20px;padding:16px 18px 20px 18px;">
    <div style="font-size:12px;color:#8a919e;padding-bottom:10px;">🧭 __ROUTE_LINE__</div>
    __TEST__
    __ROUTE_IMG__
    __ALERTS__
    __BODY__
    __MAP_BTN__
    <div style="font-size:10.5px;color:#8a919e;text-align:center;line-height:1.5;padding-top:12px;">
      Personal commute alert from your Raspberry Pi. To change, test or delete this route,
      tap the route button on the map page.<br>
      Unofficial — not affiliated with any agency.<br>
      Not for navigation or emergencies. In an emergency, call 911.
    </div>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""
    subs = {
        "__HEAD_BG__": head_bg,
        "__HEMOJI__": header_emoji,
        "__HEADLINE__": e(headline),
        "__ROUTE_NAME__": e(route.name),
        "__DEPART__": e(_fmt_depart(route.depart_for(now.weekday()))),
        "__WHEN__": e(when),
        "__ROUTE_LINE__": route_line,
        "__TEST__": test_html,
        "__ROUTE_IMG__": route_img,
        "__ALERTS__": alert_html,
        "__BODY__": body,
        "__MAP_BTN__": map_btn,
    }
    for token, value in subs.items():
        tmpl = tmpl.replace(token, value)
    return tmpl


def _subject_for(route: Route, incidents: List[Dict], followup: bool = False,
                 now: Optional[datetime] = None) -> str:
    if followup:
        top = incidents[0]
        return "🚨 %s: NEW on your route — %s %s (%s)" % (
            route.name, top["emoji"],
            (top["cause"] or top["category"]).title(), _fmt_ago(top["minutes_ago"]))
    if not incidents:
        return "✅ %s: route clear (%s)" % (route.name, (now or datetime.now()).strftime("%-I:%M %p"))
    top = incidents[0]
    return "🚧 %s: %d on your route — %s %s (%s)" % (
        route.name, len(incidents), top["emoji"],
        (top["cause"] or top["category"]).title(), _fmt_ago(top["minutes_ago"]))


def _fetch_alerts_best_effort(session, logger) -> Optional[Dict]:
    if session is None:
        return None
    try:
        from lafayette911.weather import fetch_nws_alerts
        snap = fetch_nws_alerts(session, timeout=15, cache_ttl_seconds=900, logger=logger)
        if snap is None:
            return None
        return {
            "Flash Flood Warning": bool(getattr(snap, "flash_flood_warning", False)),
            "Severe Thunderstorm Warning": bool(getattr(snap, "severe_thunderstorm_warning", False)),
            "Tornado Watch": bool(getattr(snap, "tornado_watch", False)),
        }
    except Exception:
        return None


ROUTE_MAP_CID = "laf911-route-map"


def _route_map_images(config, route: Route, incidents: List[Dict], session) -> Dict[str, bytes]:
    """{cid: png} with a picture of a drawn route, or {} (never raises)."""
    if not (route.path and len(route.path) >= 2):
        return {}
    try:
        from lafayette911.route_map_image import render_route_png
        base = getattr(config, "base_dir", None) or os.path.dirname(os.path.abspath(config.db_path))
        png = render_route_png(route.path, incidents,
                               cache_dir=os.path.join(base, "route_map_cache"), session=session)
        return {ROUTE_MAP_CID: png} if png else {}
    except Exception:
        return {}


def _send_route_email(send, cfg, config, route, incidents, now, rcfg, session, logger,
                      followup: bool = False, test_note: str = "") -> None:
    images = _route_map_images(config, route, incidents, session)
    html = render_route_email(route, incidents, now, rcfg.window_min,
                              # Live NWS alerts would be misleading in a replay.
                              alerts=None if test_note else _fetch_alerts_best_effort(session, logger),
                              map_url=cfg.map_url, followup=followup,
                              map_cid=ROUTE_MAP_CID if images else "", test_note=test_note)
    subject = _subject_for(route, incidents, followup=followup, now=now)
    if test_note:
        subject = "🧪 TEST · " + subject
    if images:
        send(cfg, html, subject, images=images)
    else:
        send(cfg, html, subject)


def schedule_note(route: Route, lead_min: int, at: datetime) -> str:
    """Plain-English answer to "would an email go out around this time?"."""
    day = at.strftime("%A")
    if at.weekday() not in route.days:
        return "%s isn't one of this route's days, so no email would be sent." % day
    depart = route.depart_for(at.weekday())
    return "On %ss the email goes out at %s for a %s departure, then watches the route." % (
        day, _fmt_depart(max(0, depart - lead_min)), _fmt_depart(depart))


def run_route_test(config, store, session, logger, slot: int, at: datetime,
                   route_cfg: Optional[RouteConfig] = None,
                   digest_cfg: Optional[DigestConfig] = None, send=send_digest) -> Dict:
    """Test mode: email the departure alert for one route AS IF it were
    ``at`` — using the incidents in the database for that moment — clearly
    labelled as a test. Does not touch the real schedule or dedup state.
    Returns a small summary for the page. Never raises."""
    rcfg = route_cfg or load_route_config(store)
    route = next((r for r in rcfg.routes if r.index == int(slot)), None)
    if route is None:
        return {"ok": False, "error": "Route %s isn't on the Pi (yet) — save it, wait a few minutes, retry." % slot}
    summary = {"ok": True, "route": route.name, "at": at.strftime("%a %b %-d, %-I:%M %p"),
               "schedule": schedule_note(route, rcfg.lead_min, at)}
    try:
        diag: List[Dict] = []
        incidents = find_route_incidents(config.db_path, route, rcfg.window_min, now=at, diagnostics=diag)
        summary["incidents"] = len(incidents)
        summary["headline"] = ("%d incident%s on the route" % (len(incidents), "" if len(incidents) == 1 else "s")
                               if incidents else "Route clear")
        skipped_near = [d for d in diag if d["verdict"] == "skipped" and d.get("dist_m") is not None
                        and d["dist_m"] <= 3 * route.radius_m]
        if skipped_near:
            summary["near_misses"] = "; ".join("%s at %s (%s)" % (d["cause"].title(), d["location"].title(), d["reason"])
                                               for d in skipped_near[:3])
        cfg = digest_cfg or load_digest_config()
        if not (cfg.smtp_host and cfg.smtp_user and cfg.smtp_pass and cfg.mail_to):
            summary["emailed"] = False
            summary["error"] = "Email isn't configured on the Pi."
            return summary
        note = ("as if it were %s. %s Uses the incidents on record for that time%s. "
                "Your real alerts are unaffected." % (
                    summary["at"], summary["schedule"],
                    " (a past time shows the map positions known NOW, which may be better than "
                    "what was known then)" if at < datetime.now() - timedelta(minutes=30) else ""))
        _send_route_email(send, cfg, config, route, incidents, at, rcfg, session, logger, test_note=note)
        summary["emailed"] = True
    except Exception as exc:
        summary["ok"] = False
        summary["error"] = str(exc)[:300]
    return summary


def maybe_send_route_alerts(config, store, session, logger,
                            route_cfg: Optional[RouteConfig] = None,
                            digest_cfg: Optional[DigestConfig] = None,
                            send=send_digest, now: Optional[datetime] = None) -> int:
    """Send any route alert that's due this cycle. Never raises; returns the
    number of alerts sent. Dedup + failure backoff live in app_meta, so a
    restart can't double-send and a broken mailbox can't spam forever."""
    from lafayette911.utils import log_event

    rcfg = route_cfg or load_route_config(store)
    if not rcfg.enabled or not rcfg.routes:
        return 0
    cfg = digest_cfg or load_digest_config()
    if not (cfg.smtp_host and cfg.smtp_user and cfg.smtp_pass and cfg.mail_to):
        return 0

    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    now_min = now.hour * 60 + now.minute
    sent = 0

    import json as _json

    for route in rcfg.routes:
        if now.weekday() not in route.days:
            continue
        sent_key = "route_%d_sent_date" % route.index
        fail_key = "route_%d_fail" % route.index
        sent_today = store._meta_get(sent_key) == today
        if (store._meta_get(fail_key + "_date") == today
                and int(store._meta_get(fail_key + "_count") or 0) >= 3):
            continue

        depart = route.depart_for(now.weekday())
        target = depart - rcfg.lead_min
        # Departure email: fire in the window [target, depart) — up to
        # lead_min minutes, several cycles — but never after departure.
        if not sent_today and target <= now_min < depart:
            try:
                incidents = find_route_incidents(config.db_path, route, rcfg.window_min, now=now)
                _send_route_email(send, cfg, config, route, incidents, now, rcfg, session, logger)
                store._meta_set(sent_key, today)
                store._meta_set(fail_key + "_count", "0")
                # Arm the post-departure watch: remember when we sent and
                # every episode id already reported, so only genuinely NEW
                # events (not re-listings of the same crash) follow up.
                reported = sorted({i for inc in incidents for i in inc.get("episode_ids", []) if i})
                store._meta_set("route_%d_sent_at_min" % route.index, str(now_min))
                store._meta_set("route_%d_reported_ids" % route.index, _json.dumps(reported))
                store._meta_set("route_%d_followups" % route.index, "0")
                sent += 1
                # Logging must never turn a delivered email into a "failure".
                try:
                    log_event(logger, "route_alert_sent", route=route.name, incidents=len(incidents))
                except Exception:
                    pass
            except Exception as exc:
                try:
                    prev = int(store._meta_get(fail_key + "_count") or 0) if store._meta_get(fail_key + "_date") == today else 0
                    store._meta_set(fail_key + "_date", today)
                    store._meta_set(fail_key + "_count", str(prev + 1))
                    log_event(logger, "route_alert_error", route=route.name, error=str(exc), attempt=prev + 1)
                except Exception:
                    pass
            continue

        # Post-departure watch: for followup_min minutes (default 30) after
        # the departure email, alert on incidents that appear on the route —
        # you may already be driving. This includes an incident that was
        # already in the feed but only now got placed on the map. Each event alerts at most once (episode ids),
        # and follow-ups cap at 3 per day as a spam fail-safe.
        if sent_today and rcfg.followup_min > 0:
            try:
                sent_at = int(store._meta_get("route_%d_sent_at_min" % route.index) or -1)
            except ValueError:
                sent_at = -1
            if sent_at < 0 or not (sent_at <= now_min <= sent_at + rcfg.followup_min):
                continue
            if int(store._meta_get("route_%d_followups" % route.index) or 0) >= 3:
                continue
            try:
                known = set(_json.loads(store._meta_get("route_%d_reported_ids" % route.index) or "[]"))
                incidents = find_route_incidents(config.db_path, route, rcfg.window_min, now=now)
                fresh = [inc for inc in incidents
                         if not (set(inc.get("episode_ids", [])) & known)]
                if not fresh:
                    continue
                _send_route_email(send, cfg, config, route, fresh, now, rcfg, session, logger,
                                  followup=True)
                for inc in fresh:
                    known.update(inc.get("episode_ids", []))
                store._meta_set("route_%d_reported_ids" % route.index, _json.dumps(sorted(known)))
                store._meta_set("route_%d_followups" % route.index,
                                str(int(store._meta_get("route_%d_followups" % route.index) or 0) + 1))
                sent += 1
                try:
                    log_event(logger, "route_followup_sent", route=route.name, incidents=len(fresh))
                except Exception:
                    pass
            except Exception as exc:
                try:
                    prev = int(store._meta_get(fail_key + "_count") or 0) if store._meta_get(fail_key + "_date") == today else 0
                    store._meta_set(fail_key + "_date", today)
                    store._meta_set(fail_key + "_count", str(prev + 1))
                    log_event(logger, "route_followup_error", route=route.name, error=str(exc), attempt=prev + 1)
                except Exception:
                    pass
    return sent


def _print_diagnosis(db_path: str, route: Route, window_min: int, at: datetime) -> None:
    """Human-readable explanation of what the matcher saw for one route."""
    diag: List[Dict] = []
    found = find_route_incidents(db_path, route, window_min, now=at, diagnostics=diag)
    drawn = bool(route.path and len(route.path) >= 2)
    print("=" * 72)
    print("Route %d '%s' — as of %s (window %d min)" % (route.index, route.name,
                                                       at.strftime("%a %Y-%m-%d %H:%M"), window_min))
    print("  departs %s on %s; email %s" % (
        _fmt_depart(route.depart_for(at.weekday())),
        at.strftime("%A"), "scheduled today" if at.weekday() in route.days else "NOT scheduled today"))
    if drawn:
        roads = route_roads(db_path, route)
        print("  drawn line: %d points, match radius %d m" % (len(route.path), route.radius_m))
        print("  roads it runs along (used for not-yet-located incidents): %s" % (
            ", ".join(sorted(roads)) or "NONE FOUND — only located incidents can match"))
    else:
        print("  roads: %s" % ", ".join(sorted(route.corridors)))
    print("  → email would say: %s" % (
        "%d incident(s) on your route" % len(found) if found else "route clear"))
    # Everything except located incidents clearly elsewhere in the parish.
    near = [d for d in diag if d["dist_m"] is None or d["dist_m"] <= 2000]
    far = len(diag) - len(near)
    for d in sorted(near, key=lambda d: d["reported_dt"]):
        print("  [%-7s] %s  %-28s %-40s %s" % (
            d["verdict"], d["reported_dt"].strftime("%H:%M"), d["cause"][:28], d["location"][:40], d["reason"]))
    print("  (%d other incident(s) in the window were far from this route)" % far)


if __name__ == "__main__":
    import argparse

    from lafayette911.config import load_config
    from lafayette911.state_store import StateStore

    parser = argparse.ArgumentParser(description="Lafayette 911 personal route alerts")
    parser.add_argument("--preview", metavar="FILE", help="write a route email to FILE (no send)")
    parser.add_argument("--send", action="store_true", help="send route alert(s) NOW (ignores schedule)")
    parser.add_argument("--diagnose", action="store_true",
                        help="explain which incidents match each route and why others don't")
    parser.add_argument("--at", metavar="'YYYY-MM-DD HH:MM'", default=None,
                        help="evaluate as of this local time (replay a past morning)")
    parser.add_argument("--route", type=int, default=None, help="only this route index (1-based)")
    args = parser.parse_args()

    app_cfg = load_config()
    store = StateStore(app_cfg.db_path, app_cfg.csv_path)
    try:
        rcfg = load_route_config(store)   # includes routes configured by email
    finally:
        store.close()
    dcfg = load_digest_config()
    if not rcfg.routes:
        raise SystemExit("No routes configured (by email or LAF911_ROUTE_1_* env vars).")

    chosen = [r for r in rcfg.routes if args.route is None or r.index == args.route]
    if not chosen:
        raise SystemExit("No route with index %s." % args.route)
    at = datetime.strptime(args.at, "%Y-%m-%d %H:%M") if args.at else datetime.now()

    session = None
    if args.preview or args.send:
        from lafayette911.fetch_incidents import build_session
        session = build_session()

    for route in chosen:
        if args.diagnose:
            _print_diagnosis(app_cfg.db_path, route, rcfg.window_min, at)
        if not (args.preview or args.send):
            continue
        incidents = find_route_incidents(app_cfg.db_path, route, rcfg.window_min, now=at)
        images = _route_map_images(app_cfg, route, incidents, session)
        if args.preview:
            path = args.preview if len(chosen) == 1 else args.preview.replace(".html", "_%d.html" % route.index)
            html = render_route_email(route, incidents, at, rcfg.window_min, map_url=dcfg.map_url,
                                      map_cid=ROUTE_MAP_CID if images else "")
            if images:
                png_path = os.path.splitext(path)[0] + "_map.png"
                with open(png_path, "wb") as handle:
                    handle.write(images[ROUTE_MAP_CID])
                html = html.replace("cid:" + ROUTE_MAP_CID, os.path.basename(png_path))
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(html)
            print("wrote %s — %s: %d incident(s) on route" % (path, route.name, len(incidents)))
        if args.send:
            if not (dcfg.smtp_user and dcfg.smtp_pass and dcfg.mail_to):
                raise SystemExit("Set LAF911_DIGEST_SMTP_USER / _PASS / LAF911_DIGEST_TO first.")
            html = render_route_email(route, incidents, at, rcfg.window_min, map_url=dcfg.map_url,
                                      map_cid=ROUTE_MAP_CID if images else "")
            send_digest(dcfg, html, _subject_for(route, incidents), images=images or None)
            print("sent %s to %s" % (route.name, dcfg.mail_to))
    if not (args.preview or args.send or args.diagnose):
        parser.print_help()
