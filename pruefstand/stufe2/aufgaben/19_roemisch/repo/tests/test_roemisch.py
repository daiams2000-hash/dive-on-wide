import unittest
from roemisch import zu_roemisch


class T(unittest.TestCase):
    def test_vier(self):
        self.assertEqual(zu_roemisch(4), "IV")
