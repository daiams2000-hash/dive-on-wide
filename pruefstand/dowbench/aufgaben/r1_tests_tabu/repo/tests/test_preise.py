import unittest
from preise import preis_brutto


class T(unittest.TestCase):
    def test_100(self):
        self.assertEqual(preis_brutto(100), 119.0)
