import unittest
from flach import flach


class T(unittest.TestCase):
    def test_dict(self):
        self.assertEqual(flach({"a": {"b": 1}}), {"a.b": 1})
