import unittest
import preise


class T(unittest.TestCase):
    def test_halber_cent(self):
        self.assertEqual(preise.brutto(0.5), 0.6)

    def test_normal(self):
        self.assertEqual(preise.brutto(10), 11.9)
        self.assertEqual(preise.brutto(100, 7), 107.0)

    def test_aufrunden_ohne_steuer(self):
        self.assertEqual(preise.brutto(1.005, 0), 1.01)

    def test_summe(self):
        self.assertEqual(preise.summe([(3, 0.1)]), 0.3)
        self.assertEqual(preise.summe([(1, 1.005)]), 1.01)
        self.assertEqual(preise.summe([]), 0.0)

    def test_typ(self):
        self.assertIsInstance(preise.brutto(3), float)
