"""AI-authored synthetic cases verify retrieval mechanics, not meme accuracy."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import youtube_candidates as yc
from research_log import DuplicateRunError, ResearchLog, hash_file

CUTOFF = "2026-09-15T00:00:00Z"


def observation(oid, video, text, **extra):
    row = {"observation_id": oid, "platform": "youtube", "source_id": video,
           "video_id": video, "channel_id": "synthetic_channel_" + video,
           "modality": "title", "text": text, "context": {"title": text},
           "posted_at": "2026-09-12T00:00:00Z", "updated_at": None,
           "collected_at": "2026-09-13T00:00:00Z", "available_at": "2026-09-13T00:00:00Z",
           "provenance": "ai_synthetic", "source_url": None,
           "sample_method": "synthetic_test_only"}
    row.update(extra)
    return row


class CandidateRetrievalTest(unittest.TestCase):
    def test_exact_repeat_is_unverified_and_copy_status_not_invented(self):
        rows = [observation("a", "v1", "오늘도 연구실 문이 열렸다"),
                observation("b", "v2", "오늘도 연구실 문이 열렸다")]
        result = yc.detect_candidates(rows, cutoff=CUTOFF)
        self.assertEqual(result["summary"]["candidate_count"], 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["representative_phrase"], "오늘도 연구실 문이 열렸다")
        self.assertEqual(candidate["score_components"]["source_document_frequency"], 2)
        self.assertEqual(candidate["score_components"]["distinct_observed_content_count"], 1)
        self.assertEqual(candidate["exact_normalized_text_groups"], [["a", "b"]])
        self.assertIsNone(candidate["score_components"]["independent_use_count"])
        self.assertFalse(candidate["verified_meme"])
        self.assertIsNone(candidate["meaning"])
        self.assertEqual(result["summary"]["eligible_by_provenance"], {"ai_synthetic": 2})

    def test_repetition_within_one_video_is_not_cross_video_support(self):
        rows = [observation(str(i), "v1", "오늘도 연구실 문이 열렸다 " * 20,
                            source_id="comment_" + str(i), modality="comment") for i in range(5)]
        result = yc.detect_candidates(rows, cutoff=CUTOFF)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["summary"]["eligible_source_documents"], 5)
        self.assertEqual(result["summary"]["eligible_source_videos"], 1)

    def test_title_description_same_source_is_one_document(self):
        rows = [observation("a", "v1", "오늘도 연구실 문이 열렸다"),
                observation("b", "v1", "오늘도 연구실 문이 열렸다", modality="description"),
                observation("c", "v2", "오늘도 연구실 문이 열렸다")]
        candidate = yc.detect_candidates(rows, cutoff=CUTOFF)["candidates"][0]
        self.assertEqual(candidate["score_components"]["source_document_frequency"], 2)
        self.assertEqual(candidate["score_components"]["observation_count"], 3)

    def test_variants_are_provisional_surface_links_not_verified_families(self):
        rows = [observation("a", "v1", "오늘도 연구실 문이 열렸다"),
                observation("b", "v2", "오늘도 작업실 문이 열렸다")]
        d0 = yc.detect_candidates(rows, cutoff=CUTOFF, method="D0")
        d1 = yc.detect_candidates(rows, cutoff=CUTOFF, method="D1")
        self.assertTrue(any(c["possible_variant_links"] for c in d1["candidates"]))
        linked = next(c for c in d1["candidates"] if c["possible_variant_links"])
        self.assertIn("오늘도 연구실 문이 열렸다", linked["surface_forms"])
        self.assertIn("오늘도 작업실 문이 열렸다", linked["surface_forms"])
        self.assertEqual(linked["evidence_observation_ids"], ["a", "b"])
        self.assertTrue(all(not c["possible_variant_links"] for c in d0["candidates"]))
        self.assertEqual(d1["summary"]["context_semantic_validation"], "not_performed")

    def test_unrelated_text_can_correctly_yield_zero_candidates(self):
        rows = [observation("a", "v1", "푸른 하늘을 바라봅니다"),
                observation("b", "v2", "점심 반찬 준비 완료")]
        result = yc.detect_candidates(rows, cutoff=CUTOFF, method="D1")
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["summary"]["missed_meme_count"])
        self.assertIsNone(result["summary"]["precision"])
        self.assertEqual(result["summary"]["eligible_observations"], 2)

    def test_mixed_synthetic_and_collected_evidence_cannot_create_support(self):
        rows = [observation("a", "v1", "오늘도 연구실 문이 열렸다"),
                observation("b", "v2", "오늘도 연구실 문이 열렸다", provenance="actual_collected")]
        with self.assertRaisesRegex(ValueError, "provenance separately"):
            yc.detect_candidates(rows, cutoff=CUTOFF)

    def test_new_links_are_not_backdated_to_the_evidence_cutoff(self):
        rows = [observation("a", "v1", "오늘도 연구실 문이 열렸다"),
                observation("b", "v2", "오늘도 작업실 문이 열렸다")]
        result = yc.detect_candidates(rows, cutoff=CUTOFF, method="D1")
        for candidate in result["candidates"]:
            self.assertEqual(candidate["linked_at"], result["summary"]["generated_at"])
            for edge in candidate["possible_variant_links"]:
                self.assertEqual(edge["linked_at"], result["summary"]["generated_at"])

    def test_time_cutoff_rejects_every_future_field_and_unknown_dates(self):
        rows = []
        for field in ("posted_at", "updated_at", "collected_at", "available_at"):
            rows.append(observation(field, field, "오늘도 연구실 문이 열렸다", **{field: "2026-09-16T00:00:00Z"}))
        rows.append(observation("missing", "v5", "오늘도 연구실 문이 열렸다", posted_at=None))
        result = yc.detect_candidates(rows, cutoff=CUTOFF)
        self.assertEqual(result["summary"]["eligible_observations"], 0)
        self.assertEqual(result["summary"]["excluded_observations"], 5)
        self.assertEqual(set(result["summary"]["exclusion_reasons"]),
                         {"posted_at_after_cutoff", "updated_at_after_cutoff", "collected_at_after_cutoff",
                          "available_at_after_cutoff", "missing_or_invalid_posted_at"})
        with self.assertRaises(ValueError):
            yc.detect_candidates([], cutoff="2026-09-15T00:00:00")

    def test_first_seen_timestamp_cannot_leak_later_fetched_text(self):
        row = observation("a", "v1", "나중에 편집한 설명",
                          collected_at="2026-09-16T00:00:00Z", available_at="2026-09-12T00:00:00Z")
        result = yc.detect_candidates([row], cutoff=CUTOFF)
        self.assertEqual(result["summary"]["eligible_observations"], 0)

    def test_latest_snapshot_and_duplicate_ids_do_not_multiply_frequency(self):
        old = observation("old", "v1", "오늘도 연구실 문이 열렸다")
        newer = observation("new", "v1", "수정한 제목만 남깁니다", collected_at="2026-09-14T00:00:00Z")
        third = observation("third", "v2", "오늘도 연구실 문이 열렸다")
        result = yc.detect_candidates([old, newer, third, copy.deepcopy(third)], cutoff=CUTOFF)
        self.assertEqual(result["summary"]["eligible_observations"], 2)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["summary"]["exclusion_reasons"],
                         {"duplicate_observation": 1, "superseded_source_snapshot": 1})
        conflict = copy.deepcopy(third)
        conflict["text"] = "동일 ID 다른 원문"
        result = yc.detect_candidates([third, conflict], cutoff=CUTOFF)
        self.assertEqual(result["summary"]["eligible_observations"], 0)
        self.assertEqual(result["summary"]["exclusion_reasons"], {"conflicting_observation_id": 2})

    def test_unicode_normalization_and_document_frequency_not_token_count(self):
        rows = [observation("a", "v1", "오늘도   연구실 문이 열렸다 " * 3),
                observation("b", "v2", "오늘도 연구실 문이 열렸다")]
        result = yc.detect_candidates(rows, cutoff=CUTOFF)
        repeated = next(c for c in result["candidates"] if c["representative_phrase"] == "오늘도 연구실 문이 열렸다")
        self.assertEqual(repeated["score_components"]["source_document_frequency"], 2)

    def test_baseline_fractions_have_observed_document_denominators(self):
        rows = [observation("a", "v1", "오늘도 연구실 문이 열렸다", posted_at="2026-09-10T00:00:00Z"),
                observation("b", "v2", "오늘도 연구실 문이 열렸다"),
                observation("c", "v3", "관계없는 제목 내용", posted_at="2026-09-10T00:00:00Z")]
        result = yc.detect_candidates(rows, cutoff=CUTOFF, baseline_end="2026-09-11T00:00:00Z")
        self.assertEqual(result["summary"]["sample_window_source_denominators"], {"baseline": 2, "recent": 1})
        self.assertEqual(result["candidates"][0]["sample_growth"]["fraction_difference"], .5)
        self.assertEqual(result["candidates"][0]["sample_growth"]["claim_scope"], "observed_sample_only_not_platform_growth")

    def test_logged_cli_preserves_input_and_blocks_duplicate_even_new_output_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "observations.jsonl"
            rows = [observation("a", "v1", "오늘도 연구실 문이 열렸다"),
                    observation("b", "v2", "오늘도 연구실 문이 열렸다")]
            input_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
            before = hash_file(input_path)
            summary = yc.run(input_path, root / "first", db=root / "log.sqlite3", cutoff=CUTOFF)
            self.assertEqual(before, hash_file(input_path))
            self.assertEqual(summary["candidate_count"], 1)
            entry = ResearchLog(root / "log.sqlite3").status(summary["run_id"])
            self.assertEqual(entry["status"], "succeeded")
            self.assertEqual(entry["spec"]["provenance"], "ai_synthetic")
            self.assertEqual(len(entry["finish"]["artifacts"]), 5)
            with self.assertRaises(DuplicateRunError):
                yc.run(input_path, root / "second", db=root / "log.sqlite3", cutoff="2026-09-15T09:00:00+09:00", method="D0")
            self.assertFalse((root / "second").exists())
            with self.assertRaises(ValueError):
                yc.run(input_path, root / "first", db=root / "log.sqlite3", cutoff=CUTOFF)

    def test_metadata_boundaries_do_not_create_cross_line_or_adjacent_tag_phrases(self):
        rows = [observation(str(i), "v" + str(i), "바람이 분다\n강물이 흐른다\n#푸른별 #노란달",
                            modality="video_metadata", context={"title": "바람이 분다",
                            "description": "강물이 흐른다\n#푸른별 #노란달"}) for i in range(2)]
        metadata = yc.detect_candidates(rows, cutoff=CUTOFF)
        forms = {p for c in metadata["candidates"] for p in c["surface_forms"]}
        self.assertIn("푸른별", forms)
        self.assertIn("노란달", forms)
        self.assertNotIn("푸른별 노란달", forms)
        self.assertFalse(any("분다 강물이" in p for p in forms))
        legacy = yc.detect_candidates(rows, cutoff=CUTOFF, text_scope="full_text")
        self.assertTrue(any("분다 강물이" in p for c in legacy["candidates"] for p in c["surface_forms"]))

    def test_repeated_description_and_contacts_are_suppressed_with_evidence_while_tags_survive(self):
        rows = [observation(str(i), "v" + str(i), "고유한 제목\n항상 같은 안내 문장\n#별빛콩\n편집: 연구원\nhttps://example.test\nmail@example.test",
                            channel_id="same_channel", modality="video_metadata",
                            context={"title": "고유한 제목 " + str(i), "description":
                            "항상 같은 안내 문장\n#별빛콩\n편집: 연구원\nhttps://example.test\nmail@example.test"}) for i in range(3)]
        original = copy.deepcopy(rows)
        result = yc.detect_candidates(rows, cutoff=CUTOFF)
        self.assertEqual(rows, original)
        self.assertEqual(result["evidence"], sorted(rows, key=lambda row: row["observation_id"]))
        forms = {p for c in result["candidates"] for p in c["surface_forms"]}
        self.assertIn("별빛콩", forms)
        self.assertNotIn("항상 같은 안내 문장", forms)
        self.assertNotIn("편집 연구원", forms)
        self.assertEqual(result["summary"]["suppression_reasons"]["same_channel_repeated_description_line"], 3)
        self.assertEqual(result["summary"]["suppression_reasons"]["production_or_contact_notice"], 3)
        self.assertEqual(result["summary"]["suppression_reasons"]["url_or_email_fragment"], 6)
        self.assertTrue(all(item["observation_id"] in {"0", "1", "2"} for item in result["suppressed"]))

    def test_title_repetition_survives_even_when_same_description_phrase_is_suppressed(self):
        rows = [observation(str(i), "v" + str(i), "오늘도 연구실 문이 열렸다", channel_id="one",
                            modality="video_metadata", context={"title": "오늘도 연구실 문이 열렸다",
                            "description": "오늘도 연구실 문이 열렸다"}) for i in range(3)]
        candidate = yc.detect_candidates(rows, cutoff=CUTOFF)["candidates"][0]
        self.assertEqual(candidate["score_components"]["title_support"], 3)
        self.assertEqual(candidate["score_components"]["boilerplate_support_excluded"], 3)
        self.assertEqual(candidate["score_components"]["channel_count"], 1)
        self.assertFalse(candidate["verified_meme"])

    def test_channel_wide_tags_are_retained_but_rank_below_title_supported_phrase(self):
        rows = [observation(str(i), "v" + str(i), "연구실 문이 열렸다\n#상시태그", channel_id="one",
                            modality="video_metadata", context={"title": "연구실 문이 열렸다" if i < 2 else "새로운 주제입니다",
                            "description": "#상시태그", "channel_label": "상시태그"}) for i in range(3)]
        baseline = yc.detect_candidates(rows, cutoff=CUTOFF, method="D0")
        self.assertEqual(baseline["summary"]["ranking_dimensions_descending"], ["source_document_frequency", "source_video_count"])
        self.assertFalse(baseline["summary"]["experimental_context_ranking"])
        self.assertEqual(baseline["candidates"][0]["representative_phrase"], "상시태그")
        result = yc.detect_candidates(rows, cutoff=CUTOFF, method="D1")
        self.assertTrue(result["summary"]["experimental_context_ranking"])
        tag = next(c for c in result["candidates"] if c["representative_phrase"] == "상시태그")
        title = next(c for c in result["candidates"] if c["representative_phrase"] == "연구실 문이 열렸다")
        self.assertTrue(tag["ubiquitous_hashtag_only_flag"])
        self.assertEqual(tag["score_components"]["hashtag_support"], 3)
        self.assertEqual(tag["score_components"]["channel_label_match_support"], 3)
        self.assertLess(title["rank"], tag["rank"])

    def test_api_video_kind_uses_metadata_but_comment_parent_title_is_not_an_observed_use(self):
        rows = [observation(str(i), "v" + str(i), "서로 다른 댓글 " + str(i), kind="comment",
                            modality="text", source_id="comment_" + str(i),
                            context={"title": "부모영상 제목을 끌어오지 않는다"}) for i in range(2)]
        comments = yc.detect_candidates(rows, cutoff=CUTOFF)
        self.assertTrue(all(c["score_components"]["title_support"] == 0 for c in comments["candidates"]))
        self.assertFalse(any("부모영상" in p for c in comments["candidates"] for p in c["surface_forms"]))
        for row in rows:
            row["kind"] = "video"
            row["context"]["video_title"] = row["context"].pop("title")
            row["context"]["video_description"] = "편집: 자동제거확인"
        videos = yc.detect_candidates(rows, cutoff=CUTOFF)
        self.assertTrue(any(c["score_components"]["title_support"] == 2 for c in videos["candidates"]))
        self.assertEqual(videos["summary"]["suppression_reasons"], {"production_or_contact_notice": 2})


if __name__ == "__main__":
    unittest.main()
