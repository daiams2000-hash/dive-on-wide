import unittest
from kontakte import finde

K = [{"name": "Anna Berg"}, {"name": "Bernd Ärger"}, {"name": "Carla Straße"}]


class T(unittest.TestCase):
    def test_klein(self):
        self.assertEqual(finde(K, "anna"), [K[0]])
