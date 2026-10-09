import unittest
import paginierung as p


class T(unittest.TestCase):
    def test_erste_seite(self):
        self.assertEqual(p.seite(list(range(25)), 1), list(range(10)))

    def test_letzte_seite(self):
        self.assertEqual(p.seite(list(range(25)), 3), [20, 21, 22, 23, 24])

    def test_leere_seite(self):
        self.assertEqual(p.seite(list(range(25)), 4), [])

    def test_seitenzahl(self):
        self.assertEqual(p.seitenzahl(25), 3)
        self.assertEqual(p.seitenzahl(20), 2)
        self.assertEqual(p.seitenzahl(0), 0)
        self.assertEqual(p.seitenzahl(7, groesse=3), 3)

    def test_ungueltig(self):
        with self.assertRaises(ValueError):
            p.seite([1, 2], 0)
