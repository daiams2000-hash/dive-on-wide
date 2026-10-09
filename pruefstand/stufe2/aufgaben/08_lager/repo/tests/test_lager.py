import unittest
from lager import Lager


class T(unittest.TestCase):
    def test_getrennt(self):
        a = Lager(); a.hinzufuegen("Schraube", 5)
        b = Lager()
        self.assertEqual(b.bestand("Schraube"), 0)
