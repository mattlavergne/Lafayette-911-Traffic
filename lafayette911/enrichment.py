"""Derived-field enrichment for collected incidents.

Pure functions only — no I/O. Given the feed's reported-time string and
location text, these derive the analytical fields stored with each incident:
hour/day-of-week context, rush-hour and school-day flags (Lafayette Parish
School System calendar heuristics), US/Louisiana holiday detection, and a
road-type guess from the street name (used until OSM data refines it).
"""

import re
from datetime import datetime
from typing import Optional


_REPORTED_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{2,4})"
    r"(?:"
    r"(?:\s+|\s*[T@-]\s*)"
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
    r"(?:\s*([AaPp][Mm]))?"
    r")?"
)

# Fallback: MM/DD [HH:MM[:SS] [AM/PM]] without year (assumes current year)
_REPORTED_NOYEAR_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})"
    r"(?!\s*/\s*\d)"  # negative lookahead: not followed by /digits
    r"(?:"
    r"(?:\s+|\s*[T@-]\s*)"
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
    r"(?:\s*([AaPp][Mm]))?"
    r")?"
    r"\s*$"
)


def _parse_reported_dt(reported: str) -> Optional[datetime]:
    """Parse a reported timestamp string into a datetime.

    Handles common feed variants such as:
      - MM/DD/YYYY [HH:MM[:SS] [AM/PM]]
      - MM/DD/YY [HH:MM[:SS] [AM/PM]]
      - MM/DD [HH:MM[:SS] [AM/PM]] (assumes current year)
      - ISO-like YYYY-MM-DD[ T]HH:MM[:SS]
    """
    if not reported:
        return None

    s = reported.strip()
    if not s:
        return None

    def _normalize_two_digit_year(yy: int) -> int:
        return 2000 + yy if yy <= 69 else 1900 + yy

    m = _REPORTED_RE.search(s)
    if m:
        try:
            mm, dd = int(m.group(1)), int(m.group(2))
            yy_raw = m.group(3)
            yy = int(yy_raw)
            if len(yy_raw) == 2:
                yy = _normalize_two_digit_year(yy)
            hh = int(m.group(4)) if m.group(4) is not None else 0
            mi = int(m.group(5)) if m.group(5) is not None else 0
            ap = (m.group(7) or "").lower()
            if ap == "pm" and hh < 12:
                hh += 12
            elif ap == "am" and hh == 12:
                hh = 0
            return datetime(yy, mm, dd, hh, mi)
        except (ValueError, TypeError):
            pass

    m2 = _REPORTED_NOYEAR_RE.search(s)
    if m2:
        try:
            mm, dd = int(m2.group(1)), int(m2.group(2))
            yy = datetime.now().year
            hh = int(m2.group(3)) if m2.group(3) is not None else 0
            mi = int(m2.group(4)) if m2.group(4) is not None else 0
            ap = (m2.group(6) or "").lower()
            if ap == "pm" and hh < 12:
                hh += 12
            elif ap == "am" and hh == 12:
                hh = 0
            return datetime(yy, mm, dd, hh, mi)
        except (ValueError, TypeError):
            pass

    for fmt in (
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y %I:%M %p",
        "%m/%d/%y %H:%M",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if "%y" in fmt and "%Y" not in fmt:
                yy = dt.year % 100
                dt = dt.replace(year=_normalize_two_digit_year(yy))
            return dt
        except (ValueError, TypeError):
            continue

    return None


def _is_holiday(dt: Optional[datetime]) -> bool:
    """Return True if dt falls on a major US federal or Louisiana public holiday.

    Covers: New Year's Day, MLK Day (3rd Mon Jan), Presidents Day (3rd Mon Feb),
    Memorial Day (last Mon May), Juneteenth (Jun 19), Independence Day (Jul 4),
    Labor Day (1st Mon Sep), Columbus/Indigenous Peoples Day (2nd Mon Oct),
    Veterans Day (Nov 11), Thanksgiving (4th Thu Nov), Christmas (Dec 25).
    Also marks the day after Christmas and New Year's Eve as observed when they
    fall on a weekday (common Louisiana practice).
    """
    if dt is None:
        return False
    m, d, dow = dt.month, dt.day, dt.weekday()  # 0=Mon

    # Fixed-date holidays (observed Mon if Sun, observed Fri if Sat)
    def _observed(month: int, day: int) -> bool:
        try:
            h = datetime(dt.year, month, day)
            h_dow = h.weekday()  # 0=Mon, 6=Sun
            if h_dow == 6:  # Sunday → observed Monday
                return m == month and d == day or (
                    datetime(dt.year, month, day + 1 if day < 31 else 1).month == m and
                    datetime(dt.year, month, day + 1 if day < 31 else 1).day == d
                )
            if h_dow == 5:  # Saturday → observed Friday
                return m == month and d == day or (
                    datetime(dt.year, month, day - 1).month == m and
                    datetime(dt.year, month, day - 1).day == d
                )
            return m == month and d == day
        except (ValueError, TypeError):
            return False

    # Simpler observed-day check: within ±1 day for the holiday date
    def _near(month: int, day: int) -> bool:
        try:
            h = datetime(dt.year, month, day)
            delta = abs((dt - h).days)
            if delta == 0:
                return True
            h_dow = h.weekday()
            # Sun holiday → Mon observed
            if h_dow == 6 and (dt - h).days == 1:
                return True
            # Sat holiday → Fri observed
            if h_dow == 5 and (h - dt).days == 1:
                return True
            return False
        except (ValueError, TypeError):
            return False

    if _near(1, 1): return True    # New Year's Day
    if _near(6, 19): return True   # Juneteenth
    if _near(7, 4): return True    # Independence Day
    if _near(11, 11): return True  # Veterans Day
    if _near(12, 25): return True  # Christmas

    # Floating Monday holidays
    def _nth_weekday(year: int, month: int, n: int, weekday: int) -> Optional[int]:
        """Return day-of-month for nth occurrence (1-based) of weekday in month."""
        try:
            count = 0
            for day in range(1, 32):
                try:
                    if datetime(year, month, day).weekday() == weekday:
                        count += 1
                        if count == n:
                            return day
                except ValueError:
                    break
        except Exception:
            pass
        return None

    def _last_weekday(year: int, month: int, weekday: int) -> Optional[int]:
        """Return day-of-month for the LAST occurrence of weekday in month."""
        result = None
        try:
            for day in range(1, 32):
                try:
                    if datetime(year, month, day).weekday() == weekday:
                        result = day
                except ValueError:
                    break
        except Exception:
            pass
        return result

    mlk = _nth_weekday(dt.year, 1, 3, 0)       # MLK Day: 3rd Mon Jan
    if mlk and m == 1 and d == mlk: return True

    pres = _nth_weekday(dt.year, 2, 3, 0)      # Presidents Day: 3rd Mon Feb
    if pres and m == 2 and d == pres: return True

    mem = _last_weekday(dt.year, 5, 0)         # Memorial Day: last Mon May
    if mem and m == 5 and d == mem: return True

    labor = _nth_weekday(dt.year, 9, 1, 0)     # Labor Day: 1st Mon Sep
    if labor and m == 9 and d == labor: return True

    columbus = _nth_weekday(dt.year, 10, 2, 0) # Columbus/Indigenous: 2nd Mon Oct
    if columbus and m == 10 and d == columbus: return True

    thanks = _nth_weekday(dt.year, 11, 4, 3)   # Thanksgiving: 4th Thu Nov
    if thanks and m == 11 and d == thanks: return True

    return False


def _infer_road_type(location: str) -> Optional[str]:
    """Infer OSM highway classification from a location/intersection name.

    Returns one of: motorway, trunk, primary, secondary, residential, or None.
    This provides data for the Road Type filter when OSMnx is unavailable.
    """
    if not location:
        return None
    loc = str(location).upper()

    # Interstate highways (I-10, I-49, I-610 etc.)
    if re.search(r'\bI[-\s]?\d{1,3}\b', loc) or re.search(r'\bINTERSTATE\s+\d', loc):
        return "motorway"

    # US highways (US 90, US-190, US HWY 90)
    if re.search(r'\bU\.?S\.?\s*(?:HWY\s*)?[-]?\s*\d{1,3}\b', loc):
        return "trunk"

    # Louisiana state highways (LA 14, LA-182, HWY 90, HWY-14)
    if re.search(r'\bLA\s*[-]?\s*\d{1,3}\b', loc) or re.search(r'\bHWY\s*[-]?\s*\d{1,3}\b', loc):
        return "primary"

    # Known major arterials in Lafayette Parish
    _PRIMARY_KEYWORDS = (
        "AMBASSADOR CAFFERY", "EVANGELINE THRUWAY", "NW EVANGELINE",
        "JOHNSTON ST", "JOHNSTON STREET", "PINHOOK", "KALISTE SALOOM",
        "HUGH WALLIS", "UNIVERSITY AVE", "UNIVERSITY BLVD",
        "CONGRESS ST", "CAMERON ST", "BERTRAND DR", "VEROT SCHOOL",
        "PONT DES MOUTON", "WILLOW ST", "ERASTE LANDRY", "MUDD AVE",
        "CURRY ST", "OAK PARK BLVD", "RIDGE RD",
        "W PINHOOK", "E PINHOOK", "NORTH UNIVERSITY",
        "S COLLEGE RD", "N COLLEGE RD", "CAMELLIA BLVD",
        "DUHON RD", "YOUNGSVILLE HWY", "SURREY ST",
    )
    for kw in _PRIMARY_KEYWORDS:
        if kw in loc:
            return "primary"

    # Boulevards and parkways → secondary
    if re.search(r'\b(BLVD|BOULEVARD|PKWY|PARKWAY|THRUWAY|EXPRESSWAY)\b', loc):
        return "secondary"

    # Local/residential streets (ST, DR, AVE, CT, CIR, LN, PL, WAY, TRL, LOOP)
    # that didn't match any higher-priority pattern above.
    if re.search(r'\b(ST|STREET|DR|DRIVE|AVE|AVENUE|CT|COURT|CIR|CIRCLE|LN|LANE|PL|PLACE|WAY|TRL|TRAIL|LOOP)\b', loc):
        return "residential"

    return None


def _is_school_day(dt: Optional[datetime]) -> bool:
    """
    Heuristic for Lafayette Parish School System (LPSS) school days.
    Returns True on Mon-Fri during the academic year (roughly mid-Aug through May),
    excluding major holiday breaks.
    """
    if dt is None:
        return False
    dow = dt.weekday()  # 0=Monday, 6=Sunday
    if dow >= 5:
        return False
    month = dt.month
    day = dt.day
    # Summer break: June and July
    if month in {6, 7}:
        return False
    # School year starts mid-August
    if month == 8 and day < 15:
        return False
    # Christmas / winter break: Dec 20 – Jan 3
    if month == 12 and day >= 20:
        return False
    if month == 1 and day <= 3:
        return False
    # Thanksgiving week: approximate Wed–Fri around the fourth Thursday of November
    if month == 11 and 21 <= day <= 27:
        try:
            # Find fourth Thursday of November
            first_nov = datetime(dt.year, 11, 1)
            # Thursday is weekday 3
            offset = (3 - first_nov.weekday()) % 7
            fourth_thursday_day = 1 + offset + 21  # 1st Thu + 3 weeks
            thanksgiving = datetime(dt.year, 11, fourth_thursday_day)
            # Flag Wed before through Fri after
            if thanksgiving.day - 1 <= day <= thanksgiving.day + 1:
                return False
        except (ValueError, TypeError):
            pass
    return True


def _enrich_incident_time(inc: dict) -> None:
    """Derive time-context and road-type fields from the incident and attach to the dict."""
    reported = inc.get("reported", "")
    dt = _parse_reported_dt(str(reported))
    if dt is None:
        inc["hour_of_day"] = None
        inc["day_of_week"] = None
        inc["is_weekend"] = None
        inc["is_rush_hour"] = None
        inc["month"] = None
        inc["is_school_day"] = None
        inc["is_holiday"] = None
    else:
        hour = dt.hour
        dow = dt.weekday()  # 0=Mon, 6=Sun
        is_weekend = 1 if dow >= 5 else 0
        # Rush hour: Mon-Fri 7-9 AM or 4-7 PM (Central)
        is_rush = 1 if (dow < 5 and ((7 <= hour < 9) or (16 <= hour < 19))) else 0
        inc["hour_of_day"] = hour
        inc["day_of_week"] = dow
        inc["is_weekend"] = is_weekend
        inc["is_rush_hour"] = is_rush
        inc["month"] = dt.month
        inc["is_school_day"] = 1 if _is_school_day(dt) else 0
        inc["is_holiday"] = 1 if _is_holiday(dt) else 0

    # Road type: always seed via name inference so the filter works immediately,
    # even when OSMnx is unavailable.  When OSMnx is available, _persist_osm_road_types
    # at render time will overwrite with the authoritative OSM value.
    if not inc.get("road_type"):
        inc["road_type"] = _infer_road_type(inc.get("location", ""))
