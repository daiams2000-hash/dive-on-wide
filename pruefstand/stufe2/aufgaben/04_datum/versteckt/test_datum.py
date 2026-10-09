import unittest
from datetime import date
from datum import parse_datum


class T(unittest.TestCase):
    def test_deutsch(self):
        self.assertEqual(parse_datum("13.09.2026"), date(2026, 9, 13))

    def test_iso(self):
        self.assertEqual(parse_datum("2026-09-13"), date(2026, 9, 13))

    def test_kurzjahr(self):
        self.assertEqual(parse_datum("01.02.26"), date(2026, 2, 1))

    def test_rand(self):
        self.assertEqual(parse_datum("  05.05.2025 "), date(2025, 5, 5))

    def test_ungueltig(self):
        for x in ["32.01.2026", "abc", "", "1.2", "2026-13-01", "aa.bb.cccc", "13.09.2026.1"]:
            with self.subTest(x=x):
                with self.assertRaises(ValueError):
                    parse_datum(x)
