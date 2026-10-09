import unittest
from aufloesen import reihenfolge


class T(unittest.TestCase):
    def test_einfach(self):
        self.assertEqual(reihenfolge({"app": ["lib", "log"], "lib": ["log"], "log": []}), ["log", "lib", "app"])
