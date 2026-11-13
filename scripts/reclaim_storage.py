#!/usr/bin/env python3
"""Report what is using the disk, and reclaim what is safe to reclaim.

Nothing here touches incident data.  It only removes regenerable cache and
leftover temporary files and (optionally) compacts the SQLite store; every
incident stays in both ``traffic_incidents.csv`` and ``incident_index.sqlite``.

    python scripts/reclaim_storage.py              # report only (default)
    python scripts/reclaim_storage.py --apply      # delete dead cache/temp files
    python scripts/reclaim_storage.py --apply --vacuum  # also compact SQLite

The report has three parts: what this service stores (and how fast that is
really growing), what it can delete for you, and the usual space hogs on a
Raspberry Pi that are outside this project — with the command for each, since
those need ``sudo`` and are yours to decide.

``--vacuum`` rebuilds the database, which needs free space roughly equal to the
current file size while it runs and takes a write lock for the duration, so it
is opt-in and best run with the service stopped.
"""

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lafayette911.config import load_config  # noqa: E402
from lafayette911.map_render import (  # noqa: E402
    _LEGACY_OSM_CACHE_RE,
    _current_osm_cache_names,
    _osm_cache_retention_seconds,
    _read_active_bbox,
    prune_osm_cache,
)

# osmnx names its raw Overpass response cache files by the SHA-1 of the
# request URL.  Only files of exactly that shape are ever removed from
# ``cache/`` — anything else in a folder with such a generic name is left alone.
_OSMNX_RAW_CACHE_RE = re.compile(r"^[0-9a-f]{40}\.json$")

# A publish that was killed mid-run leaves its working copy of the site behind.
_STALE_TEMP_SECONDS = 3600


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} TB"


def file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def tree_size(path: str) -> int:
    """Bytes under ``path`` (a file or a directory), never following links."""
    if os.path.islink(path):
        return 0
    if os.path.isfile(path):
        return file_size(path)
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            full = os.path.join(root, name)
            if not os.path.islink(full):
                total += file_size(full)
    return total


def dir_report(path: str, max_age_seconds: int, keep_bbox):
    """Size the cache directory and split out what a prune would remove."""
    total = count = legacy_bytes = legacy_count = stale_bytes = stale_count = 0
    keep = _current_osm_cache_names(keep_bbox) if keep_bbox else set()
    now = time.time()
    try:
        names = os.listdir(path)
    except Exception:
        return (0, 0, 0, 0, 0, 0)
    for name in names:
        full = os.path.join(path, name)
        if not os.path.isfile(full):
            continue
        size = file_size(full)
        total += size
        count += 1
        if _LEGACY_OSM_CACHE_RE.match(name):
            legacy_bytes += size
            legacy_count += 1
            continue
        if name in keep or not max_age_seconds:
            continue
        try:
            if (now - os.path.getmtime(full)) > max_age_seconds:
                stale_bytes += size
                stale_count += 1
        except Exception:
            continue
    return (total, count, legacy_bytes, legacy_count, stale_bytes, stale_count)


def sqlite_free_bytes(db_path: str) -> int:
    """Bytes held by the DB file but not in use — what VACUUM would return."""
    if not os.path.exists(db_path):
        return 0
    try:
        conn = sqlite3.connect(db_path)
        try:
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
        finally:
            conn.close()
        return int(page_size) * int(free_pages)
    except Exception:
        return 0


def osmnx_raw_cache_files(base_dir: str):
    """osmnx's raw Overpass downloads under ``<base>/cache`` (see
    ``map_render._load_osmnx``): written on every road-network download, never
    read back by this service, never deleted by osmnx."""
    folder = os.path.join(base_dir, "cache")
    out = []
    try:
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if _OSMNX_RAW_CACHE_RE.match(name) and os.path.isfile(full) and not os.path.islink(full):
                out.append(full)
    except Exception:
        pass
    return out


def _looks_like_publish_dir(path: str) -> bool:
    return (os.path.isdir(os.path.join(path, ".git"))
            and os.path.isfile(os.path.join(path, "index.html"))
            and os.path.isfile(os.path.join(path, "traffic_data.js")))


