import unittest
from duplikate import gemeinsame, doppelte


class T(unittest.TestCase):
    def test_klein(self):
        self.assertEqual(gemeinsame([1, 2, 2, 3], [2, 3, 4]), [2, 2, 3])
        self.assertEqual(doppelte([3, 1, 3, 2, 1, 3]), [3, 1])
