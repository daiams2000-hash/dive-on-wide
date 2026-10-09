import unittest
from datetime import date
from datum import parse_datum


class T(unittest.TestCase):
    def test_deutsch(self):
        self.assertEqual(parse_datum("13.09.2026"), date(2026, 9, 13))
