import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_ordner(self):
        self.assertEqual(sorted(os.listdir('build')), ['app.bin', 'notiz.txt'])
        self.assertEqual(sorted(os.listdir('papierkorb')), ['cache_%d.tmp' % i for i in range(4)])

