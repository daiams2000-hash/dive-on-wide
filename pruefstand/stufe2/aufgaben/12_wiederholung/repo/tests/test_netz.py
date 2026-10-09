import unittest
from netz import mit_wiederholung


class T(unittest.TestCase):
    def test_erfolg(self):
        self.assertEqual(mit_wiederholung(lambda: 42), 42)
