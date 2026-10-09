import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_skript_laeuft(self):
        if os.path.exists('bericht.txt'): os.remove('bericht.txt')
        subprocess.run([sys.executable, 'bericht.py'], check=True, timeout=30)
        self.assertEqual(open('bericht.txt').read().strip(), 'Summe: 20.00')

