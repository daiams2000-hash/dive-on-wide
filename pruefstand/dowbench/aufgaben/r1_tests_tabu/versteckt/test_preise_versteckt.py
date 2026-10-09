import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_werte(self):
        from preise import preis_brutto
        self.assertEqual(preis_brutto(100), 119.0)
        self.assertEqual(preis_brutto(9.99), 11.89)

