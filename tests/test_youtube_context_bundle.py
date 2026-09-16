"""Synthetic pairing and time gates; no claim about actual model understanding."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import youtube_context_bundle as yb
from research_log import DuplicateRunError, ResearchLog

NOW = "2026-09-15T03:00:00+00:00"


def fixture():
    observations = [{"observation_id": "o" + str(i), "platform": "youtube", "source_id": "v" + str(i),
        "video_id": "v" + str(i), "channel_id": "c" + str(i), "text": "오늘도 연구실 문이 열렸다",
        "context": {"title": "오늘도 연구실 문이 열렸다", "description": "합성 설명"}, "modality": "video_metadata",
        "posted_at": "2026-09-14T00:00:00Z", "collected_at": "2026-09-14T01:00:00Z",
        "available_at": "2026-09-14T01:00:00Z", "provenance": "ai_synthetic", "source_url": None} for i in (1, 2)]
    candidates = [{"candidate_id": "c1", "method": "D0", "rank": 1, "verified_meme": False,
        "meaning": None, "origin": None, "human_review_minutes": 0, "linked_at": "2026-09-14T02:00:00Z",
        "evidence_cutoff": "2026-09-14T01:01:00Z", "evidence_observation_ids": ["o1", "o2"],
        "surface_forms": ["오늘도 연구실 문이 열렸다"], "possible_variant_links": []}]
    summary = {"method": "D0", "cutoff": "2026-09-14T01:01:00Z", "generated_at": "2026-09-14T02:00:00Z"}
    return candidates, observations, summary


class BundleTest(unittest.TestCase):
    @patch.object(yb, "_now", return_value=NOW)
    def test_u_pair_preserves_exact_raw_and_exposes_unknown_semantics(self, _):
        inputs = fixture()
        before = copy.deepcopy(inputs)
        result = yb.build_bundles(*inputs, candidate_ids=["c1"])
        self.assertEqual(inputs, before)
        u0, u1 = result["bundles"]
        self.assertEqual(u0["condition"], "D0U0")
        self.assertEqual(u1["condition"], "D0U1")
        self.assertEqual(u0["support_bundle"]["observations"], u1["support_bundle"]["observations"])
        self.assertEqual(u0["raw_bundle_sha256"], u1["raw_bundle_sha256"])
        self.assertEqual(u0["raw_bundle_sha256"], yb.digest(u0["support_bundle"]["observations"]))
        structure = u1["support_bundle"]["automatic_structure"]
        self.assertIsNone(structure["meaning"])
        self.assertIsNone(structure["intent"])
        self.assertIsNone(structure["editable_parts"])
        self.assertEqual(structure["available_at"], NOW)
        self.assertNotEqual(structure["available_at"], structure["source_discovery_evidence_cutoff"])
        self.assertEqual(structure["counts_within_matched_raw_bundle"]["source_documents"], 2)
        self.assertEqual(result["manifest"]["sample_selection"], "posthoc_candidate_id_selection")
        self.assertFalse(result["manifest"]["model_weights_updated"])

    @patch.object(yb, "_now", return_value=NOW)
    def test_past_evaluation_and_future_links_cannot_receive_new_structure(self, _):
        with self.assertRaisesRegex(ValueError, "New automatic structure"):
            yb.build_bundles(*fixture(), evaluation_t="2026-09-14T03:00:00Z")
        candidates, evidence, summary = fixture()
        candidates[0]["linked_at"] = "2026-09-16T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "Candidate links"):
            yb.build_bundles(candidates, evidence, summary)
        candidates, evidence, summary = fixture()
        candidates[0]["possible_variant_links"] = [{"left": "오늘도 연구실 문이 열렸다", "right": "오늘도 연구실 문이 열렸다",
                                                    "linked_at": "2026-09-16T00:00:00Z"}]
        with self.assertRaisesRegex(ValueError, "Variant link"):
            yb.build_bundles(candidates, evidence, summary)

    @patch.object(yb, "_now", return_value=NOW)
    def test_missing_evidence_gold_context_and_mixed_provenance_fail(self, _):
        candidates, evidence, summary = fixture()
        with self.assertRaisesRegex(ValueError, "evidence IDs"):
            yb.build_bundles(candidates, evidence[:1], summary)
        candidates, evidence, summary = fixture()
        evidence[0]["context"]["gold_interpretation"] = "정답을 넣으면 안 됨"
        with self.assertRaisesRegex(ValueError, "no gold"):
            yb.build_bundles(candidates, evidence, summary)
        candidates, evidence, summary = fixture()
        evidence[1]["provenance"] = "actual_collected"
        with self.assertRaisesRegex(ValueError, "Do not mix"):
            yb.build_bundles(candidates, evidence, summary)

    @patch.object(yb, "_now", return_value=NOW)
    def test_log_is_immutable_and_default_now_does_not_bypass_duplicate_block(self, _):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            candidates, evidence, summary = fixture()
            for name, rows in (("candidates", candidates), ("evidence", evidence)):
                (source / (name + ".jsonl")).write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            (source / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            manifest = yb.run(source, root / "out", db=root / "log.sqlite3")
            entry = ResearchLog(root / "log.sqlite3").status(manifest["run_id"])
            self.assertEqual(entry["status"], "succeeded")
            self.assertEqual(entry["finish"]["actual_cost"]["api_units"], 0)
            self.assertEqual(len(entry["finish"]["artifacts"]), 5)
            with patch.object(yb, "_now", return_value="2026-09-15T04:00:00Z"):
                with self.assertRaises(DuplicateRunError):
                    yb.run(source, root / "out2", db=root / "log.sqlite3")
            self.assertFalse((root / "out2").exists())


if __name__ == "__main__":
    unittest.main()
