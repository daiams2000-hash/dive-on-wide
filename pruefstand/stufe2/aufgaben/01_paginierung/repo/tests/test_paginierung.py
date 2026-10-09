import unittest
import paginierung as p


class T(unittest.TestCase):
    def test_erste_seite(self):
        self.assertEqual(p.seite(list(range(25)), 1), list(range(10)))
