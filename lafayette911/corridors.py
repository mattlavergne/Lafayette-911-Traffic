"""Canonical corridor identification from free-text incident locations.

The feed's location strings name the same road dozens of ways:

    3500 AMBASSADOR CAFFERY PKWY
    4500 AMBASSADOR CAFFERY
    AMBASSADOR CAFFERY PKWY AT KALISTE SALOOM RD
    NB AMBASSADOR CAFFERY PARKWAY

All of those should analyze as ONE "AMBASSADOR CAFFERY PKWY" corridor (and the
intersection should ALSO count toward "KALISTE SALOOM RD").  This module owns
that normalization so the exported map data can carry a canonical corridor
list per incident and the web page never has to guess from raw text.

Normalization steps, in order, per road mentioned:

  1. uppercase, collapse whitespace/punctuation
  2. drop leading house numbers and block ranges ("3500", "3500-3600 BLK")
  3. drop unit qualifiers (APT/STE/UNIT/BLDG/LOT ... and everything after)
  4. drop travel-direction prefixes/suffixes (NB/SB/EB/WB[D])
  5. canonicalize highway identifiers (I-10, US 90, LA 182)
  6. normalize directional prefixes (NORTH -> N) and street suffixes
     (STREET -> ST, PARKWAY -> PKWY, ...)
  7. apply the local alias table (known abbreviations / suffix-less names)

`corridor_ids()` splits intersections ("X AT Y", "X & Y", "X / Y") and returns
each road once, so one incident can legitimately contribute to two corridors
— but never twice to the same one.
"""

import re
from typing import List

# Street-suffix canonical forms. Keys are full words / common variants,
# values are the canonical postal-style abbreviation.
_SUFFIXES = {
    "STREET": "ST", "STR": "ST", "ST": "ST",
    "ROAD": "RD", "RD": "RD",
    "BOULEVARD": "BLVD", "BLVD": "BLVD", "BLV": "BLVD",
    "AVENUE": "AVE", "AVE": "AVE", "AV": "AVE",
    "PARKWAY": "PKWY", "PKWY": "PKWY", "PKY": "PKWY", "PW": "PKWY",
    "HIGHWAY": "HWY", "HWY": "HWY",
    "DRIVE": "DR", "DR": "DR",
    "LANE": "LN", "LN": "LN",
    "COURT": "CT", "CT": "CT",
    "PLACE": "PL", "PL": "PL",
    "CIRCLE": "CIR", "CIR": "CIR",
    "TRAIL": "TRL", "TRL": "TRL",
    "TERRACE": "TER", "TER": "TER",
    "LOOP": "LOOP",
    "ALLEY": "ALY", "ALY": "ALY",
    "EXPRESSWAY": "EXPY", "EXPY": "EXPY",
    "FREEWAY": "FWY", "FWY": "FWY",
    "THRUWAY": "THRUWAY", "THWY": "THRUWAY", "TRWY": "THRUWAY",
    "BYPASS": "BYP", "BYP": "BYP",
    "CROSSING": "XING", "XING": "XING",
    "EXTENSION": "EXT", "EXT": "EXT",
    "PLAZA": "PLZ", "PLZ": "PLZ",
    "SQUARE": "SQ", "SQ": "SQ",
    "POINT": "PT", "PT": "PT",
    "COVE": "CV", "CV": "CV",
    "BEND": "BND", "BND": "BND",
    "ROW": "ROW", "RUN": "RUN", "WAY": "WAY", "PASS": "PASS", "PATH": "PATH",
}

_DIRECTIONS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "N": "N", "S": "S", "E": "E", "W": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
    "NE": "NE", "NW": "NW", "SE": "SE", "SW": "SW",
}

# Travel-direction tokens that describe the vehicle, not the road.
_TRAVEL_DIRS = {"NB", "SB", "EB", "WB", "NBD", "SBD", "EBD", "WBD"}

# Unit/structure qualifiers: the token and everything after it is dropped.
_UNIT_QUALIFIERS = {
    "APT", "APTS", "APARTMENT", "APARTMENTS", "STE", "SUITE", "UNIT",
    "BLDG", "BUILDING", "LOT", "RM", "ROOM", "FL", "FLOOR", "TRLR",
    "TRAILER", "SPC", "SPACE", "#",
}

# Local alias table: canonicalized-form -> preferred corridor id. Applied
# LAST so all spelling variants funnel through normalization first. Add
# entries here as data reveals more variants.
_ALIASES = {
    "AMBASSADOR CAFFERY": "AMBASSADOR CAFFERY PKWY",
    "AMB CAFFERY": "AMBASSADOR CAFFERY PKWY",
    "AMB CAFFERY PKWY": "AMBASSADOR CAFFERY PKWY",
    "AMBASSADOR CAFFERY PW": "AMBASSADOR CAFFERY PKWY",
    "EVANGELINE": "EVANGELINE THRUWAY",
    "EVANGELINE THWY": "EVANGELINE THRUWAY",
    "NW EVANGELINE THRUWAY": "EVANGELINE THRUWAY",
    "SE EVANGELINE THRUWAY": "EVANGELINE THRUWAY",
    "SW EVANGELINE THRUWAY": "EVANGELINE THRUWAY",
    "NE EVANGELINE THRUWAY": "EVANGELINE THRUWAY",
    "JOHNSTON": "JOHNSTON ST",
    "KALISTE SALOOM": "KALISTE SALOOM RD",
    "PINHOOK": "PINHOOK RD",
    "E PINHOOK RD": "PINHOOK RD",
    "W PINHOOK RD": "PINHOOK RD",
    "CAMELLIA": "CAMELLIA BLVD",
    "VEROT SCHOOL": "VEROT SCHOOL RD",
    "UNIVERSITY": "UNIVERSITY AVE",
    "CONGRESS": "CONGRESS ST",
    "WILLOW": "WILLOW ST",
}

