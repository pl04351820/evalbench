import unittest
from scorers.setmatcher import SetMatcher

class TestSetMatcherNoSQL(unittest.TestCase):
    def test_sql_flat_match(self):
        matcher = SetMatcher({})
        golden = [{"a": 1, "b": 2}]
        generated = [{"a": 1, "b": 2}]
        score, err = matcher.compare(None, None, None, golden, None, None, None, generated, None, None)
        self.assertEqual(score, 100)
        self.assertIsNone(err)

    def test_sql_nested_unhashable(self):
        matcher = SetMatcher({})
        golden = [{"a": {"x": 1}}]
        generated = [{"a": {"x": 1}}]
        score, err = matcher.compare(None, None, None, golden, None, None, None, generated, None, None)
        self.assertEqual(score, 0)
        self.assertIn("unhashable type: 'dict'", err)

    def test_nosql_nested_match(self):
        matcher = SetMatcher({"firestore_data_model": True})
        golden = [{"a": {"x": 1}}]
        generated = [{"a": {"x": 1}}]
        score, err = matcher.compare(None, None, None, golden, None, None, None, generated, None, None)
        self.assertEqual(score, 100)
        self.assertIsNone(err)

    def test_nosql_multiset_duplication(self):
        matcher = SetMatcher({"firestore_data_model": True})
        golden = [{"a": 1}, {"a": 1}]
        generated = [{"a": 1}]
        score, err = matcher.compare(None, None, None, golden, None, None, None, generated, None, None)
        self.assertEqual(score, 0)
        self.assertIsNone(err)

        golden2 = [{"a": 1}, {"a": 1}]
        generated2 = [{"a": 1}, {"a": 1}]
        score2, err2 = matcher.compare(None, None, None, golden2, None, None, None, generated2, None, None)
        self.assertEqual(score2, 100)

    def test_nosql_list_preserve_order(self):
        matcher = SetMatcher({"firestore_data_model": True})
        golden = [{"a": [1, 2]}]
        generated = [{"a": [2, 1]}]
        score, err = matcher.compare(None, None, None, golden, None, None, None, generated, None, None)
        self.assertEqual(score, 0)
        self.assertIsNone(err)

        golden2 = [{"a": [1, 2]}]
        generated2 = [{"a": [1, 2]}]
        score2, err2 = matcher.compare(None, None, None, golden2, None, None, None, generated2, None, None)
        self.assertEqual(score2, 100)

if __name__ == '__main__':
    unittest.main()
