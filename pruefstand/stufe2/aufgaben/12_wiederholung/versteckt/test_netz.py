import unittest
from netz import mit_wiederholung


class Folge:
    def __init__(self, *ergebnisse):
        self.ergebnisse = list(ergebnisse)
        self.aufrufe = 0

    def __call__(self):
        self.aufrufe += 1
        e = self.ergebnisse.pop(0)
        if isinstance(e, BaseException):
            raise e
        return e


class T(unittest.TestCase):
    def setUp(self):
        self.schlaf = []

    def s(self, d):
        self.schlaf.append(d)

    def test_erfolg(self):
        f = Folge(7)
        self.assertEqual(mit_wiederholung(f, schlafen=self.s), 7)
        self.assertEqual((f.aufrufe, self.schlaf), (1, []))

    def test_zweimal_dann_ok(self):
        f = Folge(ConnectionError(), TimeoutError(), "ok")
        self.assertEqual(mit_wiederholung(f, schlafen=self.s), "ok")
        self.assertEqual(self.schlaf, [0.5, 1.0])

    def test_immer_fehler(self):
        f = Folge(ConnectionError("1"), ConnectionError("2"), ConnectionError("3"))
        with self.assertRaises(ConnectionError) as cm:
            mit_wiederholung(f, schlafen=self.s)
        self.assertEqual(str(cm.exception), "3")
        self.assertEqual((f.aufrufe, self.schlaf), (3, [0.5, 1.0]))

    def test_anderer_fehler(self):
        f = Folge(ValueError("x"), "nie")
        with self.assertRaises(ValueError):
            mit_wiederholung(f, schlafen=self.s)
        self.assertEqual((f.aufrufe, self.schlaf), (1, []))

    def test_eigene_fehler_und_basis(self):
        f = Folge(KeyError(), KeyError(), KeyError())
        with self.assertRaises(KeyError):
            mit_wiederholung(f, versuche=3, basis=2, schlafen=self.s, fehler=(KeyError,))
        self.assertEqual(self.schlaf, [2, 4])

    def test_ein_versuch(self):
        f = Folge(TimeoutError())
        with self.assertRaises(TimeoutError):
            mit_wiederholung(f, versuche=1, schlafen=self.s)
        self.assertEqual(self.schlaf, [])

    def test_ungueltig(self):
        with self.assertRaises(ValueError):
            mit_wiederholung(lambda: 1, versuche=0, schlafen=self.s)