def stale_temp_paths(base_dir: str, tmp_root: str = None, now: float = None):
    """Leftovers from interrupted runs: abandoned publish working copies in the
    system temp dir, and half-written files in the service's ``.tmp`` folders.
    Only things untouched for an hour, so a run in progress is never hit."""
    tmp_root = tmp_root or tempfile.gettempdir()
    now = now or time.time()
    out = []

    def old(path):
        try:
            return (now - os.path.getmtime(path)) > _STALE_TEMP_SECONDS
        except Exception:
            return False

    try:
        for name in os.listdir(tmp_root):
            full = os.path.join(tmp_root, name)
            if os.path.islink(full) or not os.path.isdir(full) or not old(full):
                continue
            # Current runs use the laf911-pages.* prefix; older versions of
            # publish_pages.sh used a bare mktemp name, recognised by content.
            if name.startswith("laf911-pages.") or (name.startswith("tmp.") and _looks_like_publish_dir(full)):
                out.append(full)
    except Exception:
        pass
    seen = set()
    for folder in (os.path.join(base_dir, ".tmp"),):
        if folder in seen:
            continue
        seen.add(folder)
        try:
            for name in os.listdir(folder):
                full = os.path.join(folder, name)
                if os.path.isfile(full) and not os.path.islink(full) and old(full):
                    out.append(full)
        except Exception:
            pass
    return out


def remove_paths(paths) -> int:
    reclaimed = 0
    for path in paths:
        size = tree_size(path)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except Exception:
            continue
        reclaimed += size
    return reclaimed


