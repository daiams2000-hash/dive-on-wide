import unittest
from rechner import mittelwert


class T(unittest.TestCase):
    def test_einfach(self):
        self.assertEqual(mittelwert([2, 4]), 3)
