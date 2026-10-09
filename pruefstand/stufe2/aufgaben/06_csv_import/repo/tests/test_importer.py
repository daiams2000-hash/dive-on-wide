import unittest
from importer import lese_kunden


class T(unittest.TestCase):
    def test_letzte_zeile(self):
        text = "name,ort\nAnna,Kiel\nBen,Jena\n"
        self.assertEqual(len(lese_kunden(text)), 2)
