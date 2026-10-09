import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_summe(self):
        self.assertEqual(open('summe.txt').read().strip(), '220.00')