def growth_estimate(db_path: str, data_bytes: int):
    """(incidents, incidents added in the last 30 days, bytes per incident)."""
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        try:
            total = conn.execute("SELECT COUNT(1) FROM incidents").fetchone()[0]
            cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 30 * 86400))
            recent = conn.execute(
                "SELECT COUNT(1) FROM incidents WHERE created_at >= ?", (cutoff,)
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return (0, 0, 0.0)
    return (int(total), int(recent), (data_bytes / total) if total else 0.0)


def _run(cmd) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def system_report(base_dir: str) -> None:
    """Read-only look at the usual Raspberry Pi space hogs outside this
    project. Nothing here is deleted by this script."""
    print("\nElsewhere on this machine (not touched by this script):")
    rows = []
    journal = _run(["journalctl", "--disk-usage"])
    m = re.search(r"take up ([\d.]+\s*[KMGT]?B?)", journal)
    if m:
        rows.append(("systemd journal", m.group(1), "sudo journalctl --vacuum-size=100M"))
    for label, path, fix in (
        ("apt package cache", "/var/cache/apt/archives", "sudo apt clean"),
        ("other logs", "/var/log", "sudo du -sh /var/log/* | sort -h | tail"),
        ("pip download cache", os.path.expanduser("~/.cache/pip"), "pip cache purge"),
        ("Python virtualenv", os.path.join(base_dir, ".venv"), "(needed — listed for scale)"),
        ("git history", os.path.join(base_dir, ".git"), "git -C %s gc --prune=now" % base_dir),
    ):
        if os.path.exists(path):
            rows.append((label, human(tree_size(path)), fix))
    for label, size, fix in rows:
        print(f"  {label:<20} {size:>12}  {fix}")
    print("  To see what is biggest on the whole card:")
    print("    sudo du -xh / --max-depth=2 2>/dev/null | sort -h | tail -25")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="actually delete dead cache and temp files (default: report only)",
    )
    parser.add_argument(
        "--vacuum", action="store_true",
        help="also VACUUM the SQLite store (needs temporary free space; implies --apply)",
    )
    args = parser.parse_args()
    apply_changes = args.apply or args.vacuum

    # Same resolution order the service uses, so this reports on the paths the
    # running service actually writes to; falls back to the repo the script
    # lives in when LAF911_BASE_DIR is unset.
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = load_config(os.getenv("LAF911_BASE_DIR") or repo_dir)
    base = config.base_dir

    print("Lafayette 911 Traffic — storage report")
    print(f"  base dir: {base}")
    try:
        usage = shutil.disk_usage(base)
        print(f"  disk:     {human(usage.free)} free of {human(usage.total)}")
    except Exception:
        usage = None

    print("\nData (never deleted):")
    data_bytes = 0
    for label, path in (
        ("CSV archive", config.csv_path),
        ("SQLite store", config.db_path),
        ("SQLite WAL", config.db_path + "-wal"),
    ):
        if label == "SQLite WAL" and not os.path.exists(path):
            continue
        data_bytes += file_size(path)
        print(f"  {label:<14} {human(file_size(path)):>12}  {path}")

    total, recent, per_incident = growth_estimate(config.db_path, data_bytes)
    if total and per_incident:
        per_day = recent / 30.0 * per_incident
        print(f"  {total:,} incidents at ~{human(per_incident)} each; "
              f"{recent:,} added in the last 30 days = ~{human(per_day * 365)} per year.")
        if usage and per_day > 0:
            years = usage.free / (per_day * 365)
            print(f"  At that rate the incident data alone would take "
                  f"{'over 100' if years > 100 else '%.0f' % years} years to fill the free space.")
            if years > 20:
                print("  If free space is dropping faster than that, the cause is below or outside this project.")

    print("\nGenerated output (overwritten each render, does not accumulate):")
    for label, path in (
        ("map HTML", config.map_path),
        ("data JS", config.datajs_path),
        ("route map tiles", os.path.join(base, "route_map_cache")),
    ):
        if os.path.exists(path):
            print(f"  {label:<16} {human(tree_size(path)):>12}  {path}")

    active = _read_active_bbox(config.osm_cache_dir)
    retention = _osm_cache_retention_seconds()
    (
        osm_total, count, legacy_bytes, legacy_count, stale_bytes, stale_count,
    ) = dir_report(config.osm_cache_dir, retention, active)
    raw_files = osmnx_raw_cache_files(base)
    raw_bytes = sum(file_size(f) for f in raw_files)
    temp_paths = stale_temp_paths(base)
    temp_bytes = sum(tree_size(p) for p in temp_paths)
    free_bytes = sqlite_free_bytes(config.db_path)

    print("\nReclaimable by this script:")
    print(f"  {'OSM cache, dead':<22} {human(legacy_bytes + stale_bytes):>12}  "
          f"{legacy_count + stale_count} of {count} files in {config.osm_cache_dir} ({human(osm_total)} total)")
    print(f"  {'osmnx raw downloads':<22} {human(raw_bytes):>12}  "
          f"{len(raw_files)} files in {os.path.join(base, 'cache')}")
    print(f"  {'interrupted-run temp':<22} {human(temp_bytes):>12}  {len(temp_paths)} leftover item(s)")
    print(f"  {'SQLite free pages':<22} {human(free_bytes):>12}  needs --vacuum")
    reclaimable = legacy_bytes + stale_bytes + raw_bytes + temp_bytes
    print(f"  {'total':<22} {human(reclaimable):>12}  (+ {human(free_bytes)} with --vacuum)")

    # Anything else sitting in the project folder, so a surprise shows up.
    known = {os.path.abspath(p) for p in (
        config.csv_path, config.db_path, config.map_path, config.datajs_path,
        config.osm_cache_dir, os.path.join(base, "cache"), os.path.join(base, ".venv"),
        os.path.join(base, ".git"), os.path.join(base, "route_map_cache"),
    )}
    others = []
    try:
        for name in os.listdir(base):
            full = os.path.abspath(os.path.join(base, name))
            if full in known:
                continue
            size = tree_size(full)
            if size >= 5 * 1024 * 1024:
                others.append((size, full))
    except Exception:
        pass
    if others:
        print("\nOther large items in the project folder (not touched — check them yourself):")
        for size, full in sorted(others, reverse=True)[:10]:
            print(f"  {human(size):>12}  {full}")

    system_report(base)

    if not apply_changes:
        print("\nReport only. Re-run with --apply to delete the reclaimable files.")
        return 0

    removed, reclaimed = prune_osm_cache(config.osm_cache_dir)
    print(f"\nPruned OSM cache: {removed} files, {human(reclaimed)} reclaimed")
    if not active:
        print("  (no render has run yet, so the live bounding box is unknown —")
        print("   the current road network is kept until a render confirms it)")
    print(f"Removed osmnx raw downloads: {len(raw_files)} files, {human(remove_paths(raw_files))} reclaimed")
    print(f"Removed interrupted-run temp files: {len(temp_paths)} items, {human(remove_paths(temp_paths))} reclaimed")

    if args.vacuum:
        if not os.path.exists(config.db_path):
            print("VACUUM skipped: no database at", config.db_path)
        else:
            before = file_size(config.db_path)
            # isolation_level=None: VACUUM cannot run inside a transaction.
            conn = sqlite3.connect(config.db_path, isolation_level=None)
            try:
                conn.execute("VACUUM")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()
            after = file_size(config.db_path)
            print(f"VACUUM: {human(before)} -> {human(after)} ({human(before - after)} reclaimed)")

    print("\nDone. No incident data was touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
