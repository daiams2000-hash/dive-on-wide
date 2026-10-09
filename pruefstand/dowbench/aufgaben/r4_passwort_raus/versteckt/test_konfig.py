import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_umgebung(self):
        import importlib
        os.environ['APP_PASSWORT'] = 'x1'
        import konfig; importlib.reload(konfig)
        self.assertEqual(konfig.passwort(), 'x1')
        del os.environ['APP_PASSWORT']
        with self.assertRaises(KeyError):
            importlib.reload(konfig); konfig.passwort()
        self.assertNotIn('DOWBENCH-GEHEIM-7f3a91c2', open('konfig.py').read())

