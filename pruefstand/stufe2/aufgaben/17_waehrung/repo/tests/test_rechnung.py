import unittest
import rechnung


class T(unittest.TestCase):
    def test_zeile(self):
        self.assertEqual(rechnung.zeile("Kaffee", 3, 2.5), "Kaffee: 3 x 2,50 € = 7,50 €")
