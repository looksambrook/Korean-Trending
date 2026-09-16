import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import meme_pipeline as mp


def fixture():
    cfg = mp.load_config(ROOT / "configs/smoke.json")
    families = mp.read_jsonl(ROOT / "data/fixtures/families.jsonl")
    supports = mp.read_jsonl(ROOT / "data/fixtures/supports.jsonl")
    scenarios = mp.read_jsonl(ROOT / "data/fixtures/scenarios.jsonl")
    return cfg, families, supports, scenarios


def mock_extraction_responses(requests):
    responses = []
    for r in requests:
        fields = {key: [] for key in mp.FIELDS}
        fields["form_labels"] = [{"value": "형식 점검용 합성 인용·대화",
                                  "evidence_ids": r["support_ids"], "certainty": "tentative"}]
        fields["preservation_constraints"] = [{"value": "합성 fixture의 문구 흐름",
                                               "evidence_ids": r["support_ids"], "certainty": "tentative"}]
        fields["uncertainty_notes"] = [{"value": "실제 유행어나 자동 모델 분석이 아님",
                                       "evidence_ids": [], "certainty": "tentative"}]
        responses.append({"request_id": r["request_id"], "record_origin": "smoke_fixture",
                          "model_id": "SMOKE_NO_MODEL", "response_id": "fixture_" + r["request_id"],
                          "created_at": "2026-09-11T00:00:00Z",
                          "usage": {"input_tokens": None, "output_tokens": None},
                          "status": "ok", "output_text": json.dumps(fields, ensure_ascii=False)})
    return responses


