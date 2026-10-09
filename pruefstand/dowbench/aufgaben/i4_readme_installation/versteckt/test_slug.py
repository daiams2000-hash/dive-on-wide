import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_slug(self):
        from text import slug
        self.assertEqual(slug('Größe & Maß!'), 'groesse-mass')
        self.assertEqual(slug('  Über  uns  '), 'ueber-uns')
        self.assertEqual(slug('A--B'), 'a-b')

