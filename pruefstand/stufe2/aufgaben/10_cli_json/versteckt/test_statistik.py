import io
import json
import unittest
from contextlib import redirect_stdout
import statistik


def lauf(args):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = statistik.main(args)
    return rc, out.getvalue()


class T(unittest.TestCase):
    def test_text(self):
        rc, o = lauf(["1", "2", "3"])
        self.assertEqual(rc, 0)
        for teil in ["Anzahl: 3", "Summe: 6.0", "Mittel: 2.0", "Min: 1.0", "Max: 3.0"]:
            self.assertIn(teil, o)

    def test_json(self):
        rc, o = lauf(["--json", "4", "1", "7"])
        self.assertEqual(rc, 0)
        d = json.loads(o)
        self.assertEqual(d, {"anzahl": 3, "summe": 12.0, "mittel": 4.0, "min": 1.0, "max": 7.0})

    def test_json_hinten(self):
        rc, o = lauf(["2", "--json"])
        self.assertEqual(json.loads(o)["max"], 2.0)
