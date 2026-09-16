"""AI-synthetic unit cases test decisions, not real-world meme accuracy."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from meme_discovery_engine import build_families, plan_seeds, select_followups


def row(n, title, *, channel=None, description="", available=True):
    return {"observation_id": "authored:" + str(n), "video_id": str(n).zfill(11),
            "channel_id": channel or "channel_" + str(n), "context": {"title": title, "description": description},
            "text": title + "\n" + description, "source_url": "https://www.youtube.com/watch?v=" + str(n).zfill(11),
            "posted_at": "2026-09-01T00:00:00Z" if available else None,
            "available_at": "2026-09-02T00:00:00Z", "provenance": "ai_synthetic",
            "sample_method": {"mode": "channel_feed", "known_meme_name_seed": False}}


class DiscoveryEngineTests(unittest.TestCase):
    def test_unseen_comparison_prefix_and_generic_synonym(self):
        rows = [row(1, "지나치게 정교한 간식은 예술과 구분할 수 없다"),
                row(2, "지나치게 정교한 설명은 주문과 구별할 수 없다")]
        seeds = plan_seeds(rows)
        self.assertEqual(seeds[0]["query"], "지나치게 정교한")
        result = build_families(rows, seeds)
        family = result["families"][0]
        self.assertEqual(family["status"], "supported_textual_reuse")
        self.assertIn("간식", str(family["knowledge"]["usage"]))
        self.assertIsNone(family["independent_use_count"])
        other = [row(3, "끝없이 반복되는 출근은 여행과 구분할 수 없다"),
                 row(4, "끝없이 반복되는 회의는 수업과 구별할 수 없다")]
        self.assertEqual(plan_seeds(other)[0]["query"], "끝없이 반복되는")

    def test_same_video_or_duplicate_title_cannot_create_variation(self):
        first = row(1, "충분히 복잡한 안내는 암호와 구분할 수 없다")
        copied = row(2, first["context"]["title"])
        result = build_families([first, dict(first), copied], plan_seeds([first]))
        self.assertEqual(result["families"][0]["distinct_video_count"], 2)
        self.assertEqual(result["families"][0]["status"], "needs_evidence")
        self.assertEqual(result["families"][0]["relationships"][0]["relation"], "duplicate_text_not_independent_variation")

    def test_news_repetition_does_not_become_meme(self):
        rows = [row(1, "[속보] 정부 금리 인하 발표"), row(2, "정부 금리 인하 발표"),
                row(3, "서울 아파트 가격 다시 상승"), row(4, "부산 아파트 가격 다시 상승")]
        self.assertEqual(plan_seeds(rows), [])
        self.assertEqual(build_families(rows, [])["families"], [])

    def test_parody_is_not_forced_to_a_slot(self):
        rows = [row(1, "모카라떼 모카란떼"),
                row(2, "모카란떼 첫 공연", description="이 공연은 패러디입니다."),
                row(3, "모카란떼와 만나다")]
        seeds = plan_seeds(rows)
        seed = next(seed for seed in seeds if seed["query"] == "모카란떼")
        family = next(f for f in build_families(rows, [seed])["families"] if f["label"] == "모카란떼")
        self.assertEqual(family["kind"], "source_declared_parody")
        self.assertEqual(family["status"], "supported_textual_reuse")
        self.assertNotIn("{A}", family["label"])
        self.assertTrue(family["knowledge"]["usage"][0]["source_excerpt"])

    def test_exact_query_and_diverse_followups(self):
        original = row(1, "지나치게 정교한 간식은 예술과 구분할 수 없다", channel="one")
        seed = plan_seeds([original])[0]
        self.assertIn(seed["query"], original["text"])
        cards = [original, row(2, original["context"]["title"], channel="one"),
                 row(3, "지나치게 정교한 설명은 주문과 구별할 수 없다", channel="two"),
                 row(4, "지나치게 정교한 지도는 미로와 구분할 수 없다", channel="three"),
                 row(5, "지나치게 정교한 컴퓨터 기술 소개", channel="four")]
        chosen = select_followups(seed, cards, limit=2)
        self.assertEqual(set(chosen), {str(3).zfill(11), str(4).zfill(11)})

    def test_untimed_search_card_never_supports_family(self):
        rows = [row(1, "지나치게 정교한 간식은 예술과 구분할 수 없다"),
                row(2, "지나치게 정교한 설명은 주문과 구별할 수 없다", available=False)]
        result = build_families(rows, plan_seeds(rows))
        self.assertEqual(result["families"][0]["status"], "needs_evidence")
        self.assertEqual(result["rejected"][0]["reason"], "exact_publication_or_observation_time_missing")

    def test_latest_snapshot_and_pipeline_origin_are_preserved(self):
        original = row(1, "지나치게 정교한 간식은 예술과 구분할 수 없다", description="old long description")
        original["discovery_origin"] = "name_free_channel_observation"
        newer = row(1, original["context"]["title"], description="new")
        newer["available_at"] = "2026-09-03T00:00:00Z"
        newer["discovery_origin"] = "observed_phrase_expansion"
        family = build_families([original, newer], plan_seeds([original]))["families"][0]
        example = family["examples"][0]
        self.assertEqual(example["description"], "new")
        self.assertEqual(example["discovery_origin"]["pipeline_origin"], "observed_phrase_expansion")
        self.assertIn("name_free_channel_observation", example["discovery_origin"]["observed_pipeline_origins"])
        self.assertEqual(len(example["collection_snapshots"]), 2)

    def test_generic_media_reference_does_not_link_different_sounds(self):
        rows = [row(1, "한번 들어본 '그 소리' 밈"), row(2, "그 소리를 따라해봤다"), row(3, "영화에서 나온 그 소리")]
        self.assertFalse(any(seed["query"] == "그 소리" for seed in plan_seeds(rows)))
        seed = {"seed_id": "synthetic", "query": "그 소리", "kind": "quoted_expression"}
        family = build_families(rows, [seed])["families"][0]
        self.assertEqual(family["status"], "needs_evidence")
        self.assertEqual(family["relationships"][0]["relation"], "rejected_generic_reference_link")

    def test_unseen_repeated_catchphrase_with_changed_middle(self):
        rows = [row(1, "먹을래 먹을래 사과를 먹을래"), row(2, "먹을래 먹을래 바나나를 먹을래")]
        seed = next(seed for seed in plan_seeds(rows) if seed["kind"] == "repeated_catchphrase")
        family = build_families(rows, [seed])["families"][0]
        self.assertEqual(family["status"], "supported_textual_reuse")
        self.assertEqual(family["kind"], "repeated_catchphrase")

    def test_search_description_snippet_can_resolve_translated_title(self):
        seed = {"query": "모카란떼", "seed_id": "synthetic", "kind": "orthographic_wordplay"}
        translated = row(1, "Mocarante - LIVE")
        translated["context"].pop("description")
        translated["context"]["description_snippet"] = "#모카란떼 공연 패러디"
        self.assertEqual(select_followups(seed, [translated]), [str(1).zfill(11)])


if __name__ == "__main__":
    unittest.main()
