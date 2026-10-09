import unittest
from kontakte import finde

K = [{"name": "Anna Berg"}, {"name": "Bernd Ärger"}, {"name": "Carla Straße"}, {"name": "berta anders"}]


class T(unittest.TestCase):
    def test_klein(self):
        self.assertEqual(finde(K, "anna"), [K[0]])

    def test_umlaut(self):
        self.assertEqual(finde(K, "ärger"), [K[1]])

    def test_eszett(self):
        self.assertEqual(finde(K, "STRASSE"), [K[2]])

    def test_leerzeichen(self):
        self.assertEqual(finde(K, "  berg "), [K[0]])

    def test_leer(self):
        self.assertEqual(finde(K, ""), K)
        self.assertEqual(finde(K, "   "), K)

    def test_reihenfolge(self):
        self.assertEqual(finde(K, "ber"), [K[0], K[1], K[3]])
