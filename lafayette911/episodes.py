"""Group multiple feed records of the SAME real-world event into one episode.

Lafayette 911 frequently logs one crash several times within minutes at the
same location: a reclassification ("TRAFFIC ACCIDENT MINOR" upgraded to
"MAJOR"), a hit-and-run logged both as "HIT AND RUN" and as an accident
type, or a companion unit ("RESCUE SQUAD NEEDED") alongside the accident it
was dispatched to. Measured on the real archive, about 5% of records are
such second listings — enough to skew accident counts, corridor rankings,
hot spots, the daily digest, and to double-fire route alerts.

This module identifies those duplicates so every consumer can count
EPISODES (real-world events) while the raw archive keeps every record
exactly as collected. Nothing is ever deleted: grouping happens at
render/report time and is fully reversible.

Grouping rule
-------------
Records group into one episode when they share the same normalized location
string and each is reported within ``window_min`` minutes of the previous
record in the chain. TRAFFIC CONTROL records never participate (they have
their own aggregation pipeline), and records whose reported time can't be
parsed are left alone. The primary record of an episode is chosen by:
located first (a record with coordinates beats one without, so the mapped
point never vanishes), then cause severity/specificity (fatal > injuries >
hit-and-run > major > ... > stalled), then earliest report.

Known trade-off: two genuinely separate events at the exact same location
string within the window would display as one episode — rare, and the other
records stay visible in the popup, so nothing is hidden.
"""

import os
import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set, Tuple

DEFAULT_EPISODE_WINDOW_MIN = 15


def episode_window_min() -> int:
    try:
        v = int(os.getenv("LAF911_EPISODE_WINDOW_MIN", "") or DEFAULT_EPISODE_WINDOW_MIN)
        return max(0, min(120, v))
    except Exception:
        return DEFAULT_EPISODE_WINDOW_MIN


def cause_severity(cause: str) -> int:
    """Rank a cause by severity/specificity — higher wins the primary slot.

    Tuned to the pairings that actually co-occur in the archive:
    MAJOR+MINOR, HIT&RUN+accident-type, STALLED+accident, RESCUE+MAJOR…
    """
    s = str(cause or "").upper()
    if "FATAL" in s:
        return 100
    if re.search(r"W/\s*INJ|WITH\s+INJ", s):
        return 90
    if "HIT" in s and ("RUN" in s or "&" in s):
        return 80          # the hit-and-run label is the event's identity
    if "MAJOR" in s:
        return 70
    if "UNK" in s and "INJ" in s:
        return 60
    if "MINOR" in s:
        return 50
    if "ACCIDENT" in s or "CRASH" in s or "COLLISION" in s:
        return 45          # generic accident (incl. "NO INJURI" via below)
    if "RESCUE" in s:
        return 20          # companion unit, not the event itself
    if "STALL" in s or "DISABLED" in s:
        return 15
    return 30


_REPORTED_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
)


def parse_reported_any(reported) -> Optional[datetime]:
    """Parse the feed's reported string in both historical formats
    (12-hour with AM/PM, and the older 24-hour form)."""
    text = re.sub(r"\s+", " ", str(reported or "").strip())
    if not text:
        return None
    for fmt in _REPORTED_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _norm_loc(location) -> str:
    return re.sub(r"\s+", " ", str(location or "").strip().upper())


def find_episode_duplicates(
    records: Iterable[Dict], window_min: Optional[int] = None
) -> Tuple[Set[str], Dict[str, List[Dict]]]:
    """Identify duplicate feed listings of the same real-world event.

    ``records``: dicts with keys ``id``, ``location``, ``cause``,
    ``reported`` and optionally ``located`` (bool — record has coordinates).

    Returns ``(secondary_ids, extras_by_primary)``:
      - ``secondary_ids``: ids that are extra listings of some other record
        and should be folded out of counts/maps/alerts;
      - ``extras_by_primary``: for each episode primary, the folded records
        as ``[{"id", "cause", "reported"}, ...]`` ordered by report time,
        so displays can show "also logged in the feed as …".
    """
    window = episode_window_min() if window_min is None else window_min
    secondary_ids: Set[str] = set()
    extras_by_primary: Dict[str, List[Dict]] = {}
    if window <= 0:
        return secondary_ids, extras_by_primary

    usable = []
    for rec in records:
        rid = str(rec.get("id") or "").strip()
        if not rid:
            continue
        cause = str(rec.get("cause") or "").strip()
        if cause.upper() == "TRAFFIC CONTROL":
            continue
        loc = _norm_loc(rec.get("location"))
        dt = parse_reported_any(rec.get("reported"))
        if not loc or dt is None:
            continue
        usable.append({
            "id": rid,
            "loc": loc,
            "dt": dt,
            "cause": cause,
            "reported": str(rec.get("reported") or "").strip(),
            "located": bool(rec.get("located", True)),
        })

    usable.sort(key=lambda r: (r["loc"], r["dt"]))

    def close_group(group: List[Dict]) -> None:
        if len(group) < 2:
            return
        # Located first so the mapped point survives, then severity, then
        # the earliest report of the event.
        primary = sorted(
            group,
            key=lambda r: (not r["located"], -cause_severity(r["cause"]), r["dt"]),
        )[0]
        extras = []
        for rec in sorted(group, key=lambda r: r["dt"]):
            if rec["id"] == primary["id"]:
                continue
            secondary_ids.add(rec["id"])
            extras.append({"id": rec["id"], "cause": rec["cause"], "reported": rec["reported"]})
        extras_by_primary[primary["id"]] = extras

    group: List[Dict] = []
    for rec in usable:
        if group and rec["loc"] == group[-1]["loc"] and \
                (rec["dt"] - group[-1]["dt"]).total_seconds() <= window * 60:
            group.append(rec)
            continue
        close_group(group)
        group = [rec]
    close_group(group)

    return secondary_ids, extras_by_primary
