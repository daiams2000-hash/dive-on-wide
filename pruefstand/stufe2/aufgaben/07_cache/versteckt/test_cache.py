import unittest
from cache import Cache


class Uhr:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class T(unittest.TestCase):
    def test_frisch(self):
        u = Uhr(); c = Cache(10, uhr=u); c.setze("a", 1); u.t = 5
        self.assertEqual(c.hole("a"), 1)

    def test_abgelaufen(self):
        u = Uhr(); c = Cache(10, uhr=u); c.setze("a", 1); u.t = 11
        self.assertEqual(c.hole("a", "weg"), "weg")

    def test_grenze(self):
        u = Uhr(); c = Cache(10, uhr=u); c.setze("a", 1); u.t = 10
        self.assertIsNone(c.hole("a"))

    def test_ueberschreiben_erneuert(self):
        u = Uhr(); c = Cache(10, uhr=u); c.setze("a", 1); u.t = 8; c.setze("a", 2); u.t = 15
        self.assertEqual(c.hole("a"), 2)

    def test_anzahl(self):
        u = Uhr(); c = Cache(10, uhr=u)
        c.setze("a", 1); u.t = 6; c.setze("b", 2); c.setze("c", 3)
        self.assertEqual(c.anzahl(), 3)
        u.t = 12
        self.assertEqual(c.anzahl(), 2)
        u.t = 16
        self.assertEqual(c.anzahl(), 0)
        self.assertEqual(len(c._daten), 0)
