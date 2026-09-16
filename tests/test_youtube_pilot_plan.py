"""Offline proposal checks; no YouTube or model operations."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import youtube_pilot_plan as yp
from research_log import DuplicateRunError, ResearchLog


class YouTubePilotPlanTest(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "configs/youtube_pilot_v01.json").read_text(encoding="utf-8-sig"))

    def test_hand_calculated_example_separates_search_bucket_and_unknown_cash(self):
        plan = yp.build_plan(self.cfg)
        self.assertEqual(plan["quota_estimate"]["search_queries_separate_bucket"], 6)
        self.assertEqual(plan["quota_estimate"]["other_units"], 145)
        self.assertEqual(plan["model_estimate"]["total_calls"], 156)
        self.assertEqual(plan["model_estimate"]["input_tokens"], 504000)
        self.assertEqual(plan["model_estimate"]["output_tokens"], 84000)
        self.assertIsNone(plan["model_estimate"]["model_only_usd"])
        self.assertFalse(plan["execution_ready"])
        self.assertEqual({cell["condition"] for cell in plan["cells"]}, {"D0U0", "D0U1", "D1U0", "D1U1"})
        self.assertTrue(all(cell["include_missed_families"] for cell in plan["cells"]))

    def test_invalid_counts_and_nonfinite_or_boolean_prices_rejected(self):
        for value in (-1, True, 1.5):
            cfg = copy.deepcopy(self.cfg)
            cfg["sampling"]["max_videos_example"] = value
            with self.assertRaises(ValueError):
                yp.build_plan(cfg)
        for value in (-1, True, float("nan"), float("inf")):
            cfg = copy.deepcopy(self.cfg)
            cfg["cost_assumptions"]["input_usd_per_million_tokens"] = value
            with self.assertRaises(ValueError):
                yp.build_plan(cfg)

    def test_human_corrections_seeds_and_changed_conditions_cannot_silently_enter(self):
        changes = [
            ("sampling", "known_meme_seed_names", ["TEST_ONLY_SEED"]),
            ("experiment", "manual_record_correction_in_main_conditions", True),
            ("experiment", "discovery_conditions", {"D0": "given_family_generation"}),
            ("access", "mode", "execute"),
        ]
        for section, key, value in changes:
            cfg = copy.deepcopy(self.cfg)
            cfg[section][key] = value
            with self.assertRaises(ValueError):
                yp.build_plan(cfg)

    def test_duplicate_output_path_change_cannot_bypass_log(self):
        with tempfile.TemporaryDirectory(prefix="TEST_ONLY_youtube_plan_") as folder:
            base = Path(folder)
            config_path = base / "config.json"
            config_path.write_text(json.dumps(self.cfg), encoding="utf-8")
            db = base / "research.sqlite3"
            result = yp.prepare(config_path, base / "plan.json", db)
            with self.assertRaises(DuplicateRunError):
                yp.prepare(config_path, base / "second.json", db)
            self.assertFalse((base / "second.json").exists())
            self.assertEqual(ResearchLog(db).status(result["run_id"])["status"], "succeeded")

    def test_existing_output_is_preserved_and_failure_registered(self):
        with tempfile.TemporaryDirectory(prefix="TEST_ONLY_youtube_plan_") as folder:
            base = Path(folder)
            config_path = base / "config.json"
            config_path.write_text(json.dumps(self.cfg), encoding="utf-8")
            output = base / "existing.json"
            output.write_text("TEST_ONLY_existing", encoding="utf-8")
            db = base / "research.sqlite3"
            with self.assertRaises(FileExistsError):
                yp.prepare(config_path, output, db)
            self.assertEqual(output.read_text(encoding="utf-8"), "TEST_ONLY_existing")
            self.assertEqual(ResearchLog(db).list()[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
