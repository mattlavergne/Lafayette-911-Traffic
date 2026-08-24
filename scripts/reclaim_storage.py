#!/usr/bin/env python3
"""Report and reclaim disk used by the Lafayette 911 Traffic service.

Nothing here touches incident data.  It only removes regenerable cache files
and compacts the SQLite store; every incident stays in both
``traffic_incidents.csv`` and ``incident_index.sqlite``.

    python scripts/reclaim_storage.py              # report only (default)
    python scripts/reclaim_storage.py --apply      # delete dead cache files
    python scripts/reclaim_storage.py --apply --vacuum  # also compact SQLite

``--vacuum`` rebuilds the database, which needs free space roughly equal to the
current file size while it runs and takes a write lock for the duration, so it
is opt-in and best run with the service stopped.
"""

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lafayette911.config import load_config
from lafayette911.map_render import (  # noqa: E402
    _LEGACY_OSM_CACHE_RE,
    _current_osm_cache_names,
    _osm_cache_retention_seconds,
    _read_active_bbox,
    prune_osm_cache,
)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} TB"


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
        try:
            size = os.path.getsize(full)
        except Exception:
            continue
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


def file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="actually delete dead cache files (default: report only)",
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

    print("Lafayette 911 Traffic — storage report")
    print(f"  base dir: {config.base_dir}\n")

    print("Data (never deleted):")
    for label, path in (
        ("CSV archive", config.csv_path),
        ("SQLite store", config.db_path),
    ):
        print(f"  {label:<14} {human(file_size(path)):>12}  {path}")

    wal = config.db_path + "-wal"
    if os.path.exists(wal):
        print(f"  {'SQLite WAL':<14} {human(file_size(wal)):>12}  {wal}")

    print("\nGenerated output (overwritten each render):")
    for label, path in (
        ("map HTML", config.map_path),
        ("data JS", config.datajs_path),
    ):
        print(f"  {label:<14} {human(file_size(path)):>12}  {path}")

    active = _read_active_bbox(config.osm_cache_dir)
    retention = _osm_cache_retention_seconds()
    retention_days = retention / 86400.0
    (
        total, count, legacy_bytes, legacy_count, stale_bytes, stale_count,
    ) = dir_report(config.osm_cache_dir, retention, active)
    print("\nOSM cache (regenerable):")
    print(f"  {'total':<14} {human(total):>12}  {count} files in {config.osm_cache_dir}")
    print(f"  {'legacy files':<14} {human(legacy_bytes):>12}  {legacy_count} count-keyed leftovers")
    print(f"  {'stale files':<14} {human(stale_bytes):>12}  {stale_count} untouched by a render in {retention_days:g}d")
    print(f"  active bbox:   {active or '(unknown — run a render first)'}")

    free_bytes = sqlite_free_bytes(config.db_path)
    if free_bytes:
        print(f"\nSQLite free pages: {human(free_bytes)} reclaimable by VACUUM")

    if not apply_changes:
        print("\nReport only. Re-run with --apply to delete the dead cache files.")
        return 0

    # max_age_seconds=0 on a dry-run-turned-apply would spare stale artifacts;
    # use the service's own retention so the script and the service agree.
    removed, reclaimed = prune_osm_cache(config.osm_cache_dir)
    print(f"\nPruned OSM cache: {removed} files, {human(reclaimed)} reclaimed")
    if not active:
        print("  (no render has run yet, so the live bounding box is unknown —")
        print("   the current road network is kept until a render confirms it)")

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
