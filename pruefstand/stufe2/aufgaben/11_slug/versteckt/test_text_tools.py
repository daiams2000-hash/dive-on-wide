import unittest
from text_tools import slug


class T(unittest.TestCase):
    def test_einfach(self):
        self.assertEqual(slug("Hallo Welt"), "hallo-welt")

    def test_umlaute(self):
        self.assertEqual(slug("Über Größe & Maß"), "ueber-groesse-mass")
        self.assertEqual(slug("ÄÖÜ"), "aeoeue")

    def test_akzente(self):
        self.assertEqual(slug("Café  crème!!"), "cafe-creme")

    def test_raender(self):
        self.assertEqual(slug("  --Dow.OS 2026--  "), "dow-os-2026")

    def test_leer(self):
        self.assertEqual(slug("!!!"), "n-a")
        self.assertEqual(slug(""), "n-a")

    def test_lang_wortgrenze(self):
        r = slug("wort " * 20)
        self.assertEqual(r, "-".join(["wort"] * 12))
        self.assertLessEqual(len(r), 60)

    def test_lang_hart(self):
        self.assertEqual(slug("a" * 70), "a" * 60)
