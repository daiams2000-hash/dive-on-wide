import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_json(self):
        self.assertEqual(json.load(open('einstellungen.json')),
                         {'port': 8080, 'name': 'dowbench', 'max_nutzer': 25, 'modus': 'test'})

