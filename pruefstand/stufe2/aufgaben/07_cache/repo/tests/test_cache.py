import unittest
from cache import Cache


class Uhr:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class T(unittest.TestCase):
    def test_frisch(self):
        u = Uhr()
        c = Cache(10, uhr=u)
        c.setze("a", 1)
        u.t = 5
        self.assertEqual(c.hole("a"), 1)
