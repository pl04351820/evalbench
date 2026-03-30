import unittest
from scorers.llmrater import LLMRater

class TestLLMRater(unittest.TestCase):
    def test_take_n_uniques_with_document_model(self):
        # A typical Document model returned result containing nested lists of dictionaries
        golden = [
            {"authors": [{"name": "Alice"}, {"name": "Bob"}]}
        ]
        
        # In current implementation, make_hashable(list) returns tuple(list).
        # Inside the list are dicts, which are unhashable.
        # frozenset will fail when it tries to hash the tuple containing dicts.
        try:
            result = LLMRater.take_n_uniques(golden, 50)
            self.assertEqual(len(result), 1)
        except TypeError as e:
            self.fail(f"take_n_uniques raised TypeError unexpectedly: {e}")

if __name__ == '__main__':
    unittest.main()
