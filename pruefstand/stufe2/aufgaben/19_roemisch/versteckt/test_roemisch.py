import unittest
from roemisch import zu_roemisch, von_roemisch


class T(unittest.TestCase):
    def test_zu(self):
        for n, r in [(1, "I"), (4, "IV"), (9, "IX"), (14, "XIV"), (40, "XL"), (90, "XC"), (400, "CD"),
                     (1994, "MCMXCIV"), (2026, "MMXXVI"), (3999, "MMMCMXCIX")]:
            self.assertEqual(zu_roemisch(n), r)

    def test_zu_ungueltig(self):
        for x in [0, 4000, -1, 2.5]:
            with self.assertRaises(ValueError):
                zu_roemisch(x)

    def test_von(self):
        self.assertEqual(von_roemisch("MCMXCIV"), 1994)
        self.assertEqual(von_roemisch("mmxxvi"), 2026)
        self.assertEqual(von_roemisch(" XIV "), 14)

    def test_von_ungueltig(self):
        for x in ["IIII", "IC", "VX", "", "ABC", "MMMM", "IIV", "XM"]:
            with self.subTest(x=x):
                with self.assertRaises(ValueError):
                    von_roemisch(x)

    def test_rundlauf(self):
        for n in range(1, 4000):
            self.assertEqual(von_roemisch(zu_roemisch(n)), n)
