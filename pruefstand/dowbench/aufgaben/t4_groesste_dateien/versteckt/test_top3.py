import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_top3(self):
        alle = []
        for w, _, fs in os.walk('daten'):
            for f in fs:
                p = os.path.join(w, f)
                alle.append((os.path.getsize(p), os.path.relpath(p, 'daten')))
        erwartet = [(p, g) for g, p in sorted(alle, key=lambda t: (-t[0], t[1]))[:3]]
        zeilen = [z.split() for z in open('top3.txt') if z.strip()]
        self.assertEqual([(a, int(b)) for a, b in zeilen], erwartet)

