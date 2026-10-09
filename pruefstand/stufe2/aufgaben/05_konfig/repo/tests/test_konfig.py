import unittest
from konfig import lade


class T(unittest.TestCase):
    def test_teilweise(self):
        r = lade({"db": {"pfad": "x.db"}})
        self.assertEqual(r["db"]["timeout"], 5)
