import unittest
import preise


class T(unittest.TestCase):
    def test_halber_cent(self):
        self.assertEqual(preise.brutto(0.5), 0.6)
