import json, os, subprocess, sys, unittest


class T(unittest.TestCase):
    def test_stichpunkte(self):
        z = [l for l in open('zusammenfassung.md', encoding='utf-8') if l.startswith('- ')]
        self.assertTrue(3 <= len(z) <= 5)
        text = ' '.join(z)
        self.assertIn('14', text)

