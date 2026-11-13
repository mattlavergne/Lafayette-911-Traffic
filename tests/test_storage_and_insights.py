"""Storage reclaim helpers and the page's Insights view wiring."""

import importlib.util
import os
import sys
import tempfile
import time
import unittest

from lafayette911 import map_render
from lafayette911.map_template import render_map_html

_SPEC = importlib.util.spec_from_file_location(
    "reclaim_storage",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "reclaim_storage.py"),
)
reclaim = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(reclaim)


class OsmnxRawCacheTests(unittest.TestCase):
    def test_only_osmnx_shaped_files_are_candidates(self):
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, "cache"))
            raw = os.path.join(base, "cache", "a" * 40 + ".json")
            for name in ("a" * 40 + ".json", "notes.json", "b" * 39 + ".json", "c" * 40 + ".txt"):
                with open(os.path.join(base, "cache", name), "w") as handle:
                    handle.write("x" * 10)
            self.assertEqual(reclaim.osmnx_raw_cache_files(base), [raw])
            self.assertEqual(reclaim.remove_paths([raw]), 10)
            self.assertEqual(sorted(os.listdir(os.path.join(base, "cache"))),
                             sorted(["notes.json", "b" * 39 + ".json", "c" * 40 + ".txt"]))

    def test_no_cache_folder_is_fine(self):
        with tempfile.TemporaryDirectory() as base:
            self.assertEqual(reclaim.osmnx_raw_cache_files(base), [])

    def test_osmnx_is_loaded_with_its_cache_off(self):
        class Settings:
            use_cache = True

        class FakeOx:
            settings = Settings()

        sys.modules["osmnx"] = FakeOx
        try:
            self.assertFalse(map_render._load_osmnx().settings.use_cache)
        finally:
            del sys.modules["osmnx"]


class StaleTempTests(unittest.TestCase):
    def test_finds_old_publish_leftovers_but_not_fresh_or_unrelated(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as tmp:
            old = time.time() - 7200

            def make(name, publish_like=False, aged=True):
                path = os.path.join(tmp, name)
                os.makedirs(os.path.join(path, ".git") if publish_like else path)
                if publish_like:
                    for f in ("index.html", "traffic_data.js"):
                        open(os.path.join(path, f), "w").close()
                if aged:
                    os.utime(path, (old, old))
                return path

            mine = make("laf911-pages.abc123")
            legacy = make("tmp.XyZ", publish_like=True)
            make("laf911-pages.running", aged=False)
            make("tmp.other")                       # someone else's temp dir
            make("unrelated")
            os.makedirs(os.path.join(base, ".tmp"))
            half = os.path.join(base, ".tmp", "tmpabc")
            open(half, "w").close()
            os.utime(half, (old, old))
            found = reclaim.stale_temp_paths(base, tmp_root=tmp)
            self.assertEqual(sorted(found), sorted([mine, legacy, half]))


class InsightsViewTests(unittest.TestCase):
    def setUp(self):
        self.html = render_map_html(30.22, -92.02, "traffic_data.js")

    def test_view_is_embedded_and_no_token_is_left_behind(self):
        self.assertIn('id="insightsView"', self.html)
        self.assertIn("function openInsights()", self.html)
        self.assertIn("#insightsView.open", self.html)
        self.assertNotIn("__INSIGHTS", self.html)

    def test_entry_points_exist(self):
        self.assertIn('id="insightsBtn"', self.html)
        self.assertIn('id="insightsCta"', self.html)

    def test_rates_skip_days_without_collection(self):
        self.assertIn("covered.has(ivKey(d))", self.html)

    def test_real_feed_crash_spellings_are_accidents(self):
        self.assertIn('"HIT/RUN"', self.html)
        self.assertIn('"TRAFF ACC"', self.html)
        # "ICE" used to match SERVICE CALL / POLICE and file them as hazards.
        self.assertNotIn('"ANIMAL", "ICE"]', self.html)


if __name__ == "__main__":
    unittest.main()
