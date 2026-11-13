#!/usr/bin/env python3
"""Build ``lafayette911/data/road_stats.json``: length and traffic volume of
every named road in Lafayette Parish.

Crash counts on their own mostly rank roads by how busy they are. Dividing by
how much road there is, and by how many vehicles use it, turns a count into a
rate — "crashes per million vehicle-miles" is the standard way to compare a
six-lane arterial with a side street.

Source: Louisiana DOTD's public Roads & Highways open-data service
(maps.dotd.la.gov) — the "Louisiana Roadways" centreline layer (road names and
segment measures in miles) and the "AADT" layer (annual average daily traffic
per route segment). No key needed.

This is a one-off import, re-run only when you want newer traffic counts
(DOTD publishes yearly):

    python scripts/build_road_stats.py

The output maps the same canonical road ids the incident export uses
(``corridors.normalize_corridor``) to ``[miles, vehicle_miles_per_day]``.
"""

import json
import os
import re
import sys
from collections import defaultdict

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lafayette911.corridors import normalize_corridor  # noqa: E402

BASE = "https://maps.dotd.la.gov/road/rest/services/Roads_and_Highways_OpenData/FeatureServer"
ROADS_LAYER, AADT_LAYER = 49, 52
PARISH = "Lafayette"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "lafayette911", "data", "road_stats.json")

# RouteID = <parish>_<route name>_<direction>_<type>_<sequence>. Type is a
# digit for through roads (1/2 mainline, 3/4 frontage) and a letter for ramps
# and connectors, which are not somewhere a "road" crash is logged.
_ROUTE_ID = re.compile(r"^(\d{3}_.+)_(\d)_(\w)_(\d{3})$")


def _query(layer: int, where: str, fields: str):
    resp = requests.post(
        "%s/%d/query" % (BASE, layer),
        data={"where": where, "outFields": fields, "returnGeometry": "false", "f": "json"},
        timeout=300,
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    if body.get("exceededTransferLimit"):
        raise RuntimeError("DOTD truncated the result; narrow the query")
    return [f["attributes"] for f in body.get("features", [])]


def build(roads, aadt):
    """Pure aggregation, separated from the download so it can be tested."""
    # Traffic segments per route, for interval overlap against road segments.
    by_route = defaultdict(list)
    for seg in aadt:
        if seg.get("AADT") is None:
            continue
        by_route[seg["RouteID"]].append((float(seg["FromMeasure"]), float(seg["ToMeasure"]), float(seg["AADT"])))

    # (road id, route base) -> direction -> [miles, vehicle-miles/day, miles with a count]
    acc = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
    for seg in roads:
        name = str(seg.get("FullName") or "").strip()
        match = _ROUTE_ID.match(str(seg.get("RouteID") or ""))
        if not name or not match or not match.group(3).isdigit():
            continue
        upper = name.upper()
        if "PROPOSED" in upper or "@" in upper or " RAMP" in upper:
            continue
        cid = normalize_corridor(upper)
        if not cid:
            continue
        lo, hi = float(seg["FromMeasure"]), float(seg["ToMeasure"])
        if hi <= lo:
            continue
        slot = acc[(cid, match.group(1))][match.group(2)]
        slot[0] += hi - lo
        for a_lo, a_hi, volume in by_route.get(seg["RouteID"], ()):
            overlap = min(hi, a_hi) - max(lo, a_lo)
            if overlap > 0:
                slot[1] += overlap * volume
                slot[2] += overlap

    # A divided road is two routes, one per direction, and DOTD carries the
    # two-way traffic total on one of them. Take the longer direction for
    # length and the larger figure for traffic, so neither is doubled.
    out = defaultdict(lambda: [0.0, 0.0, 0.0])
    for (cid, _base), directions in acc.items():
        out[cid][0] += max(d[0] for d in directions.values())
        best = max(directions.values(), key=lambda d: d[1])
        out[cid][1] += best[1]
        out[cid][2] += best[2]

    result = {}
    for cid, (miles, vmt, counted) in out.items():
        if miles < 0.05:
            continue
        # Scale traffic up to the whole road only when most of it was counted;
        # otherwise the volume is unknown rather than a guess.
        if counted >= 0.6 * miles:
            vmt = vmt * miles / counted
        else:
            vmt = 0.0
        result[cid] = [round(miles, 2), int(round(vmt))]
    return result


def main() -> int:
    roads = _query(ROADS_LAYER, "County_L='%s' OR County_R='%s'" % (PARISH, PARISH),
                   "RouteID,FromMeasure,ToMeasure,FullName")
    route_ids = sorted({r["RouteID"] for r in roads if r.get("RouteID")})
    aadt = []
    for i in range(0, len(route_ids), 150):
        chunk = ",".join("'%s'" % rid.replace("'", "''") for rid in route_ids[i:i + 150])
        aadt += _query(AADT_LAYER, "RouteID IN (%s)" % chunk, "RouteID,FromMeasure,ToMeasure,AADT")
    stats = build(roads, aadt)
    payload = {
        "source": "Louisiana DOTD Roads & Highways open data (Louisiana Roadways + AADT layers)",
        "parish": PARISH,
        "columns": ["miles", "vehicle_miles_per_day"],
        "roads": dict(sorted(stats.items())),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"), sort_keys=False)
        handle.write("\n")
    print("wrote %s: %d roads, %.0f miles" % (OUT, len(stats), sum(v[0] for v in stats.values())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
