import io
import unittest
from contextlib import redirect_stdout
import statistik


class T(unittest.TestCase):
    def test_text(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(statistik.main(["1", "2", "3"]), 0)
        self.assertIn("Summe: 6.0", out.getvalue())
