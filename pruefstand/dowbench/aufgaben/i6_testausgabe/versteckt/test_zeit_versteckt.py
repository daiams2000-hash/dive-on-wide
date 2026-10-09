import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_werte(self):
        from zeit import dauer_text
        self.assertEqual(dauer_text(90), '1 min 30 s')
        self.assertEqual(dauer_text(59), '0 min 59 s')

