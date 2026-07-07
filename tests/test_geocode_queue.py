import dataclasses
import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from lafayette911.fetch_incidents import normalize_geocode_key
from lafayette911.main import _geocode_unlocated_incidents, load_config
from lafayette911.state_store import StateStore

logging.basicConfig(level=logging.CRITICAL)
_LOGGER = logging.getLogger("test-queue")


class _QueueCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        csv_path = os.path.join(self._tmp.name, "traffic_incidents.csv")
        with open(csv_path, "w", encoding="utf-8") as handle:
            handle.write("location,cause,reported,assisting,incident_number,latitude,longitude\n")
        self.store = StateStore(os.path.join(self._tmp.name, "incident_index.sqlite"), csv_path)
        self.config = dataclasses.replace(
            load_config(self._tmp.name),
            google_api_key="fake-key",
            geocode_max_requests_per_24h=30,
            geocode_retry_batch=25,
            geocode_retry_reserve=25,
        )

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def _add_unlocated(self, location, number):
        self.store.store_new_incidents([
            {
                "location": location,
                "cause": "ACCIDENT",
                "reported": "07/01/2026 10:00 AM",
                "assisting": "",
                "incident_number": number,
                "latitude": None,
                "longitude": None,
            }
        ])

    def _attempts(self, number):
        row = self.store.conn.execute(
            "SELECT geocode_attempts FROM incidents WHERE incident_number = ?", (number,)
        ).fetchone()
        return row[0] if row else None


class AttemptFairnessTests(_QueueCase):
    def test_budget_starved_incidents_keep_their_retry_lifetime(self):
        """Only incidents actually sent to Google may consume an attempt;
        the ones the budget never reached stay fully queued."""
        for i, loc in enumerate(["ALPHA ST", "BRAVO RD", "CHARLIE AVE"]):
            self._add_unlocated(loc, f"INC{i}")

        # Budget: cap 30, reserve 25. Pre-consume 4 slots → remaining 26 →
        # spendable = 26 - 25 = 1, so only one of the three addresses can be
        # attempted this pass.
        self.store.reserve_geocode_requests(4, max_requests_per_24h=30)

        with patch("lafayette911.fetch_incidents.geocode_with_google", return_value=None) as mock_geo:
            _geocode_unlocated_incidents(self.config, self.store, None, _LOGGER, {}, set())
            self.assertEqual(mock_geo.call_count, 1)

        attempts = [self._attempts(f"INC{i}") for i in range(3)]
        # Exactly one incident carries an attempt; the other two are untouched.
        self.assertEqual(sorted(a or 0 for a in attempts), [0, 0, 1])

    def test_deferred_when_only_reserve_remains(self):
        """With no spendable budget the pass is a pure no-op: nothing
        attempted, no attempt counters moved, no budget consumed."""
        self._add_unlocated("DELTA DR", "INC0")
        self.store.reserve_geocode_requests(10, max_requests_per_24h=30)  # remaining 20 < reserve 25

        with patch("lafayette911.fetch_incidents.geocode_with_google", return_value=None) as mock_geo:
            result = _geocode_unlocated_incidents(self.config, self.store, None, _LOGGER, {}, set())
            mock_geo.assert_not_called()

        self.assertFalse(result)
        self.assertIsNone(self._attempts("INC0"))
        self.assertEqual(self.store.get_remaining_geocode_requests(30), 20)
        # Still queued for when budget frees up.
        self.assertEqual(len(self.store.get_unlocated_incidents()), 1)

    def test_cache_hits_apply_even_with_zero_spendable_budget(self):
        self._add_unlocated("ECHO LN", "INC0")
        self.store.reserve_geocode_requests(10, max_requests_per_24h=30)
        cache = {"ECHO LN": (30.2, -92.0)}
        with patch("lafayette911.fetch_incidents.geocode_with_google", return_value=None) as mock_geo:
            result = _geocode_unlocated_incidents(self.config, self.store, None, _LOGGER, cache, set())
            mock_geo.assert_not_called()
        self.assertTrue(result)
        self.assertEqual(len(self.store.get_unlocated_incidents()), 0)


class BlacklistRetirementTests(_QueueCase):
    def test_blacklisted_addresses_leave_the_queue_without_api_calls(self):
        self._add_unlocated("GHOST TOWN RD", "INC0")
        self._add_unlocated("GOOD ST", "INC1")
        skip = {normalize_geocode_key("GHOST TOWN RD")}

        result_ok = {"lat": 30.2, "lng": -92.0, "address_components": [{"long_name": "Lafayette"}]}
        with patch("lafayette911.fetch_incidents.geocode_with_google", return_value=result_ok) as mock_geo:
            _geocode_unlocated_incidents(self.config, self.store, None, _LOGGER, {}, skip)
            # Only GOOD ST is attempted.
            self.assertEqual(mock_geo.call_count, 1)
            self.assertIn("GOOD ST", mock_geo.call_args[0][1])

        # The blacklisted incident is retired: no longer in the retry queue.
        queue_numbers = {i["incident_number"] for i in self.store.get_unlocated_incidents()}
        self.assertNotIn("INC0", queue_numbers)
        self.assertGreaterEqual(self._attempts("INC0"), 3)


class LegacySeedTests(_QueueCase):
    def test_seed_blacklists_historical_failures_once(self):
        self._add_unlocated("OLD FAILED WAY", "INC0")
        self._add_unlocated("FRESH HOPE BLVD", "INC1")
        self.store.conn.execute(
            "UPDATE incidents SET geocode_attempts = 3 WHERE incident_number = 'INC0'"
        )
        self.store.conn.commit()

        seeded = self.store.seed_negative_cache_from_history()
        self.assertEqual(seeded, 1)
        skippable = self.store.get_skippable_geocode_failures(max_attempts=3)
        self.assertIn(normalize_geocode_key("OLD FAILED WAY"), skippable)
        self.assertNotIn(normalize_geocode_key("FRESH HOPE BLVD"), skippable)

        # One-time: running again is a no-op.
        self.assertEqual(self.store.seed_negative_cache_from_history(), 0)


class RetryConfigDefaultTests(unittest.TestCase):
    def test_defaults(self):
        saved = {}
        for key in ("LAF911_GEOCODE_RETRY_BATCH", "LAF911_GEOCODE_RETRY_RESERVE"):
            saved[key] = os.environ.pop(key, None)
        try:
            cfg = load_config(base_dir="/tmp")
            self.assertEqual(cfg.geocode_retry_batch, 25)
            self.assertEqual(cfg.geocode_retry_reserve, 25)
        finally:
            for key, val in saved.items():
                if val is not None:
                    os.environ[key] = val


if __name__ == "__main__":
    unittest.main()
