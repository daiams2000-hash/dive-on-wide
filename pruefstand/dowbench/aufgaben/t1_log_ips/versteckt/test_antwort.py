import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_antwort(self):
        from collections import Counter
        ips = [z.split()[0] for z in open('access.log') if z.strip()]
        c = Counter(ips)
        a = json.load(open('antwort.json'))
        self.assertEqual(a['verschiedene'], len(c))
        self.assertEqual(a['haeufigste'], max(sorted(c), key=lambda k: c[k]))

