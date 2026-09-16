"""CLI failures must not appear as successful meme or model completion."""
import contextlib
import io
from pathlib import Path
import sys
import types
import unittest
import unicodedata
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import find_memes


class FinderCompletionTests(unittest.TestCase):
    def test_decomposed_korean_source_preserves_observed_query_link(self):
        source = unicodedata.normalize("NFD", "충분히 정교한  안내는 암호와 구분할 수 없다")
        self.assertTrue(find_memes.query_occurs_in_source("충분히 정교한", source))
        self.assertFalse(find_memes.query_occurs_in_source("관찰하지 않은 표현", source))

    def invoke(self, result, args=None):
        with patch.object(find_memes, "run", return_value=(result, Path("synthetic-output"))):
            with contextlib.redirect_stdout(io.StringIO()):
                return find_memes.main(args or [])

    def test_only_unresolved_candidates_do_not_exit_success(self):
        result = {"families": [{"status": "needs_evidence"}], "failures": [], "counts": {}}
        self.assertEqual(self.invoke(result), 2)

    def test_requested_model_resource_block_is_visible_as_failure(self):
        result = {"families": [{"status": "supported_textual_reuse"}], "failures": [], "counts": {}}
        fake = types.SimpleNamespace(run=lambda **kwargs: {"status": "blocked_local_resources", "local_model_calls": 0})
        with patch.dict(sys.modules, {"meme_local_understanding": fake}):
            self.assertEqual(self.invoke(result, ["--local-model", "synthetic-model"]), 2)


if __name__ == "__main__":
    unittest.main()
