import unittest
from umbruch import umbrechen


class T(unittest.TestCase):
    def test_einfach(self):
        self.assertEqual(umbrechen("Das ist ein kleiner Test", 10), "Das ist\nein\nkleiner\nTest")

    def test_genau_passend(self):
        self.assertEqual(umbrechen("aa bb cc", 5), "aa bb\ncc")

    def test_langes_wort(self):
        self.assertEqual(umbrechen("abcdefghij kl", 4), "abcd\nefgh\nij\nkl")
        self.assertEqual(umbrechen("xy abcdefghij", 4), "xy\nabcd\nefgh\nij")
        self.assertEqual(umbrechen("abcdefgh", 4), "abcd\nefgh")

    def test_absaetze(self):
        self.assertEqual(umbrechen("a b\n\nc d", 10), "a b\n\nc d")
        self.assertEqual(umbrechen("a\n\n\n\nb", 10), "a\n\nb")
        self.assertEqual(umbrechen("a\n   \nb", 10), "a\n\nb")

    def test_einfacher_umbruch_und_spaces(self):
        self.assertEqual(umbrechen("a\nb", 10), "a b")
        self.assertEqual(umbrechen("a    b", 10), "a b")
        self.assertEqual(umbrechen("   ", 10), "")

    def test_ungueltig(self):
        with self.assertRaises(ValueError):
            umbrechen("a", 0)

    def test_eigenschaft(self):
        text = ("Lorem ipsum dolor sit amet, consetetur sadipscing elitr, sed diam nonumy eirmod tempor "
                "invidunt ut labore et dolore magna aliquyam erat. Donaudampfschifffahrtsgesellschaft ") * 3
        for breite in (5, 12, 30):
            for z in umbrechen(text, breite).split("\n"):
                self.assertLessEqual(len(z), breite)
                self.assertEqual(z, z.strip())
