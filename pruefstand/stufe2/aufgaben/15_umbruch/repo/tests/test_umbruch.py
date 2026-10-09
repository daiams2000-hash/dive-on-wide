import unittest
from umbruch import umbrechen


class T(unittest.TestCase):
    def test_einfach(self):
        self.assertEqual(umbrechen("Das ist ein kleiner Test", 10), "Das ist\nein\nkleiner\nTest")