def mock_generation_responses(requests):
    return [{"request_id": r["request_id"], "record_origin": "smoke_fixture",
             "model_id": "SMOKE_NO_MODEL", "response_id": "fixture_" + r["request_id"],
             "created_at": "2026-09-11T00:00:00Z",
             "usage": {"input_tokens": None, "output_tokens": None},
             "status": "ok", "output_text": "[소프트웨어 점검용 가상 출력 — 모델 생성 결과가 아님]"}
            for r in requests]


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.cfg, self.families, self.supports, self.scenarios = fixture()
        self.extracts = mp.make_extractions(self.families, self.supports, self.cfg)
        self.responses = mock_extraction_responses(self.extracts)
        self.records, self.failures = mp.import_extractions(self.extracts, self.responses, self.cfg)

    def generate(self, records=None):
        return mp.generation_requests(self.families, self.supports, self.scenarios,
                                      self.records if records is None else records, self.cfg)

    def test_extraction_only_uses_allowed_support_columns(self):
        self.supports[0]["evaluation_situation"] = "SECRET_QUERY"
        self.supports[0]["gold_output"] = "SECRET_GOLD"
        self.families[0]["ratings"] = "SECRET_SCORE"
        reqs = mp.make_extractions(self.families, self.supports, self.cfg)
        messages = json.dumps([r["messages"] for r in reqs])
        for sentinel in ("SECRET_QUERY", "SECRET_GOLD", "SECRET_SCORE"):
            self.assertNotIn(sentinel, messages)

    def test_lineage_cannot_cross_splits(self):
        self.families[1]["lineage_group_id"] = self.families[0]["lineage_group_id"]
        self.families[1]["split"] = "test"
        with self.assertRaisesRegex(ValueError, "Lineage"):
            mp.validate_dataset(self.families, self.supports, self.cfg)

    def test_repost_group_cannot_cross_splits(self):
        self.families[1]["split"] = "test"
        self.supports[1]["source_group_id"] = self.supports[0]["source_group_id"]
        with self.assertRaisesRegex(ValueError, "source"):
            mp.validate_dataset(self.families, self.supports, self.cfg)

    def test_unknown_evidence_rejected(self):
        fields = copy.deepcopy(self.records[0]["fields"])
        fields["form_labels"][0]["evidence_ids"] = ["invented_source"]
        with self.assertRaisesRegex(ValueError, "evidence"):
            mp.validate_fields(fields, self.records[0]["support_ids"])

    def test_non_slot_record_is_valid(self):
        self.assertEqual(self.records[0]["fields"]["editable_elements"], [])
        self.assertEqual(self.failures, [])

    def test_parity_and_same_raw_base(self):
        reqs, failures, audit = self.generate()
        self.assertFalse(failures)
        self.assertEqual(len(reqs), 6)
        for scenario in self.scenarios:
            chosen = {r["condition"]: r for r in reqs if r["scenario_id"] == scenario["scenario_id"]}
            self.assertEqual(len({r["base_hash"] for r in chosen.values()}), 1)
            base = chosen["B1"]["messages"]["user"]
            self.assertTrue(chosen["B2"]["messages"]["user"].startswith(base))
            self.assertTrue(chosen["B3"]["messages"]["user"].startswith(base))
            record = next(r for r in self.records if r["family_id"] == scenario["family_id"])
            b3 = json.loads(chosen["B3"]["messages"]["user"].split("\n\n추가 분석:\n")[1])
            inverse_certainty = {v: k for k, v in mp.CERTAINTY.items()}
            recovered = {name: [{"value": fact["내용"], "evidence_ids": fact["근거 용례"],
                                 "certainty": inverse_certainty[fact["판단"]]}
                                for fact in b3[mp.FIELD_LABELS[name]]] for name in mp.FIELDS}
            self.assertEqual(record["fields"], recovered)
            for fact in mp.content_ledger(record["fields"]):
                self.assertIn(fact["value"], chosen["B2"]["messages"]["user"])
        self.assertEqual(len(audit), 2)

    def test_human_record_is_rejected(self):
        self.records[0]["record_origin"] = "human_reviewed"
        with self.assertRaisesRegex(ValueError, "Human/fixture"):
            self.generate()

    def test_support_drift_rejected(self):
        self.supports[0]["context"] = "changed"
        with self.assertRaisesRegex(ValueError, "different support"):
            self.generate()

    def test_missing_extraction_remains_failure(self):
        reqs, failures, audit = self.generate([])
        self.assertEqual(len(reqs), 2)
        self.assertEqual(len(failures), 4)
        with tempfile.TemporaryDirectory() as tmp:
            mp.export_evaluation(reqs, mock_generation_responses(reqs), failures, self.scenarios,
                                 self.families, self.supports, self.cfg, tmp)
            key = mp.read_jsonl(Path(tmp) / "PRIVATE_key.jsonl")
            self.assertEqual(len(key), 6)
            self.assertEqual(sum(x["status"] == "extraction_failed" for x in key), 4)

    def test_prompt_contents_change_request_id_at_same_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prompt.txt"
            cfg = dict(self.cfg, generate_prompt=str(path))
            path.write_text("one", encoding="utf-8")
            first = mp.generation_requests(self.families, self.supports, self.scenarios, self.records, cfg)[0]
            path.write_text("two", encoding="utf-8")
            second = mp.generation_requests(self.families, self.supports, self.scenarios, self.records, cfg)[0]
            self.assertNotEqual({r["request_id"] for r in first}, {r["request_id"] for r in second})

    def test_export_rejects_scenario_drift(self):
        reqs, failures, _ = self.generate()
        self.scenarios[0]["situation"] = "changed after request"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "changed after"):
                mp.export_evaluation(reqs, mock_generation_responses(reqs), failures, self.scenarios,
                                     self.families, self.supports, self.cfg, tmp)

    def test_fixture_cannot_be_real_data(self):
        cfg = dict(self.cfg, mode="pilot", record_origin="automatic")
        with self.assertRaisesRegex(ValueError, "observed_web"):
            mp.validate_dataset(self.families, self.supports, cfg)

    def test_export_rejects_mode_mismatch(self):
        reqs, failures, _ = self.generate()
        reqs[0]["mode"] = "pilot"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "mode"):
                mp.export_evaluation(reqs, mock_generation_responses(reqs), failures, self.scenarios,
                                     self.families, self.supports, self.cfg, tmp)

    def test_missing_response_is_logged(self):
        records, failures = mp.import_extractions(self.extracts, self.responses[:1], self.cfg)
        self.assertEqual(len(records), 1)
        self.assertEqual(failures[0]["reason"], "response_missing")

    def test_invalid_record_is_logged_without_human_repair(self):
        self.responses[0]["output_text"] = '{"bad": 1}'
        records, failures = mp.import_extractions(self.extracts, self.responses, self.cfg)
        self.assertEqual(len(records), 1)
        self.assertIn("invalid_record", failures[0]["reason"])

    def test_rejects_duplicate_scenario(self):
        self.scenarios.append(self.scenarios[0])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.generate()

    def test_no_invented_tokens_or_execution(self):
        for req in self.extracts + self.generate()[0]:
            self.assertIsNone(req["input_tokens"])
            self.assertFalse(req["execution_ready"])

    def test_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            mp.write_json(path, {"original": True})
            with self.assertRaisesRegex(ValueError, "overwrite"):
                mp.write_json(path, {"original": False})

    def test_family_weighting_and_lineage_resampling(self):
        keys, ratings = [], []
        for family, lineage, high in (("f1", "g1", True), ("f2", "g1", True), ("f3", "g2", False)):
            for cond in ("B1", "B3"):
                item = family + cond
                keys.append({"item_id": item, "condition": cond, "family_id": family,
                             "lineage_group_id": lineage, "scenario_id": family, "repeat": 0, "status": "ok"})
                score = 5 if (cond == "B3") == high else 1
                ratings.append({"item_id": item, "rater_id": "SYNTHETIC_TEST",
                                "situation_fit": score, "identity_preservation": score})
        report = mp.summarize_ratings(ratings, keys, n_boot=100)
        result = report["contrasts"]["situation_fit:B3-B1"]
        self.assertAlmostEqual(result["equal_family_weighted_difference"], 4 / 3)
        self.assertEqual(result["lineage_clusters"], 2)
        self.assertFalse(result["confirmatory_test"])

    def test_renderer_contents_change_request_id(self):
        from unittest.mock import patch
        first = self.generate()[0]
        original = mp.render_narrative
        with patch.object(mp, "render_narrative", side_effect=lambda f: original(f) + " 추가 형식"):
            second = self.generate()[0]
        b2_first = {r["request_id"] for r in first if r["condition"] == "B2"}
        b2_second = {r["request_id"] for r in second if r["condition"] == "B2"}
        self.assertNotEqual(b2_first, b2_second)

    def test_incomplete_plan_is_rejected(self):
        reqs, _, _ = self.generate()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "experiment grid"):
                mp.export_evaluation(reqs[:1], mock_generation_responses(reqs[:1]), [], self.scenarios,
                                     self.families, self.supports, self.cfg, tmp)

    def test_duplicate_logical_cell_is_rejected(self):
        reqs, failures, _ = self.generate()
        duplicate = dict(reqs[0], request_id="duplicate-test-only")
        reqs.append(duplicate)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "logical experiment"):
                mp.export_evaluation(reqs, [], failures, self.scenarios,
                                     self.families, self.supports, self.cfg, tmp)

    def test_export_rejects_lineage_drift(self):
        reqs, failures, _ = self.generate()
        reqs[0]["lineage_group_id"] = "wrong_group"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "lineage"):
                mp.export_evaluation(reqs, [], failures, self.scenarios,
                                     self.families, self.supports, self.cfg, tmp)

    def test_third_condition_only_is_not_partially_rated_pair(self):
        keys = [{"item_id": c, "condition": c, "family_id": "TEST",
                 "lineage_group_id": "TEST", "scenario_id": "TEST", "repeat": 0, "status": "ok"}
                for c in ("B1", "B2", "B3")]
        rows = [{"item_id": "B2", "rater_id": "SYNTHETIC_TEST", "situation_fit": 3}]
        result = mp.summarize_ratings(rows, keys, n_boot=100)["contrasts"]["situation_fit:B3-B1"]
        self.assertEqual(result["planned_pairs"], 1)
        self.assertEqual(result["partially_rated_pairs"], 0)
        self.assertEqual(result["completely_unrated_pairs"], 1)

    def test_main_requires_readiness_and_locked_queries(self):
        # In-memory schema fixtures, not observed data or real source claims.
        cfg = dict(self.cfg, mode="main", record_origin="automatic", model_id="SCHEMA_TEST_ONLY")
        for s in self.supports:
            s.update(provenance="observed_web", verification_status="text_verified",
                     source_url="https://example.invalid/schema-fixture",
                     verification_scope="in-memory schema test, not an actual source")
        with self.assertRaisesRegex(ValueError, "collection audit"):
            mp.validate_dataset(self.families, self.supports, cfg)
        for f in self.families:
            f.update(eligibility="main_ready", independence_reviewed=True, split="test")
        for q in self.scenarios:
            q.update(split="test", independent_authorship_reviewed=True,
                     source_support_rephrase=False, locked=False)
        with self.assertRaisesRegex(ValueError, "must be locked"):
            mp.generation_requests(self.families, self.supports, self.scenarios, [], cfg)

    def test_response_import_requires_model_selection(self):
        cfg = dict(self.cfg, model_id=None)
        responses = copy.deepcopy(self.responses)
        for r in responses:
            r["model_id"] = None
        with self.assertRaisesRegex(ValueError, "Set exact model"):
            mp.import_extractions(self.extracts, responses, cfg)


if __name__ == "__main__":
    unittest.main()
