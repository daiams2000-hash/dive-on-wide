import unittest
from lru import LRUCache


class T(unittest.TestCase):
    def test_grundlage(self):
        c = LRUCache(2)
        c.setze("a", 1)
        self.assertEqual(c.hole("a"), 1)
