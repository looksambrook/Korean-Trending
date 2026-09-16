"""Synthetic fixtures exercise retrieval, not meme discovery accuracy."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from meme_identify import identify, run
from research_log import ResearchLog

STAMP = "2026-01-02T00:00:00+00:00"


def fixture():
    titles = ["충분히 발달한 고양이는 인형과 구분할 수 없다", "충분히 발달한 기계는 동물과 구별할 수 없다"]
    examples = [{"observation_id": f"synthetic-{i}", "video_id": str(i), "title": title,
                 "channel_id": f"synthetic-channel-{i}", "source_url": None,
                 "posted_at": "2026-01-01T00:00:00+00:00", "available_at": STAMP,
                 "provenance": "ai_synthetic"} for i, title in enumerate(titles)]
    return {"run_id": "synthetic-not-a-real-run", "created_at": STAMP, "families": [{
        "family_id": "synthetic-family", "kind": "sentence_comparison",
        "label": "충분히 발달한 {A}는 {B}와 구분/구별할 수 없다", "status": "supported_textual_reuse",
        "created_at": STAMP, "examples": examples,
        "relationships": [{"left": "synthetic-0", "right": "synthetic-1"}],
        "knowledge": {"meaning": "Synthetic rule-derived meaning for testing only.", "available_at": STAMP,
                      "method": "synthetic_fixture", "evidence_ids": ["synthetic-0", "synthetic-1"],
                      "unknowns": ["test fixture only"]}}]}


class MemeIdentifyTests(unittest.TestCase):
    def test_unseen_frame_variant_is_tentative_and_future_knowledge_is_excluded(self):
        source = fixture()
        result = identify(source, "충분히 발달한 자전거는 자동차와 구분할 수 없다",
                          as_of="2026-01-03T00:00:00Z", input_provenance="ai_synthetic")
        self.assertEqual(result["status"], "tentative_match")
        self.assertEqual(result["matches"][0]["match_type"], "shared_observed_sentence_frame")
        self.assertEqual(result["matches"][0]["input_structure"]["A"], "자전거")
        self.assertFalse(result["matches"][0]["is_definitive_identification"])
        self.assertEqual(len(result["matches"][0]["source_examples"]), 2)
        self.assertFalse(result["model_weights_trained"])
        earlier = identify(source, "충분히 발달한 자전거는 자동차와 구분할 수 없다",
                           as_of="2026-01-01T12:00:00Z", input_provenance="ai_synthetic")
        self.assertEqual(earlier["status"], "unknown")
        self.assertEqual(earlier["eligible_family_count"], 0)
        self.assertIn("after_as_of", earlier["excluded_knowledge"][0]["reason"])

    def test_unknown_input_and_synthetic_log_do_not_become_observed_evidence(self):
        source = fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            db = Path(directory) / "research.sqlite3"
            unknown, output = run(path, "오늘 오후 회의는 취소되었습니다", output_path=Path(directory) / "out.json",
                                  db=db, as_of="2026-01-03T00:00:00Z", input_provenance="ai_synthetic")
            self.assertEqual(unknown["status"], "unknown")
            self.assertEqual(unknown["matches"], [])
            self.assertFalse(unknown["input"]["is_collected_source_example"])
            self.assertTrue(output.exists())
            entry = ResearchLog(db).status(unknown["run_id"])
            self.assertEqual(entry["spec"]["provenance"], "ai_synthetic")
            self.assertEqual(entry["spec"]["parameters"]["input_provenance"], "ai_synthetic")
            self.assertEqual(entry["finish"]["actual_cost"]["human_minutes"], 0)
            self.assertEqual(len(source["families"][0]["examples"]), 2)


if __name__ == "__main__":
    unittest.main()
