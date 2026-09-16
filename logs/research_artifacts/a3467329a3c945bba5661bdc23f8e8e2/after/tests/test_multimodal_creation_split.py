"""AI-synthetic fixtures test two-axis exports; no real study/model runs."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from multimodal_creation_split import prepare
from multimodal_meme_dataset import ROOT, ingest, snapshot
from research_log import ResearchLog

T0, T1, T2, T3, T4 = [f"2026-01-0{i}T00:00:00+00:00" for i in range(1, 6)]
POLICY = {"default": {"retrieval": True, "training": True}, "declared_by": "AI synthetic fixture author",
          "assets": {"permission-denied": {"training": False}}}


def fixture(base, *, exposed_evaluation=None):
    def asset(identity, *, purpose="source_evidence", status="available", group=None, parents=None, available=T0):
        path = base / (identity + ".txt")
        path.write_text("AI SYNTHETIC ASSET " + identity, encoding="utf-8")
        return {"asset_id": identity, "source_group_id": group or identity, "parent_asset_ids": parents or [],
                "source_url": None, "local_path": str(path.relative_to(ROOT)), "mime": "text/plain", "modality": "text",
                "license_or_use_basis": "Local AI synthetic test fixture only", "status": status, "purpose": purpose,
                "available_at": available, "provenance": "ai_synthetic"}
    train = {"meme_id": "dev-source", "family_id": "development-family", "version": 1, "available_at": T0,
             "published_at": T0, "split": "train", "provenance": "ai_synthetic", "assets": [asset("development-source")]}
    locked = {"meme_id": "locked-source", "family_id": "locked-family", "version": 1, "available_at": T0,
              "published_at": T0, "split": "test", "provenance": "ai_synthetic",
              "assets": [asset("historical-source"), asset("gold-image", purpose="evaluation_only", group="historical-source", parents=["historical-source"]),
                         asset("gold-ocr", group="historical-source", parents=["gold-image"]),
                         asset("restricted-source", status="restricted"), asset("permission-denied")],
              "annotations": [{"annotation_id": "gold-annotation", "author": {"id": "synthetic-author", "type": "ai"},
                               "role": "evaluation_gold", "available_at": T1, "content": "SECRET_GOLD_ANNOTATION", "provenance": "ai_synthetic"}],
              "knowledge": [
                  {"knowledge_id": "meaning", "version": 1, "available_at": T1, "text": "HISTORICAL_MEANING",
                   "provenance": "ai_synthetic", "purpose": "source_context", "source_asset_ids": ["historical-source"]},
                  {"knowledge_id": "gold-launder", "version": 1, "available_at": T1, "text": "SECRET_GOLD_DERIVED",
                   "provenance": "ai_synthetic", "purpose": "source_context", "source_asset_ids": ["historical-source"],
                   "derived_from_annotation_ids": ["gold-annotation"]},
                  {"knowledge_id": "gold-ocr-meaning", "version": 1, "available_at": T1, "text": "SECRET_GOLD_OCR",
                   "provenance": "ai_synthetic", "purpose": "source_context", "source_asset_ids": ["gold-ocr"]}],
              "creation_examples": [
                  {"example_id": "past-support", "brief": "HISTORICAL_TRAINING_BRIEF", "target_text": "HISTORICAL_SUPPORT_TARGET",
                   "available_at": T1, "purpose": "training_supervision", "split": "train", "provenance": "ai_synthetic"},
                  {"example_id": "forbidden-target", "brief": "FORBIDDEN_TARGET_BRIEF", "target_asset_ids": ["permission-denied"],
                   "available_at": T1, "purpose": "training_supervision", "provenance": "ai_synthetic"},
                  {"example_id": "locked-early-brief", "brief": "EVALUATION_BRIEF_EARLY", "target_text": "SECRET_EVALUATION_TARGET_EARLY",
                   "available_at": T1, "purpose": "evaluation_only", "provenance": "ai_synthetic"}]}
    later = copy.deepcopy(locked)
    later.update(version=2, available_at=T2)
    later["assets"][0] = asset("later-source", available=T2)
    later["knowledge"][0].update(version=2, available_at=T2, text="LATER_VERSION_MEANING", source_asset_ids=["later-source"])
    for knowledge in later["knowledge"][1:]:
        knowledge["source_asset_ids"] = ["later-source"] if knowledge["knowledge_id"] == "gold-launder" else ["gold-ocr"]
    later["creation_examples"][0]["target_text"] = "LATER_VERSION_SUPPORT_TARGET"
    later["creation_examples"].append({"example_id": "future-evaluation", "brief": "EVALUATION_BRIEF_FUTURE",
                                       "target_text": "SECRET_EVALUATION_TARGET_FUTURE", "available_at": T3,
                                       "purpose": "evaluation_only", "provenance": "ai_synthetic"})
    if exposed_evaluation == "same_id":
        later["creation_examples"][0]["purpose"] = "evaluation_only"
    elif exposed_evaluation == "same_content":
        later["creation_examples"].append({"example_id": "renamed-old-support", "brief": "HISTORICAL_TRAINING_BRIEF",
                                           "target_text": "HISTORICAL_SUPPORT_TARGET", "available_at": T2,
                                           "purpose": "evaluation_only", "provenance": "ai_synthetic"})
    bridge = copy.deepcopy(later)
    bridge.update(version=3, available_at=T4)
    bridge["relationships"] = [{"relationship_id": "new-bridge", "target_meme_id": "dev-source", "relation": "repost_of",
                                "available_at": T4, "provenance": "ai_synthetic"}]
    input_path = base / "input.jsonl"
    input_path.write_text("\n".join(json.dumps(row) for row in [train, locked, later, bridge]) + "\n", encoding="utf-8")
    db = base / "research.sqlite3"
    ingest(input_path, base / "dataset", db)
    snapshot(base / "dataset", T0, base / "partition", db)
    return base / "dataset", base / "partition/manifest.json", db


class TwoAxisCreationTests(unittest.TestCase):
    def test_relabelled_or_renamed_support_cannot_become_unseen_evaluation(self):
        for collision in ("same_id", "same_content"):
            with self.subTest(collision=collision), tempfile.TemporaryDirectory(prefix="two-axis-exposure-", dir=ROOT / "data") as directory:
                base = Path(directory)
                dataset, partition, db = fixture(base, exposed_evaluation=collision)
                with self.assertRaisesRegex(ValueError, "already prepared as adaptation"):
                    prepare(dataset, partition, base / "blocked", mode="locked_family_past_support", source_cutoff=T1,
                            evaluation_at=T3, locked_family_ids=["locked-family"], support_permissions=POLICY, db=db)
                failure = json.loads((base / "blocked/failure.json").read_text(encoding="utf-8"))
                expected = "same_example_identity" if collision == "same_id" else "same_exact_brief_and_target"
                self.assertIn(expected, {c["reason"] for c in failure["evaluation_exposure_conflicts"]})
                self.assertFalse((base / "blocked/shared_supervision.jsonl").exists())
                self.assertFalse((base / "blocked/evaluation_briefs.jsonl").exists())
                self.assertEqual(ResearchLog(db).list()[-1]["status"], "failed")

    def test_historical_test_family_support_is_equal_and_distinct_from_evaluation(self):
        with tempfile.TemporaryDirectory(prefix="two-axis-", dir=ROOT / "data") as directory:
            base = Path(directory)
            dataset, partition, db = fixture(base)
            result = prepare(dataset, partition, base / "adapt", mode="locked_family_past_support", source_cutoff=T1,
                             evaluation_at=T3, locked_family_ids=["locked-family"], support_permissions=POLICY, db=db)
            context = (base / "adapt/shared_context.jsonl").read_text(encoding="utf-8")
            supervision = (base / "adapt/shared_supervision.jsonl").read_text(encoding="utf-8")
            self.assertEqual(result["arms"]["CR"], result["arms"]["CF"])
            self.assertIn("HISTORICAL_MEANING", context)
            self.assertIn('"asset_id": "historical-source"', context)
            self.assertIn("HISTORICAL_SUPPORT_TARGET", supervision)
            for forbidden in ("LATER_VERSION_", "EVALUATION_BRIEF", "SECRET_", "FORBIDDEN_TARGET_BRIEF", "restricted-source", "permission-denied", "gold-ocr"):
                self.assertNotIn(forbidden, context + supervision)
            support = json.loads(supervision)
            self.assertEqual(support["family_partition"], "test")
            self.assertEqual(support["declared_example_split"], "train")
            self.assertEqual(support["record_version"], 1)
            self.assertEqual(support["temporal_role"], "adaptation_support")
            evaluation = (base / "adapt/evaluation_briefs.jsonl").read_text(encoding="utf-8")
            self.assertIn("EVALUATION_BRIEF_FUTURE", evaluation)
            self.assertNotIn("SECRET_", evaluation)
            self.assertIn("SECRET_EVALUATION_TARGET", (base / "adapt/protected_gold.jsonl").read_text(encoding="utf-8"))
            self.assertFalse(result["independent_gold_verified"])
            self.assertFalse(result["actual_research_run_performed"])
            self.assertEqual(result["provenance"], "ai_synthetic")
            baseline = prepare(dataset, partition, base / "train_only", mode="train_only", source_cutoff=T1,
                               evaluation_at=T3, locked_family_ids=["locked-family"], support_permissions=POLICY, db=db)
            self.assertEqual(baseline["files"]["shared_supervision.jsonl"]["count"], 0)
            self.assertEqual(baseline["files"]["evaluation_briefs.jsonl"]["sha256"], result["files"]["evaluation_briefs.jsonl"]["sha256"])

    def test_unknown_permissions_withhold_support_and_invalid_mode_is_logged_retryable(self):
        with tempfile.TemporaryDirectory(prefix="two-axis-policy-", dir=ROOT / "data") as directory:
            base = Path(directory)
            dataset, partition, db = fixture(base)
            result = prepare(dataset, partition, base / "unknown_policy", mode="locked_family_past_support", source_cutoff=T1,
                             evaluation_at=T3, locked_family_ids=["locked-family"], db=db)
            self.assertEqual(result["files"]["shared_context.jsonl"]["count"], 0)
            self.assertEqual(result["files"]["shared_supervision.jsonl"]["count"], 0)
            arguments = dict(mode=None, source_cutoff=T1, evaluation_at=T3, locked_family_ids=["locked-family"], db=db)
            with self.assertRaisesRegex(ValueError, "mode must be explicitly"):
                prepare(dataset, partition, base / "bad_mode", **arguments)
            failed = ResearchLog(db).list()[-1]
            self.assertEqual(failed["status"], "failed")
            self.assertTrue((base / "bad_mode/failure.json").exists())
            with self.assertRaisesRegex(ValueError, "mode must be explicitly"):
                prepare(dataset, partition, base / "retry", retry_of=failed["run_id"], retry_reason="Synthetic retry behavior check", **arguments)
            self.assertEqual(ResearchLog(db).list()[-1]["retry_of"], failed["run_id"])

    def test_new_cross_partition_link_blocks_adaptation_and_evaluation_exports(self):
        with tempfile.TemporaryDirectory(prefix="two-axis-conflict-", dir=ROOT / "data") as directory:
            base = Path(directory)
            dataset, partition, db = fixture(base)
            prepare(dataset, partition, base / "first", mode="locked_family_past_support", source_cutoff=T1,
                    evaluation_at=T3, locked_family_ids=["locked-family"], support_permissions=POLICY, db=db)
            with self.assertRaisesRegex(ValueError, "split conflict"):
                prepare(dataset, base / "first/manifest.json", base / "conflict", mode="locked_family_past_support", source_cutoff=T3,
                        evaluation_at=T4, locked_family_ids=["locked-family"], support_permissions=POLICY, db=db)
            failed = json.loads((base / "conflict/failure.json").read_text(encoding="utf-8"))
            self.assertEqual(set(failed["conflict_meme_ids"]), {"dev-source", "locked-source"})
            self.assertEqual(set(failed["requested_or_fixed_splits"]), {"train", "test"})
            self.assertFalse((base / "conflict/shared_context.jsonl").exists())
            self.assertFalse((base / "conflict/evaluation_briefs.jsonl").exists())
            self.assertEqual(ResearchLog(db).list()[-1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
