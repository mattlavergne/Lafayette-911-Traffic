import os
import tempfile
import unittest
from unittest.mock import patch

from lafayette911.fetch_incidents import (
    estimate_needed_geocode_requests,
    geocode_incidents,
    normalize_geocode_key,
)
from lafayette911.main import _record_geocode_outcomes
from lafayette911.state_store import StateStore


def _inc(location, number=None):
    return {
        "location": location,
        "cause": "ACCIDENT",
        "reported": "01/01/2026 10:00 AM",
        "assisting": "",
        "incident_number": number or f"INC::{location}",
    }


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        csv_path = os.path.join(self._tmp.name, "traffic_incidents.csv")
        with open(csv_path, "w", encoding="utf-8") as handle:
            handle.write("location,cause,reported,assisting,incident_number,latitude,longitude\n")
        self.store = StateStore(os.path.join(self._tmp.name, "incident_index.sqlite"), csv_path)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()


class NegativeCacheStoreTests(_StoreCase):
    def test_failures_accumulate_then_become_skippable(self):
        key = normalize_geocode_key("BAD PRIVATE DR")
        self.store.record_geocode_failures([key])
        self.store.record_geocode_failures([key])
        self.assertNotIn(key, self.store.get_skippable_geocode_failures(max_attempts=3))
        self.store.record_geocode_failures([key])
        self.assertIn(key, self.store.get_skippable_geocode_failures(max_attempts=3))

    def test_success_clears_failure_history(self):
        key = normalize_geocode_key("FLAKY RD")
        for _ in range(3):
            self.store.record_geocode_failures([key])
        self.assertIn(key, self.store.get_skippable_geocode_failures())
        self.store.clear_geocode_failures([key])
        self.assertNotIn(key, self.store.get_skippable_geocode_failures())

    def test_retry_window_expires_blacklist(self):
        key = normalize_geocode_key("STALE ST")
        for _ in range(3):
            self.store.record_geocode_failures([key])
        # With a zero retry window every entry is immediately stale again.
        self.assertNotIn(key, self.store.get_skippable_geocode_failures(retry_after_seconds=0))
        self.assertIn(key, self.store.get_skippable_geocode_failures(retry_after_seconds=3600))


class NegativeCacheGeocodeTests(unittest.TestCase):
    def test_skip_keys_prevent_api_calls(self):
        skip = {normalize_geocode_key("KNOWN BAD RD")}
        incidents = [_inc("KNOWN BAD RD"), _inc("GOOD ST")]
        result = {"lat": 30.2, "lng": -92.0, "address_components": []}
        with patch(
            "lafayette911.fetch_incidents.geocode_with_google", return_value=result
        ) as mock_geo:
            attempted = set()
            geocode_incidents(
                None, incidents, "fake-key",
                location_cache={}, skip_keys=skip, attempted_keys=attempted,
            )
            self.assertEqual(mock_geo.call_count, 1)
            self.assertEqual(attempted, {normalize_geocode_key("GOOD ST")})
        self.assertIsNone(incidents[0].get("latitude"))
        self.assertEqual(incidents[1]["latitude"], 30.2)

    def test_estimate_excludes_skip_keys(self):
        skip = {normalize_geocode_key("KNOWN BAD RD")}
        incidents = [_inc("KNOWN BAD RD"), _inc("known  bad rd"), _inc("GOOD ST")]
        self.assertEqual(
            estimate_needed_geocode_requests(incidents, None, skip_keys=skip), 1
        )

    def test_attempted_keys_track_failures_too(self):
        incidents = [_inc("NOWHERE LN")]
        with patch("lafayette911.fetch_incidents.geocode_with_google", return_value=None):
            attempted = set()
            geocode_incidents(
                None, incidents, "fake-key", location_cache={}, attempted_keys=attempted
            )
            self.assertEqual(attempted, {normalize_geocode_key("NOWHERE LN")})


class RecordOutcomesTests(_StoreCase):
    def test_recurring_bad_address_stops_burning_quota(self):
        """End-to-end shape of the budget bleed: the same unresolvable street
        recurs as a new incident every cycle. After max_attempts cycles the
        address is blacklisted and needs no further API budget."""
        bad = "UNRESOLVABLE PRIVATE DR"
        key = normalize_geocode_key(bad)
        with patch("lafayette911.fetch_incidents.geocode_with_google", return_value=None) as mock_geo:
            for cycle in range(5):
                skip = self.store.get_skippable_geocode_failures(max_attempts=3)
                batch = [_inc(bad, number=f"INC{cycle}")]
                needed = estimate_needed_geocode_requests(batch, {}, skip_keys=skip)
                attempted = set()
                geocode_incidents(
                    None, batch, "fake-key",
                    location_cache={}, api_call_limit=needed,
                    skip_keys=skip, attempted_keys=attempted,
                )
                _record_geocode_outcomes(self.store, batch, attempted)
            # Only the first 3 cycles may call Google; cycles 4-5 are skipped.
            self.assertEqual(mock_geo.call_count, 3)
        self.assertIn(key, self.store.get_skippable_geocode_failures(max_attempts=3))

    def test_success_records_nothing_and_clears_history(self):
        loc = "REDEEMED ST"
        key = normalize_geocode_key(loc)
        self.store.record_geocode_failures([key])
        batch = [_inc(loc)]
        result = {"lat": 30.2, "lng": -92.0, "address_components": []}
        with patch("lafayette911.fetch_incidents.geocode_with_google", return_value=result):
            attempted = set()
            geocode_incidents(None, batch, "fake-key", location_cache={}, attempted_keys=attempted)
            _record_geocode_outcomes(self.store, batch, attempted)
        self.assertNotIn(key, self.store.get_skippable_geocode_failures(max_attempts=1))

    def test_unattempted_addresses_not_recorded(self):
        # Budget exhausted: no API calls made, so nothing may be blacklisted.
        batch = [_inc("NEVER TRIED AVE")]
        with patch("lafayette911.fetch_incidents.geocode_with_google", return_value=None) as mock_geo:
            attempted = set()
            geocode_incidents(
                None, batch, "fake-key",
                location_cache={}, api_call_limit=0, attempted_keys=attempted,
            )
            mock_geo.assert_not_called()
            _record_geocode_outcomes(self.store, batch, attempted)
        self.assertEqual(
            self.store.get_skippable_geocode_failures(max_attempts=1), set()
        )


if __name__ == "__main__":
    unittest.main()
