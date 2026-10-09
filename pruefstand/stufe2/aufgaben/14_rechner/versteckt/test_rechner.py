import unittest
from rechner import rechne


class T(unittest.TestCase):
    def test_punkt_vor_strich(self):
        self.assertEqual(rechne("2+3*4"), 14)
        self.assertEqual(rechne("10-2*3+1"), 5)

    def test_klammern(self):
        self.assertEqual(rechne("(2+3)*4"), 20)
        self.assertEqual(rechne("((1))"), 1)
        self.assertEqual(rechne("2*(3+(4-1))*2"), 24)

    def test_negativ(self):
        self.assertEqual(rechne("-3+5"), 2)
        self.assertEqual(rechne("2*(-4)"), -8)

    def test_division(self):
        self.assertEqual(rechne("10/4"), 2.5)
        self.assertEqual(rechne("8/2/2"), 2)
        with self.assertRaises(ValueError):
            rechne("1/0")

    def test_potenz(self):
        self.assertEqual(rechne("2^3^2"), 512)
        self.assertEqual(rechne("2*3^2"), 18)

    def test_leerzeichen(self):
        self.assertEqual(rechne(" 1 + 2 * 3 "), 7)

    def test_ungueltig(self):
        for x in ["2+", "2 3", "(1+2", "", "2*/3", "a+1", ")"]:
            with self.subTest(x=x):
                with self.assertRaises(ValueError):
                    rechne(x)
