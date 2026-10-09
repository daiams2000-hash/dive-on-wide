import unittest
from konto import Konto
from bank import ueberweisen


class T(unittest.TestCase):
    def test_deckung(self):
        a, b = Konto("A", 100), Konto("B")
        with self.assertRaises(ValueError):
            ueberweisen(a, b, 500)
