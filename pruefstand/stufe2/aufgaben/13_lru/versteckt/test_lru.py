import unittest
from lru import LRUCache


class T(unittest.TestCase):
    def test_verdraengung(self):
        c = LRUCache(2); c.setze("a", 1); c.setze("b", 2)
        self.assertEqual(c.hole("a"), 1)
        c.setze("c", 3)
        self.assertNotIn("b", c); self.assertIn("a", c); self.assertEqual(len(c), 2)

    def test_ueberschreiben(self):
        c = LRUCache(2); c.setze("a", 1); c.setze("b", 2); c.setze("a", 10); c.setze("c", 3)
        self.assertEqual(c.hole("a"), 10); self.assertNotIn("b", c)

    def test_standard(self):
        self.assertEqual(LRUCache(1).hole("x", "leer"), "leer")

    def test_in_zaehlt_nicht(self):
        c = LRUCache(2); c.setze("a", 1); c.setze("b", 2)
        self.assertTrue("a" in c)
        c.setze("c", 3)
        self.assertNotIn("a", c); self.assertIn("b", c)

    def test_verfehlt_zaehlt_nicht(self):
        c = LRUCache(2); c.setze("a", 1); c.setze("b", 2); c.hole("zzz"); c.setze("c", 3)
        self.assertNotIn("a", c)

    def test_ungueltig(self):
        with self.assertRaises(ValueError):
            LRUCache(0)

    def test_viele(self):
        c = LRUCache(1000)
        for i in range(5000):
            c.setze(i, i)
        self.assertEqual(len(c), 1000)
        self.assertNotIn(3999, c); self.assertIn(4000, c)
