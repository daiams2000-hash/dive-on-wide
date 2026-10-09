import time
import unittest
from duplikate import gemeinsame, doppelte


class T(unittest.TestCase):
    def test_klein(self):
        self.assertEqual(gemeinsame([1, 2, 2, 3], [2, 3, 4]), [2, 2, 3])
        self.assertEqual(doppelte([3, 1, 3, 2, 1, 3]), [3, 1])
        self.assertEqual(doppelte([2, 1, 2, 1]), [2, 1])
        self.assertEqual(doppelte([]), [])

    def test_gross_gemeinsame(self):
        # 40 000 statt 20 000 (27.09.2026): Auf einer schnellen Linux-VM blieb
        # die quadratische Ausgangsfassung bei 20 000 unter einer Sekunde — der
        # Pruefstand haengt dann vom Rechner ab. Die Aufgabe nennt 20 000–40 000.
        a = list(range(40000))
        b = list(range(20000, 60000))
        t = time.perf_counter()
        r = gemeinsame(a, b)
        dauer = time.perf_counter() - t
        self.assertEqual(r, list(range(20000, 40000)))
        self.assertLess(dauer, 1.0)

    def test_gross_doppelte(self):
        werte = list(range(20000)) * 2
        t = time.perf_counter()
        r = doppelte(werte)
        dauer = time.perf_counter() - t
        self.assertEqual(r, list(range(20000)))
        self.assertLess(dauer, 1.0)
