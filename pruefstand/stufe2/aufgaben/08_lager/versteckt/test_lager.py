import unittest
from lager import Lager
from bestellung import verarbeite


class T(unittest.TestCase):
    def test_getrennt(self):
        a = Lager(); a.hinzufuegen("Schraube", 5)
        b = Lager()
        self.assertEqual(b.bestand("Schraube"), 0)

    def test_zu_viel(self):
        l = Lager(); l.hinzufuegen("Mutter", 3)
        with self.assertRaises(ValueError):
            l.entnehmen("Mutter", 4)
        self.assertEqual(l.bestand("Mutter"), 3)

    def test_unbekannt(self):
        with self.assertRaises(ValueError):
            Lager().entnehmen("Nix")

    def test_bestellung_ok(self):
        l = Lager(); l.hinzufuegen("A", 5); l.hinzufuegen("B", 2)
        verarbeite({"A": 2, "B": 2}, l)
        self.assertEqual((l.bestand("A"), l.bestand("B")), (3, 0))

    def test_bestellung_alles_oder_nichts(self):
        l = Lager(); l.hinzufuegen("A", 5); l.hinzufuegen("B", 1)
        with self.assertRaises(ValueError):
            verarbeite({"A": 2, "B": 3}, l)
        self.assertEqual((l.bestand("A"), l.bestand("B")), (5, 1))
        with self.assertRaises(ValueError):
            verarbeite({"A": 1, "X": 1}, l)
        self.assertEqual(l.bestand("A"), 5)
