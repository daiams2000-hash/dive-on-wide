import unittest
from konfig import lade
import standard


class T(unittest.TestCase):
    def test_teilweise(self):
        r = lade({"db": {"pfad": "x.db"}})
        self.assertEqual(r["db"], {"pfad": "x.db", "timeout": 5, "optionen": {"wal": True}})

    def test_tief(self):
        r = lade({"db": {"optionen": {"cache": 10}}})
        self.assertEqual(r["db"]["optionen"], {"wal": True, "cache": 10})

    def test_unberuehrt(self):
        r = lade({})
        r["db"]["timeout"] = 99
        r["db"]["optionen"]["wal"] = False
        self.assertEqual(lade({})["db"]["timeout"], 5)
        self.assertEqual(standard.STANDARD["db"]["optionen"]["wal"], True)

    def test_nutzer_unberuehrt(self):
        n = {"db": {"pfad": "y.db"}}
        r = lade(n)
        r["db"]["pfad"] = "z.db"
        self.assertEqual(n, {"db": {"pfad": "y.db"}})

    def test_unbekannt(self):
        self.assertEqual(lade({"neu": [1]})["neu"], [1])
        self.assertEqual(lade({"port": 1})["port"], 1)
