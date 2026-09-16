"""Synthetic local tests; no real model is loaded and no network is used."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import meme_local_understanding as ml
from research_log import DuplicateRunError, ResearchLog


class FakeBackend:
    def __init__(self, *args, **kwargs):
        pass

    def render(self, messages):
        return json.dumps(messages, ensure_ascii=False)

    def count_tokens(self, prompt):
        return len(prompt) // 4

    def generate(self, prompt, max_new_tokens):
        return {"text": json.dumps({"meaning": "TEST_ONLY_interpretation", "usage": "TEST_ONLY_usage",
            "evidence_ids": ["TEST_ONLY_1"], "uncertainty": "Synthetic transport only"}),
            "input_tokens": self.count_tokens(prompt), "output_tokens": 40,
            "generation_seconds": 0, "output_token_limit_reached": False}


class LocalUnderstandingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TEST_ONLY_local_understanding_")
        self.base = Path(self.tmp.name)
        self.model = self.base / "model"
        self.model.mkdir()
        for name, value in (("config.json", {"torch_dtype": "bfloat16", "num_hidden_layers": 1, "hidden_size": 8}),
                            ("tokenizer_config.json", {}), ("tokenizer.json", {})):
            (self.model / name).write_text(json.dumps(value), encoding="utf8")
        (self.model / "model.safetensors").write_bytes(b"TEST_ONLY_FAKE_NOT_A_MODEL")
        self.data = {"families": [{"family_id": "TEST_ONLY_FAMILY", "label": "TEST_ONLY_NOT_IN_U0",
            "examples": [{"observation_id": "TEST_ONLY_1", "title": "TEST_ONLY_source_one",
                "description": "TEST_ONLY_description", "video_id": "TEST_ONLY_VIDEO",
                "source_url": "https://example.invalid/test-only", "available_at": "2020-01-01T00:00:00+00:00"}],
            "knowledge": {"provenance": "automatic_rule_derived_interpretation",
                "available_at": "2020-01-02T00:00:00+00:00", "meaning": "TEST_ONLY_AUTO_STRUCTURE",
                "evidence_ids": ["TEST_ONLY_1"], "unknowns": ["TEST_ONLY"]}}]}
        self.result = self.base / "result.json"
        self.db = self.base / "log.sqlite3"
        self.write_data()

    def tearDown(self):
        self.tmp.cleanup()

    def write_data(self):
        self.result.write_text(json.dumps(self.data), encoding="utf8")

    def execute(self, suffix="run", **kwargs):
        return ml.run(result_path=self.result, output_dir=self.base / suffix, model_path=self.model,
                      db=self.db, backend_factory=FakeBackend, test_only=True, **kwargs)

    def test_u0_u1_have_identical_sources_and_only_u1_gets_automatic_structure(self):
        result = self.execute()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["local_model_calls"], 2)
        u0 = json.loads((self.base / "run/family_01_U0_messages.json").read_text(encoding="utf8"))
        u1 = json.loads((self.base / "run/family_01_U1_messages.json").read_text(encoding="utf8"))
        a, b = json.loads(u0[1]["content"]), json.loads(u1[1]["content"])
        self.assertEqual(a["source_excerpts"], b["source_excerpts"])
        self.assertNotIn("automatic_structure", a)
        self.assertEqual(b["automatic_structure"]["meaning"], "TEST_ONLY_AUTO_STRUCTURE")
        self.assertNotIn("TEST_ONLY_NOT_IN_U0", u0[1]["content"])
        self.assertIsNone(result["semantic_accuracy"])

    def test_unknown_provenance_or_human_correction_cannot_enter_u1(self):
        for change in ({"provenance": "ai_manual_review"}, {"human_reviewed": True}, {"gold": True}):
            family = copy.deepcopy(self.data["families"][0])
            family["knowledge"].update(change)
            knowledge, reason = ml._automatic_knowledge(family, {}, "2026-01-01T00:00:00+00:00")
            self.assertIsNone(knowledge)
            self.assertIsNotNone(reason)

    def test_unavailable_sources_and_future_knowledge_are_excluded(self):
        family = copy.deepcopy(self.data["families"][0])
        family["examples"].append({"observation_id": "TEST_ONLY_FUTURE", "title": "Future",
                                   "available_at": "2099-01-01T00:00:00+00:00"})
        rows, exclusions = ml.select_examples(family, "2026-01-01T00:00:00+00:00")
        self.assertEqual(len(rows), 1)
        self.assertEqual(exclusions[0]["reason"], "not_available_at_interpretation")
        family["knowledge"]["available_at"] = "2099-01-01T00:00:00+00:00"
        self.assertEqual(ml._automatic_knowledge(family, {}, "2026-01-01T00:00:00+00:00")[1],
                         "knowledge_not_available_at_interpretation")

    def test_foreign_knowledge_evidence_prevents_u1(self):
        self.data["families"][0]["knowledge"]["evidence_ids"] = ["TEST_ONLY_NOT_SUPPLIED"]
        self.write_data()
        result = self.execute()
        self.assertEqual(result["local_model_calls"], 1)
        self.assertIn("excluded_or_missing", result["families"][0]["knowledge_exclusion_reason"])

    def test_low_memory_is_logged_without_constructing_model(self):
        with patch.object(ml, "memory_preflight", return_value={"status": "blocked_local_resources",
                "available_bytes": 100, "estimated_required_available_bytes": 100000}), patch.object(ml, "LocalBackend") as backend, \
                patch.object(ml, "CachedPromptEncoder", FakeBackend):
            result = ml.run(self.result, output_dir=self.base / "blocked", model_path=self.model, db=self.db)
            backend.assert_not_called()
        self.assertEqual(result["status"], "blocked_local_resources")
        self.assertEqual(result["local_model_calls"], 0)
        self.assertEqual(ResearchLog(self.db).status(result["run_id"])["status"], "failed")
        self.assertTrue((self.base / "blocked/manifest.json").is_file())
        self.assertTrue((self.base / "blocked/family_01_U0_prompt.txt").is_file())
        self.assertTrue((self.base / "blocked/family_01_U1_prompt.txt").is_file())
        self.assertFalse((self.base / "blocked/family_01_U0_response.json").exists())

    def test_duplicate_change_output_path_is_blocked(self):
        self.execute()
        with self.assertRaises(DuplicateRunError):
            self.execute("other")
        self.assertFalse((self.base / "other").exists())

    def test_model_failure_preserves_failed_manifest(self):
        class BrokenBackend(FakeBackend):
            def generate(self, *args):
                raise RuntimeError("TEST_ONLY_expected_failure")
        result = ml.run(self.result, output_dir=self.base / "failed", model_path=self.model,
            db=self.db, backend_factory=BrokenBackend, test_only=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["issues"][0]["phase"], "inference_U0")
        self.assertTrue((self.base / "failed/family_01_U0_prompt.txt").is_file())


if __name__ == "__main__":
    unittest.main()
