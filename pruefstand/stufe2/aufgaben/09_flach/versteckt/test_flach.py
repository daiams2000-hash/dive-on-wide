import unittest
from flach import flach


class T(unittest.TestCase):
    def test_dict(self):
        self.assertEqual(flach({"a": {"b": 1}, "c": 2}), {"a.b": 1, "c": 2})

    def test_liste(self):
        self.assertEqual(flach({"a": [1, {"b": 2}]}), {"a.0": 1, "a.1.b": 2})

    def test_leer(self):
        self.assertEqual(flach({"x": {}, "y": []}), {"x": {}, "y": []})

    def test_oben_liste(self):
        self.assertEqual(flach([5, 6]), {"0": 5, "1": 6})

    def test_tief(self):
        self.assertEqual(flach({"a": [[1, 2], {"b": [3]}]}), {"a.0.0": 1, "a.0.1": 2, "a.1.b.0": 3})

    def test_zahlschluessel(self):
        self.assertEqual(flach({1: {"a": None}}), {"1.a": None})
