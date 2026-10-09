import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_werte(self):
        from rechner import mittelwert
        self.assertEqual(mittelwert([1, 2, 3, 4]), 2.5)
        with self.assertRaises(ValueError):
            mittelwert([])

