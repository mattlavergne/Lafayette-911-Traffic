import unittest
from unittest.mock import patch

from lafayette911.fetch_incidents import (
    estimate_needed_geocode_requests,
    geocode_incidents,
    normalize_geocode_key,
)
from lafayette911.main import _filter_geocode_results


def _inc(location):
    return {
        "location": location,
        "cause": "ACCIDENT",
        "reported": "01/01/2026 10:00 AM",
        "assisting": "",
        "incident_number": f"INC::{location}",
    }


class GeocodeCacheTests(unittest.TestCase):
    def test_normalize_key_collapses_case_and_whitespace(self):
        self.assertEqual(
            normalize_geocode_key("  w  Congress   St "),
            normalize_geocode_key("W CONGRESS ST"),
        )

    def test_exact_cache_hit_uses_no_api_call(self):
        cache = {"W CONGRESS ST": (30.2, -92.0)}
        incidents = [_inc("W CONGRESS ST")]
        with patch("lafayette911.fetch_incidents.geocode_with_google") as mock_geo:
            geocode_incidents(None, incidents, "fake-key", location_cache=cache)
            mock_geo.assert_not_called()
        self.assertEqual(incidents[0]["latitude"], 30.2)

    def test_normalized_cache_hit_uses_no_api_call(self):
        cache = {"W CONGRESS ST": (30.2, -92.0)}
        incidents = [_inc("w  congress st")]
        with patch("lafayette911.fetch_incidents.geocode_with_google") as mock_geo:
            geocode_incidents(None, incidents, "fake-key", location_cache=cache)
            mock_geo.assert_not_called()
        self.assertEqual(incidents[0]["latitude"], 30.2)
        self.assertEqual(incidents[0]["longitude"], -92.0)

    def test_failed_address_not_retried_within_batch(self):
        incidents = [_inc("NOWHERE RD"), _inc("nowhere  rd"), _inc("NOWHERE RD")]
        with patch(
            "lafayette911.fetch_incidents.geocode_with_google", return_value=None
        ) as mock_geo:
            geocode_incidents(None, incidents, "fake-key", location_cache={})
            self.assertEqual(mock_geo.call_count, 1)

    def test_batch_success_shared_across_duplicates(self):
        result = {"lat": 30.21, "lng": -92.01, "address_components": []}
        incidents = [_inc("JOHNSTON ST"), _inc("johnston  st")]
        with patch(
            "lafayette911.fetch_incidents.geocode_with_google", return_value=result
        ) as mock_geo:
            geocode_incidents(None, incidents, "fake-key", location_cache={})
            self.assertEqual(mock_geo.call_count, 1)
        self.assertEqual(incidents[1]["latitude"], 30.21)

    def test_estimate_dedupes_normalized_duplicates(self):
        cache = {"KNOWN ST": (30.2, -92.0)}
        incidents = [
            _inc("KNOWN ST"),        # exact cache hit
            _inc("known  st"),       # normalized cache hit
            _inc("NEW RD"),          # needs one call
            _inc("new rd"),          # duplicate of NEW RD after normalization
        ]
        self.assertEqual(estimate_needed_geocode_requests(incidents, cache), 1)

    def test_estimate_without_cache(self):
        incidents = [_inc("A ST"), _inc("B ST"), _inc("A ST")]
        self.assertEqual(estimate_needed_geocode_requests(incidents, None), 2)

    def test_rejected_geocode_purged_from_cycle_cache(self):
        # A fresh Google result outside Lafayette Parish is rejected AND removed
        # from the in-cycle cache so later duplicates don't reuse bad coords.
        cache = {"FAR AWAY BLVD": (35.0, -100.0)}
        inc = _inc("FAR AWAY BLVD")
        inc["latitude"] = 35.0
        inc["longitude"] = -100.0
        inc["address_components"] = [{"long_name": "Houston"}]
        _filter_geocode_results([inc], cache)
        self.assertIsNone(inc["latitude"])
        self.assertNotIn("FAR AWAY BLVD", cache)

    def test_cache_sourced_coords_keep_cache_entry(self):
        # Coordinates served from the cache (no address_components) that fail
        # only the bounds check are nulled but do not purge the cache blindly.
        cache = {"EDGE CASE ST": (30.2, -92.0)}
        inc = _inc("EDGE CASE ST")
        inc["latitude"] = 30.2
        inc["longitude"] = -92.0
        _filter_geocode_results([inc], cache)
        self.assertEqual(inc["latitude"], 30.2)
        self.assertIn("EDGE CASE ST", cache)


if __name__ == "__main__":
    unittest.main()
