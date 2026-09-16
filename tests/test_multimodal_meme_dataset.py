"""Synthetic fixtures check dataset isolation; they are not study observations."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from multimodal_meme_dataset import ROOT, ingest, snapshot, validate_records
from research_log import ResearchLog, hash_file

T1 = "2026-01-01T00:00:00+00:00"
T2 = "2026-01-02T00:00:00+00:00"
T3 = "2026-01-03T00:00:00+00:00"
T4 = "2026-01-04T00:00:00+00:00"


def record(mid, path, split, *, family=None, available=T1):
    return {"meme_id": mid, "family_id": family or mid, "version": 1, "available_at": available,
            "published_at": T1, "provenance": "ai_synthetic", "split": split,
            "assets": [{"asset_id": mid + "-asset", "source_group_id": mid + "-source", "source_url": None,
                        "local_path": str(path.relative_to(ROOT)), "mime": "text/plain", "modality": "text",
                        "license_or_use_basis": "AI synthetic test fixture authored locally; not collected research data",
                        "status": "available", "provenance": "ai_synthetic", "purpose": "source_evidence"}]}


class MultimodalMemeDatasetTests(unittest.TestCase):
    def test_time_version_and_gold_isolation_preserve_equal_supervision(self):
        with tempfile.TemporaryDirectory(prefix="dataset-fixture-", dir=ROOT / "data") as directory:
            base = Path(directory)
            source_a, source_b = base / "a.txt", base / "b.txt"
            source_a.write_text("SYNTHETIC SOURCE A", encoding="utf-8")
            source_b.write_text("SYNTHETIC SOURCE B", encoding="utf-8")
            original_hash = hash_file(source_a)
            a, b = record("a", source_a, "train"), record("b", source_b, "test")
            a["annotations"] = [{"annotation_id": "gold-annotation", "author": {"id": "fixture-author", "type": "ai"},
                                 "role": "evaluation_gold", "available_at": T2, "content": "SECRET_GOLD_ANNOTATION",
                                 "provenance": "ai_synthetic", "human_minutes": 0}]
            a["knowledge"] = [{"knowledge_id": "source-meaning", "version": 1, "available_at": T2,
                               "text": "EARLY_CONTEXT", "provenance": "ai_synthetic", "purpose": "source_context", "source_asset_ids": ["a-asset"]},
                              {"knowledge_id": "gold", "version": 1, "available_at": T2, "text": "SECRET_GOLD_KNOWLEDGE",
                               "provenance": "ai_synthetic", "purpose": "evaluation_gold", "source_asset_ids": ["a-asset"]},
                              {"knowledge_id": "source-meaning", "version": 2, "available_at": T4,
                               "text": "FUTURE_CONTEXT", "provenance": "ai_synthetic", "purpose": "source_context", "source_asset_ids": ["a-asset"]}]
            a["relationships"] = [{"relationship_id": "future-link", "target_meme_id": "b", "relation": "repost_of",
                                   "available_at": T4, "provenance": "ai_synthetic", "basis_asset_ids": ["a-asset"]}]
            a["creation_examples"] = [{"example_id": "train-example", "brief": "SYNTHETIC_TRAIN_BRIEF", "target_text": "SHARED_TRAIN_TARGET",
                                       "available_at": T2, "purpose": "training_supervision", "split": "train", "provenance": "ai_synthetic"}]
            b["creation_examples"] = [{"example_id": "test-example", "brief": "PROTECTED_TEST_BRIEF", "target_text": "SECRET_TEST_TARGET",
                                       "available_at": T2, "purpose": "evaluation_only", "split": "test", "provenance": "ai_synthetic"}]
            revised_a = copy.deepcopy(a)
            revised_a.update(version=2, available_at=T4, title="FUTURE_RECORD_TITLE")
            input_path = base / "input.jsonl"
            input_path.write_text("\n".join(json.dumps(r) for r in [a, revised_a, b]) + "\n", encoding="utf-8")
            db = base / "research.sqlite3"
            ingested = ingest(input_path, base / "ingested", db)
            self.assertEqual(hash_file(source_a), original_hash)
            self.assertEqual(ingested["provenance"], "ai_synthetic")
            result = snapshot(base / "ingested", T3, base / "snapshot", db)
            out = base / "snapshot"
            self.assertEqual(result["record_count"], 2)
            self.assertEqual((out / "rag_examples.jsonl").read_bytes(), (out / "finetune_examples.jsonl").read_bytes())
            shared = (out / "shared_training_examples.jsonl").read_text(encoding="utf-8")
            context = (out / "rag_context.jsonl").read_text(encoding="utf-8")
            self.assertIn("SHARED_TRAIN_TARGET", shared)
            self.assertIn("EARLY_CONTEXT", context)
            for forbidden in ["SECRET_", "PROTECTED_TEST_BRIEF", "FUTURE_"]:
                self.assertNotIn(forbidden, shared + context)
            self.assertIn("SECRET_TEST_TARGET", (out / "evaluation_targets.protected.jsonl").read_text(encoding="utf-8"))
            self.assertNotIn("SECRET_TEST_TARGET", (out / "evaluation_inputs.jsonl").read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in (out / "records.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([r["version"] for r in rows if r["meme_id"] == "a"], [1])
            self.assertEqual(rows[0]["relationships"], [])

    def test_group_leakage_and_hash_tampering_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="dataset-groups-", dir=ROOT / "data") as directory:
            base = Path(directory)
            source_a, source_b = base / "a.txt", base / "b.txt"
            source_a.write_text("SYNTHETIC PARENT", encoding="utf-8")
            source_b.write_text("SYNTHETIC OCR DERIVATIVE", encoding="utf-8")
            a, b = record("a", source_a, "train"), record("b", source_b, "test")
            b["assets"][0]["source_group_id"] = a["assets"][0]["source_group_id"]
            input_path = base / "input.jsonl"
            input_path.write_text("\n".join(json.dumps(r) for r in [a, b]) + "\n", encoding="utf-8")
            db = base / "research.sqlite3"
            ingest(input_path, base / "ingested", db)
            with self.assertRaisesRegex(ValueError, "split conflict"):
                snapshot(base / "ingested", T3, base / "snapshot", db)
            a["assets"][0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                validate_records([a])
            bad = base / "bad.jsonl"
            bad.write_text(json.dumps(a) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                ingest(bad, base / "bad_attempt", db)
            previous = ResearchLog(db).list()[-1]
            self.assertEqual(previous["status"], "failed")
            self.assertIn("SHA-256 mismatch", (base / "bad_attempt/failure.json").read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                ingest(bad, base / "bad_retry", db, retry_of=previous["run_id"], retry_reason="Exercise explicit preserved-failure retry in a synthetic fixture")
            retry = ResearchLog(db).list()[-1]
            self.assertEqual(retry["retry_of"], previous["run_id"])
            source_a.write_text("CHANGED SYNTHETIC PARENT", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                ingest(bad, base / "changed_asset_attempt", db)
            changed = ResearchLog(db).list()[-1]
            self.assertNotEqual(changed["fingerprint"], previous["fingerprint"])
            self.assertTrue(any(s["id"].startswith("asset:") for s in changed["spec"]["data_snapshots"]))

    def test_fixed_partition_survives_revision_and_blocks_new_bridge(self):
        with tempfile.TemporaryDirectory(prefix="dataset-fixed-", dir=ROOT / "data") as directory:
            base = Path(directory)
            paths = []
            for index in range(3):
                path = base / f"source{index}.txt"
                path.write_text(f"SYNTHETIC DISTINCT SOURCE {index}", encoding="utf-8")
                paths.append(path)
            z = record("z", paths[0], "train")
            b = record("b", paths[1], "test")
            revised = copy.deepcopy(z)
            revised.update(version=2, available_at=T2, split=None)
            revised["relationships"] = [{"relationship_id": "new-bridge", "target_meme_id": "b", "relation": "repost_of",
                                          "available_at": T4, "provenance": "ai_synthetic"}]
            newcomer = record("a-new", paths[2], None, family="z", available=T2)
            source = base / "input.jsonl"
            source.write_text("\n".join(json.dumps(r) for r in [z, b, revised, newcomer]) + "\n", encoding="utf-8")
            db = base / "research.sqlite3"
            ingest(source, base / "dataset", db)
            config = {"seed": "synthetic-fixed", "fractions": {"train": 0, "dev": 0, "test": 1}}
            snapshot(base / "dataset", T1, base / "first", db, split_config=config)
            snapshot(base / "dataset", T3, base / "second", db, prior_partition=base / "first/manifest.json")
            split = json.loads((base / "second/split_config.json").read_text(encoding="utf-8"))
            mapping = {mid: group["split"] for group in split["assignments"] for mid in group["meme_ids"]}
            self.assertEqual(mapping, {"z": "train", "a-new": "train", "b": "test"})
            with self.assertRaisesRegex(ValueError, "split conflict"):
                snapshot(base / "dataset", T4, base / "third", db, prior_partition=base / "second/manifest.json")
            self.assertFalse((base / "third/records.jsonl").exists())
            self.assertTrue((base / "third/partition_conflict.json").exists())
            conflict = json.loads((base / "third/partition_conflict.json").read_text(encoding="utf-8"))
            self.assertEqual(set(conflict["conflict_meme_ids"]), {"a-new", "b", "z"})
            self.assertEqual(set(conflict["requested_or_fixed_splits"]), {"train", "test"})
            failed = ResearchLog(db).list()[-1]
            self.assertEqual(failed["status"], "failed")
            self.assertIn("PartitionConflictError", failed["finish"]["notes"])


if __name__ == "__main__":
    unittest.main()
