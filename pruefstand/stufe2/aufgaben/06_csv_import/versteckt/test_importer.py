import unittest
from importer import lese_kunden


class T(unittest.TestCase):
    def test_letzte_zeile(self):
        text = "name,ort\nAnna,Kiel\nBen,Jena\n"
        self.assertEqual(lese_kunden(text), [{"name": "Anna", "ort": "Kiel"}, {"name": "Ben", "ort": "Jena"}])

    def test_ohne_zeilenende(self):
        self.assertEqual(lese_kunden("name,ort\nAnna,Kiel"), [{"name": "Anna", "ort": "Kiel"}])

    def test_anfuehrung(self):
        text = 'name,ort\n"Müller, Söhne GmbH",Köln\n'
        self.assertEqual(lese_kunden(text), [{"name": "Müller, Söhne GmbH", "ort": "Köln"}])

    def test_windows(self):
        text = "name,ort\r\nAnna,Kiel\r\nBen,Jena\r\n"
        self.assertEqual(lese_kunden(text)[1], {"name": "Ben", "ort": "Jena"})

    def test_leerzeilen(self):
        text = "name,ort\n\nAnna,Kiel\n\n\nBen,Jena\n\n"
        self.assertEqual([k["name"] for k in lese_kunden(text)], ["Anna", "Ben"])

    def test_nur_kopf(self):
        self.assertEqual(lese_kunden("name,ort\n"), [])
