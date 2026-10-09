import unittest
from aufloesen import reihenfolge


def gueltig(testfall, pakete, r):
    alle = set(pakete) | {d for ds in pakete.values() for d in ds}
    testfall.assertEqual(len(r), len(set(r)))
    testfall.assertEqual(set(r), alle)
    pos = {n: i for i, n in enumerate(r)}
    for p, ds in pakete.items():
        for d in ds:
            testfall.assertLess(pos[d], pos[p])


class T(unittest.TestCase):
    def test_einfach(self):
        self.assertEqual(reihenfolge({"app": ["lib", "log"], "lib": ["log"], "log": []}), ["log", "lib", "app"])

    def test_raute(self):
        p = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}
        gueltig(self, p, reihenfolge(p))

    def test_nicht_gelistet(self):
        self.assertEqual(reihenfolge({"a": ["x"]}), ["x", "a"])

    def test_leer(self):
        self.assertEqual(reihenfolge({}), [])

    def test_zyklus(self):
        with self.assertRaises(ValueError) as cm:
            reihenfolge({"a": ["b"], "b": ["c"], "c": ["a"]})
        m = str(cm.exception)
        self.assertIn("->", m)
        for n in "abc":
            self.assertIn(n, m)

    def test_selbstzyklus(self):
        with self.assertRaises(ValueError):
            reihenfolge({"a": ["a"]})

    def test_lange_kette(self):
        n = 5000
        p = {f"p{i}": [f"p{i+1}"] for i in range(n - 1)}
        p[f"p{n-1}"] = []
        r = reihenfolge(p)
        self.assertEqual((len(r), r[0], r[-1]), (n, f"p{n-1}", "p0"))
