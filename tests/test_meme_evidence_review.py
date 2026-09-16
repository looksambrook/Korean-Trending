"""Synthetic examples test triage safeguards; they are never collected evidence."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from meme_evidence_review import build_review

CUTOFF = "2026-09-15T12:00:00+00:00"


def observation(number, title, description="", channel="channel-a"):
    return {"observation_id": f"o{number}", "platform": "youtube", "source_id": f"v{number}",
            "video_id": f"v{number}", "channel_id": channel, "source_url": f"https://example.test/{number}",
            "text": title + "\n" + description, "context": {"title": title, "description": description, "channel_label": "테스트방송"},
            "modality": "video_metadata", "provenance": "ai_synthetic", "posted_at": "2026-09-14T00:00:00+00:00",
            "available_at": "2026-09-15T10:00:00+00:00", "collected_at": "2026-09-15T10:00:00+00:00",
            "sample_method": {"mode": "unit_test", "known_meme_name_seed": False}}


def candidate(phrase, ids, **extra):
    return {"candidate_id": "c1", "representative_phrase": phrase, "surface_forms": [phrase],
            "evidence_observation_ids": ids, **extra}


class MemeEvidenceTests(unittest.TestCase):
    def test_repeated_series_and_tags_are_not_memes(self):
        rows = [observation(1, "산에 간 하루 | 산책일기", "#산책일기"),
                observation(2, "책 읽은 하루 | 산책일기", "#산책일기")]
        result = build_review(rows, [candidate("산책일기", ["o1", "o2"])], cutoff=CUTOFF)
        self.assertEqual(result["reviews"][0]["decision"], "likely_topic")
        self.assertFalse(result["reviews"][0]["verified_meme"])
        self.assertEqual(result["family_hints"], [])

    def test_recurring_phrase_alone_requires_evidence(self):
        rows = [observation(1, "오늘 정말 좋은 하루"), observation(2, "오늘 정말 좋은 하루")]
        item = build_review(rows, [candidate("좋은 하루", ["o1", "o2"])], cutoff=CUTOFF)["reviews"][0]
        self.assertEqual(item["decision"], "requires_evidence")
        self.assertEqual(item["literal_recurrence"]["distinct_source_videos"], 2)
        self.assertIsNone(item["literal_recurrence"]["independent_use_count"])

    def test_generic_clause_variation_keeps_source_and_unknowns(self):
        rows = [observation(1, "충분히 잠든 고양이는 인형과 구분할 수 없다"),
                observation(2, "충분히 지친 직장인은 돌과 구분할 수 없다", channel="channel-b")]
        result = build_review(rows, [candidate("구분할 수 없다", ["o1", "o2"])], cutoff=CUTOFF)
        self.assertEqual(len(result["family_hints"]), 1)
        item = result["reviews"][0]
        self.assertEqual(item["decision"], "possible_meme")
        self.assertEqual(item["observed_examples"][0]["source_url"], "https://example.test/1")
        self.assertIn("semantic_meme_relation", item["unknowns"])
        self.assertFalse(item["verified_meme"])
        self.assertEqual(len(result["seed_candidate_review_queue"]), 2)

    def test_future_evidence_cannot_support_relation_single_title_stays_queue(self):
        rows = [observation(1, "고양이가 말을 할 때"), observation(2, "고양이가 말을 할 때 패러디")]
        rows[1]["collected_at"] = rows[1]["available_at"] = "2026-09-16T10:00:00+00:00"
        result = build_review(rows, [candidate("고양이가 말을 할 때", ["o1", "o2"])], cutoff=CUTOFF)
        self.assertEqual(result["reviews"][0]["missing_or_excluded_evidence_ids"], ["o2"])
        self.assertEqual(result["reviews"][0]["decision"], "requires_evidence")
        self.assertEqual(result["seed_candidate_review_queue"][0]["decision"], "requires_evidence")
        self.assertEqual(len(result["exclusions"]), 1)


if __name__ == "__main__":
    unittest.main()
