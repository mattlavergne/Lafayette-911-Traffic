import unittest

from lafayette911.main import _parse_reported_dt


class DateParsingTests(unittest.TestCase):
    def test_parse_four_digit_year(self):
        dt = _parse_reported_dt("02/14/2026 1:23 PM")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 2)
        self.assertEqual(dt.day, 14)
        self.assertEqual(dt.hour, 13)
        self.assertEqual(dt.minute, 23)

    def test_parse_two_digit_year_in_2000s_window(self):
        dt = _parse_reported_dt("02/14/26 1:23 PM")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 2)
        self.assertEqual(dt.day, 14)

    def test_parse_two_digit_year_in_1900s_window(self):
        dt = _parse_reported_dt("02/14/99 1:23 PM")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 1999)
        self.assertEqual(dt.month, 2)
        self.assertEqual(dt.day, 14)


if __name__ == "__main__":
    unittest.main()
