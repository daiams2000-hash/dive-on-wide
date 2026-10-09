import unittest
from text_tools import slug


class T(unittest.TestCase):
    def test_einfach(self):
        self.assertEqual(slug("Hallo Welt"), "hallo-welt")
