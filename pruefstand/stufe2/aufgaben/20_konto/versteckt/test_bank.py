import unittest
from konto import Konto
from bank import ueberweisen, sammelueberweisung


def zustand(*konten):
    return [(k.stand, list(k.buchungen)) for k in konten]


class T(unittest.TestCase):
    def test_normal(self):
        a, b = Konto("A", 100), Konto("B")
        ueberweisen(a, b, 30)
        self.assertEqual((a.stand, b.stand), (70, 30))
        self.assertEqual(a.buchungen[-1], ("ab", 30))

    def test_deckung(self):
        a, b = Konto("A", 100), Konto("B")
        vorher = zustand(a, b)
        with self.assertRaises(ValueError):
            ueberweisen(a, b, 500)
        self.assertEqual(zustand(a, b), vorher)

    def test_betrag(self):
        a, b = Konto("A", 100), Konto("B")
        vorher = zustand(a, b)
        for x in (0, -5):
            with self.assertRaises(ValueError):
                ueberweisen(a, b, x)
        with self.assertRaises(ValueError):
            a.einzahlen(0)
        self.assertEqual(zustand(a, b), vorher)

    def test_abheben_direkt(self):
        a = Konto("A", 10)
        with self.assertRaises(ValueError):
            a.abheben(11)
        self.assertEqual(zustand(a), [(10, [])])

    def test_sammel_ok(self):
        a, b, c = Konto("A", 100), Konto("B"), Konto("C", 5)
        sammelueberweisung(a, [(b, 40), (c, 60)])
        self.assertEqual((a.stand, b.stand, c.stand), (0, 40, 65))

    def test_sammel_alles_oder_nichts(self):
        a, b, c = Konto("A", 100), Konto("B", 1), Konto("C", 2)
        vorher = zustand(a, b, c)
        with self.assertRaises(ValueError):
            sammelueberweisung(a, [(b, 40), (c, 40), (b, 40)])
        self.assertEqual(zustand(a, b, c), vorher)
        with self.assertRaises(ValueError):
            sammelueberweisung(a, [(b, 10), (c, -1)])
        self.assertEqual(zustand(a, b, c), vorher)
