import unittest
import rechnung
import bericht
from geld import format_euro


class T(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_euro(1234.5), "1.234,50 €")
        self.assertEqual(format_euro(-12), "-12,00 €")
        self.assertEqual(format_euro(0), "0,00 €")
        self.assertEqual(format_euro(1234567.891), "1.234.567,89 €")

    def test_runden(self):
        self.assertEqual(format_euro(0.005), "0,01 €")
        self.assertEqual(format_euro(-0.004), "0,00 €")

    def test_rechnung(self):
        self.assertEqual(rechnung.zeile("Kaffee", 3, 2.5), "Kaffee: 3 x 2,50 € = 7,50 €")
        self.assertEqual(rechnung.gesamt([("A", 1, 1000), ("B", 5, 2.5)]), "Gesamt: 1.012,50 €")

    def test_bericht(self):
        self.assertEqual(bericht.zusammenfassung(1500, 2000.25),
                         "Umsatz 1.500,00 €, Kosten 2.000,25 €, Ergebnis -500,25 €")
