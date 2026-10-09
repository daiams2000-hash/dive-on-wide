import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_mittel(self):
        self.assertEqual(open('mittel.txt').read().strip().splitlines()[0].strip(), '11.5')