# Intersection separators: "X AT Y", "X & Y", "X / Y", "X @ Y", "X NEAR Y".
_INTERSECTION_SPLIT = re.compile(
    r"\s+(?:AT|&+|/|@|NEAR|AND)\s+|\s*[&/]\s*"
)

# Leading house number or block range: "3500", "3500-3600", "3500 BLK",
# "3500 BLOCK OF".
_HOUSE_NUMBER = re.compile(
    r"^\s*\d+[A-Z]?(?:\s*-\s*\d+[A-Z]?)?\s+(?:BLK\s+(?:OF\s+)?|BLOCK\s+(?:OF\s+)?)?"
)

# Highway identifiers. Order matters: interstates, then US, then LA state.
_INTERSTATE = re.compile(r"^(?:I|INT|INTERSTATE)[\s.-]*(\d{1,3})$")
_US_HWY = re.compile(r"^(?:US|U\.S\.)[\s.-]*(?:HWY[\s.-]*)?(\d{1,3})$")
_LA_HWY = re.compile(r"^(?:LA|LA\.)[\s.-]*(?:HWY[\s.-]*)?(\d{1,4})$")
_MILE_MARKER = re.compile(r"^(?:MM|MILE\s+MARKER|MILEMARKER)\s*\d*$")


def _canonical_highway(text: str):
    """Return 'I-10' / 'US 90' / 'LA 182' for highway phrasings, else None."""
    compact = re.sub(r"\s+", " ", text.strip())
    m = _INTERSTATE.match(compact)
    if m:
        return "I-" + m.group(1)
    m = _US_HWY.match(compact)
    if m:
        return "US " + m.group(1)
    m = _LA_HWY.match(compact)
    if m:
        return "LA " + m.group(1)
    return None


def normalize_corridor(road: str) -> str:
    """Normalize ONE road mention to its canonical corridor id ('' if none)."""
    s = str(road or "").upper()
    # Unify punctuation to spaces (keep - for ranges/highways, handled below).
    s = re.sub(r"[.,;:()\"']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""

    # Highway forms may carry directional glue ("I-10 E", "I 10 WB").
    tokens_probe = s.split(" ")
    probe = " ".join(
        t for t in tokens_probe
        if t not in _TRAVEL_DIRS and t not in ("N", "S", "E", "W")
    )
    hw = _canonical_highway(probe or s)
    if hw:
        return hw

    s = _HOUSE_NUMBER.sub("", s).strip()
    # A bare number (or range) is an address fragment, not a road.
    if not s or re.fullmatch(r"\d+[A-Z]?(\s*-\s*\d+[A-Z]?)?", s):
        return ""

    tokens = s.split(" ")

    # Cut at the first unit qualifier ("... APT 4", "... SUITE B").
    for i, tok in enumerate(tokens):
        if tok in _UNIT_QUALIFIERS or tok.startswith("#"):
            tokens = tokens[:i]
            break
    if not tokens:
        return ""

    # Drop pure travel-direction tokens anywhere ("NB", "SB", ...).
    tokens = [t for t in tokens if t not in _TRAVEL_DIRS]
    if not tokens:
        return ""
    if _MILE_MARKER.match(" ".join(tokens)):
        return ""

    # Re-check highway now that direction/house noise is gone ("10 I"? no —
    # but "US HWY 90" with a house number stripped, yes).
    hw = _canonical_highway(" ".join(tokens))
    if hw:
        return hw

    # Normalize a directional PREFIX (only the first token, so "WEST ST"
    # keeps its name but "WEST CONGRESS STREET" becomes "W CONGRESS ST").
    if len(tokens) >= 2 and tokens[0] in _DIRECTIONS:
        tokens[0] = _DIRECTIONS[tokens[0]]

    # Normalize the street SUFFIX (last token only).
    if len(tokens) >= 2 and tokens[-1] in _SUFFIXES:
        tokens[-1] = _SUFFIXES[tokens[-1]]

    out = " ".join(tokens).strip()
    # Alias table: try the full form, then the form without its directional
    # prefix (so "W PINHOOK RD" can alias to "PINHOOK RD").
    if out in _ALIASES:
        return _ALIASES[out]
    if len(tokens) >= 2 and tokens[0] in ("N", "S", "E", "W", "NE", "NW", "SE", "SW"):
        rest = " ".join(tokens[1:])
        if rest in _ALIASES:
            return _ALIASES[rest]
    return out


def corridor_ids(location: str) -> List[str]:
    """All canonical corridors a location mentions (deduped, order kept).

    Intersections contribute each named road once:

    >>> corridor_ids("AMBASSADOR CAFFERY PKWY AT KALISTE SALOOM RD")
    ['AMBASSADOR CAFFERY PKWY', 'KALISTE SALOOM RD']
    """
    raw = str(location or "").strip()
    if not raw:
        return []
    seen = []
    for part in _INTERSECTION_SPLIT.split(raw.upper()):
        cid = normalize_corridor(part)
        if cid and cid not in seen:
            seen.append(cid)
    return seen
