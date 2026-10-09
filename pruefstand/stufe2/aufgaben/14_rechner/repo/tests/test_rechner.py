import unittest
from rechner import rechne


class T(unittest.TestCase):
    def test_punkt_vor_strich(self):
        self.assertEqual(rechne("2+3*4"), 14)
