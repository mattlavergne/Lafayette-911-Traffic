"""The OSM cache must not grow without bound.

Before this, the context cache file was named ``osmctx_<bbox>_<n>.json`` where
``n`` was the current incident count, so every render that saw a new incident
wrote a brand-new file and orphaned the previous one — roughly 0.1 GB/day, and
accelerating, with nothing ever deleting them.  The bounding box was also taken
from the raw min/max of every geocoded incident, so a single incident outside
the previous extent changed the cache key and re-downloaded a multi-megabyte
.graphml that likewise stayed forever.
"""

import os
import tempfile
import time
import unittest

from lafayette911.map_render import (
    OSM_CACHE_KIND_CONTEXT,
    OSM_CACHE_KIND_INTERSECTIONS,
    _compute_bbox_from_points,
    _hash_bbox,
    _mark_active_bbox,
    _osm_cache_paths,
    _quantize_bbox,
    prune_osm_cache,
)


class QuantizeBboxTests(unittest.TestCase):
    def test_snaps_outward_so_the_box_still_contains_its_points(self):
        south, north, west, east = _quantize_bbox((30.211, 30.289, -92.061, -91.999))
        self.assertLessEqual(south, 30.211)
        self.assertGreaterEqual(north, 30.289)
        self.assertLessEqual(west, -92.061)
        self.assertGreaterEqual(east, -91.999)

    def test_small_extent_growth_does_not_change_the_cache_key(self):
        # A new incident a few hundred metres past the old extent used to mint a
        # fresh bbox_id, and with it a fresh multi-megabyte .graphml download.
        before = _compute_bbox_from_points(30.15, 30.27, -92.10, -91.96)
        after = _compute_bbox_from_points(30.149, 30.271, -92.101, -91.959)
        self.assertEqual(_hash_bbox(before), _hash_bbox(after))

    def test_a_coordinate_on_a_grid_line_does_not_snap_a_whole_cell_out(self):
        # 30.30 / 0.05 is 605.9999999999999 in binary floating point; a naive
        # ceil() would push the edge to 30.35 and invalidate the cache.
        _, north, _, _ = _quantize_bbox((30.10, 30.30, -92.10, -92.00))
        self.assertAlmostEqual(north, 30.30, places=9)

    def test_a_genuinely_different_region_still_gets_its_own_key(self):
        here = _compute_bbox_from_points(30.15, 30.28, -92.10, -91.95)
        far = _compute_bbox_from_points(29.60, 29.90, -92.20, -92.00)
        self.assertNotEqual(_hash_bbox(here), _hash_bbox(far))


class CachePathTests(unittest.TestCase):
    def test_cache_name_carries_no_incident_count(self):
        _, first = _osm_cache_paths("/cache", "abc123", OSM_CACHE_KIND_CONTEXT)
        _, second = _osm_cache_paths("/cache", "abc123", OSM_CACHE_KIND_CONTEXT)
        self.assertEqual(first, second)
        self.assertEqual(os.path.basename(first), "osmctx_abc123.json")

    def test_the_two_consumers_do_not_share_a_file(self):
        # They store different payload shapes; sharing one name made each
        # invalidate the other's cache and recompute.
        _, ctx = _osm_cache_paths("/cache", "abc123", OSM_CACHE_KIND_CONTEXT)
        _, hot = _osm_cache_paths("/cache", "abc123", OSM_CACHE_KIND_INTERSECTIONS)
        self.assertNotEqual(ctx, hot)


class PruneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, size=64):
        path = os.path.join(self.cache, name)
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
        return path

    def _age(self, path, days):
        """Backdate a file's mtime, as if no render had touched it in that long."""
        stamp = time.time() - days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def test_legacy_count_keyed_files_are_always_removed(self):
        for n in (1200, 1201, 1202):
            self._write(f"osmctx_abc123_{n}.json", size=100)
        removed, reclaimed = prune_osm_cache(self.cache)
        self.assertEqual(removed, 3)
        self.assertEqual(reclaimed, 300)
        self.assertEqual(os.listdir(self.cache), [])

    def test_artifacts_no_render_has_touched_in_days_are_removed(self):
        stale_graph = self._age(self._write("drive_old.graphml", size=500), days=5)
        stale_ctx = self._age(self._write("osmctx_old.json", size=50), days=5)
        keep_graph = self._write("drive_new.graphml", size=500)
        keep_ctx = self._write("osmctx_new.json", size=50)

        removed, reclaimed = prune_osm_cache(self.cache, bbox_id="new")

        self.assertEqual(removed, 2)
        self.assertEqual(reclaimed, 550)
        self.assertFalse(os.path.exists(stale_graph))
        self.assertFalse(os.path.exists(stale_ctx))
        self.assertTrue(os.path.exists(keep_graph))
        self.assertTrue(os.path.exists(keep_ctx))

    def test_a_second_live_bbox_survives_even_though_only_one_is_marked(self):
        # The road-type pass and the intersection pass derive their bounding
        # boxes from slightly different point sets and can land on two ids.
        # Evicting "everything but the marked one" would make them delete each
        # other's road network every cycle and re-download it every cycle.
        _mark_active_bbox(self.cache, "aaa")
        marked = self._write("drive_aaa.graphml", size=500)
        other = self._write("drive_bbb.graphml", size=500)  # touched this render

        removed, _ = prune_osm_cache(self.cache)

        self.assertEqual(removed, 0)
        self.assertTrue(os.path.exists(marked))
        self.assertTrue(os.path.exists(other))

    def test_the_live_bbox_is_kept_however_old_its_files_look(self):
        _mark_active_bbox(self.cache, "abc123")
        live = self._age(self._write("drive_abc123.graphml", size=500), days=30)
        stale = self._age(self._write("drive_stale.graphml", size=500), days=30)

        removed, _ = prune_osm_cache(self.cache)

        self.assertEqual(removed, 1)
        self.assertTrue(os.path.exists(live))
        self.assertFalse(os.path.exists(stale))

    def test_legacy_files_go_even_when_retention_is_disabled(self):
        legacy = self._write("osmctx_abc123_900.json", size=50)
        old_graph = self._age(self._write("drive_old.graphml", size=500), days=30)

        removed, _ = prune_osm_cache(self.cache, max_age_seconds=0)

        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(legacy))
        self.assertTrue(os.path.exists(old_graph))

    def test_unrelated_files_are_never_touched(self):
        keeper = self._age(self._write("notes.txt", size=10), days=30)
        prune_osm_cache(self.cache, bbox_id="new")
        self.assertTrue(os.path.exists(keeper))

    def test_missing_cache_directory_is_a_no_op(self):
        self.assertEqual(prune_osm_cache(os.path.join(self.cache, "nope")), (0, 0))


if __name__ == "__main__":
    unittest.main()
