import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_json(self):
        d = json.load(open('kunden.json', encoding='utf-8'))
        self.assertEqual([x['name'] for x in d], ['Müller GmbH', 'Schulz & Söhne', 'Å Handel', 'Bäckerei Nord'])
        self.assertEqual([x['umsatz'] for x in d], [1234.5, 980.0, 12000.75, 0.99])
        self.assertEqual([x['stadt'] for x in d], ['Berlin', 'Köln', 'Hamburg', 'Kiel'])

