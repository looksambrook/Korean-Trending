"""Offline executor regression tests; every provider response is TEST_ONLY.

No SDK or network client is used. Config and schema fixtures are constructed in
memory without reading research data; simulated outputs and budget ledgers live
exclusively in temporary TEST_ONLY paths. Production enum values such as
observed_web and text_verified simulate validation states, not actual observations.
These checks do not produce automatic research records or model-performance results.
"""
import copy
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import execute_responses as er
import meme_pipeline as mp


class TestOnlyProviderError(RuntimeError):
    status_code = 429


class FakeResponse:
    def __init__(self, raw):
        self.raw = raw
        self.output_text = raw.get("TEST_ONLY_output_text", "TEST_ONLY: no actual model response")
        self._request_id = "TEST_ONLY_provider_request"

    def model_dump(self, mode="json"):
        return copy.deepcopy(self.raw)


class FakeClient:
    """Entirely local stand-in; cannot contact an endpoint."""
    max_retries = 0
    base_url = "https://api.openai.com/v1"

    def __init__(self, *, count_error=None, create_error=None, override=None, input_tokens=100):
        self.count_error, self.create_error = count_error, create_error
        self.override, self.input_tokens = override or {}, input_tokens
        self.count_calls, self.create_calls = [], []
        self.responses = SimpleNamespace(
            input_tokens=SimpleNamespace(count=self.count), create=self.create)

    def count(self, **payload):
        self.count_calls.append(copy.deepcopy(payload))
        if self.count_error:
            raise self.count_error
        return SimpleNamespace(input_tokens=self.input_tokens)

    def create(self, **payload):
        self.create_calls.append(copy.deepcopy(payload))
        if self.create_error:
            raise self.create_error
        raw = {
            "id": "TEST_ONLY_response_" + str(len(self.create_calls)),
            "model": er.SUPPORTED_MODEL, "service_tier": "default",
            "created_at": 0, "status": "completed",
            "usage": {"input_tokens": self.input_tokens, "output_tokens": 10,
                      "total_tokens": self.input_tokens + 10,
                      "input_tokens_details": {"cached_tokens": 20}},
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": "TEST_ONLY: not a model result"}]}],
        }
        raw.update({key: payload[key] for key in
                    ("temperature", "max_output_tokens", "store", "truncation", "background")})
        raw.update(copy.deepcopy(self.override))
        return FakeResponse(raw)


class ExecuteResponsesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TEST_ONLY_executor_")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.serial = 0
        self.cfg = {
            "config_version": "TEST_ONLY", "dataset_version": "TEST_ONLY_in_memory",
            "mode": "pilot", "record_origin": "automatic",
            "model_id": er.SUPPORTED_MODEL,
            "temperature": 0.7, "max_output_tokens": 128,
            "extraction_temperature": 0, "extraction_max_output_tokens": 1600,
            "generation_repeats": 1, "seed": 17,
            "conditions": ["B1", "B2", "B3"], "allow_network": False,
            "extract_prompt": str(ROOT / "prompts/extract_v1.txt"),
            "generate_prompt": str(ROOT / "prompts/generate_v1.txt"),
            "note": "TEST_ONLY in-memory schema config; no research execution authorization",
        }
        # Three synthetic families keep batch-limit checks meaningful: a cap of
        # two must reject the whole batch before either fake client method runs.
        self.families = [
            {"family_id": "TEST_ONLY_family_" + str(index),
             "lineage_group_id": "TEST_ONLY_lineage_" + str(index),
             "canonical_text": "TEST_ONLY synthetic expression " + str(index),
             "split": "dev"}
            for index in range(3)
        ]
        # These enum values exercise production validation paths. They do not
        # claim that an actual web source was observed or verified. All content
        # is TEST_ONLY and the reserved invalid domain is never contacted.
        self.supports = [
            {"support_id": "TEST_ONLY_support_" + str(index),
             "family_id": family["family_id"],
             "source_id": "TEST_ONLY_source_" + str(index),
             "source_group_id": "TEST_ONLY_source_group_" + str(index),
             "text": "TEST_ONLY synthetic support " + str(index),
             "context": "TEST_ONLY synthetic context; no observation or source claim",
             "provenance": "observed_web", "verification_status": "text_verified",
             "source_url": "https://example.invalid/TEST_ONLY/source_" + str(index),
             "verification_scope": "TEST_ONLY simulated validation state; not source verification"}
            for index, family in enumerate(self.families)
        ]
        self.requests = mp.make_extractions(self.families, self.supports, self.cfg)
        self.scope = self.new_scope()

    def new_scope(self, scenarios=None):
        self.serial += 1
        return {
            "scope_id": "TEST_ONLY_scope_" + str(self.serial),
            "approved": True, "approval_reference": "TEST_ONLY_simulated_approval_not_research_authorization",
            "model_id": er.SUPPORTED_MODEL, "rate_card_id": er.RATE_CARD_ID,
            "rates": copy.deepcopy(er.RATE_CARD), "rates_verified": True,
            "rates_verified_on": date.today().isoformat(),
            "budget_usd": "1", "max_model_calls": 21, "max_token_count_calls": 21,
            "max_retries": 0,
            "ledger_path": str(self.base / ("TEST_ONLY_ledger_" + str(self.serial) + ".json")),
            "limits": {"extraction": {"max_input_tokens": 12000, "max_output_tokens": 4096},
                       "generation": {"max_input_tokens": 16000, "max_output_tokens": 1024}},
            "approved_family_ids": [f["family_id"] for f in self.families],
            "cleared_source_ids": sorted({s["source_id"] for s in self.supports}),
            "material_hashes": er.material_hashes(self.cfg, self.families, self.supports, scenarios),
        }

    def execute(self, client, *, scope=None, requests=None, cfg=None, ledger=None, **extra):
        self.serial += 1
        self.last_out = self.base / ("TEST_ONLY_run_" + str(self.serial))
        scope = self.scope if scope is None else scope
        return er.run_requests(
            client, self.requests if requests is None else requests, scope,
            scope["ledger_path"] if ledger is None else ledger, self.last_out,
            cfg=self.cfg if cfg is None else cfg, families=self.families,
            supports=self.supports, **extra)

    def assert_no_transmission(self, client):
        self.assertEqual(client.count_calls, [])
        self.assertEqual(client.create_calls, [])

    def test_approval_source_model_and_rates_fail_before_transmission(self):
        variants = [
            {"approved": False}, {"approval_reference": ""},
            {"cleared_source_ids": []}, {"approved_family_ids": []},
            {"model_id": "TEST_ONLY_wrong_model"},
            {"rates": dict(er.RATE_CARD, output_usd_per_million="0")},
            {"rates_verified_on": (date.today() - timedelta(days=8)).isoformat()},
            {"budget_usd": 0}, {"budget_usd": -1}, {"budget_usd": True},
            {"budget_usd": "nan"}, {"max_model_calls": True}, {"max_retries": 1},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                scope = dict(self.scope, **changes)
                client = FakeClient()
                with self.assertRaises((ValueError, RuntimeError)):
                    self.execute(client, scope=scope)
                self.assert_no_transmission(client)

    def test_success_uses_identical_input_without_seed_and_accounts_for_cache(self):
        client = FakeClient()
        path = self.execute(client)
        self.assertEqual(len(client.count_calls), len(self.requests))
        for count, create in zip(client.count_calls, client.create_calls):
            self.assertEqual(count, {key: create[key] for key in count})
            for key in ("seed", "tools", "previous_response_id", "conversation"):
                self.assertNotIn(key, create)
            self.assertEqual(create["service_tier"], "default")
            self.assertIs(create["store"], False)
        events = mp.read_jsonl(path)
        self.assertTrue(all(e["status"] == "ok" and not e["seed_sent"] for e in events))
        self.assertTrue(all(e["response_id"].startswith("TEST_ONLY_") for e in events))
        state = mp.read_json(self.scope["ledger_path"])
        # 80 ordinary + 20 cached input tokens, plus 10 output tokens.
        self.assertEqual(Fraction(state["spent_usd"]), Fraction("0.00005") * len(self.requests))
        self.assertEqual(state["reservations"], {})
        self.assertIsNone(state["count_inflight"])
        self.assertEqual(len(list((self.last_out / "raw").glob("*.response.json"))), len(self.requests))

    def test_bound_ledger_and_duplicate_requests_cannot_be_reexecuted(self):
        client = FakeClient()
        with self.assertRaisesRegex(ValueError, "Ledger path"):
            self.execute(client, ledger=self.base / "TEST_ONLY_alternate_ledger.json")
        self.assert_no_transmission(client)
        self.execute(client)
        repeat = FakeClient()
        with self.assertRaises(RuntimeError):
            self.execute(repeat)
        self.assert_no_transmission(repeat)
        self.assertTrue(mp.read_json(self.scope["ledger_path"])["blocked"])

    def test_budget_and_call_limits_stop_before_transmission(self):
        for changes in ({"budget_usd": "0.000001"}, {"max_model_calls": 2},
                        {"max_token_count_calls": 2}):
            with self.subTest(changes=changes):
                scope = self.new_scope()
                scope.update(changes)
                client = FakeClient()
                with self.assertRaises((ValueError, RuntimeError)):
                    self.execute(client, scope=scope)
                self.assert_no_transmission(client)

    def test_cumulative_call_limit_is_enforced_on_existing_ledger(self):
        scope = self.new_scope()
        scope["max_model_calls"] = len(self.requests)
        with er.DurableBudget(scope["ledger_path"], scope) as ledger:
            for index in range(scope["max_model_calls"]):
                rid = "TEST_ONLY_previous_" + str(index)
                ledger.begin_count(rid, Fraction("0.01"))
                ledger.finish_count(rid)
                ledger.reserve(rid, Fraction("0.01"))
                ledger.settle(rid, Fraction("0.001"))
        client = FakeClient()
        with self.assertRaises(RuntimeError):
            self.execute(client, scope=scope)
        self.assert_no_transmission(client)

    def test_count_failure_is_redacted_preserved_and_never_retried(self):
        sentinel = "TEST_ONLY_SECRET_KEY_AND_PRIVATE_PAYLOAD"
        client = FakeClient(count_error=TestOnlyProviderError(sentinel))
        with self.assertRaises(RuntimeError) as caught:
            self.execute(client)
        self.assertNotIn(sentinel, str(caught.exception))
        self.assertEqual(len(client.count_calls), 1)
        self.assertEqual(client.create_calls, [])
        event = mp.read_jsonl(self.last_out / "responses.jsonl")[0]
        self.assertEqual(event["error_stage"], "token_count")
        self.assertEqual(event["http_status"], 429)
        state = mp.read_json(self.scope["ledger_path"])
        self.assertTrue(state["blocked"])
        self.assertEqual(state["count_inflight"], self.requests[0]["request_id"])
        for path in self.base.rglob("*.json*"):
            self.assertNotIn(sentinel, path.read_text(encoding="utf-8"))
        retry = FakeClient()
        with self.assertRaises((ValueError, RuntimeError)):
            self.execute(retry)
        self.assert_no_transmission(retry)

    def test_create_error_retains_reservation_and_stops_batch(self):
        client = FakeClient(create_error=TestOnlyProviderError("TEST_ONLY_sensitive_failure"))
        with self.assertRaises(RuntimeError):
            self.execute(client)
        self.assertEqual(len(client.count_calls), 1)
        self.assertEqual(len(client.create_calls), 1)
        state = mp.read_json(self.scope["ledger_path"])
        self.assertTrue(state["blocked"])
        self.assertIn(self.requests[0]["request_id"], state["reservations"])
        self.assertEqual(Fraction(state["spent_usd"]), 0)
        self.assertEqual(mp.read_jsonl(self.last_out / "responses.jsonl")[0]["status"], "error")

    def test_incomplete_refusal_and_bad_provider_metadata_stop_without_retry(self):
        cases = [
            ({"status": "incomplete"}, False),
            ({"output": [{"content": [{"type": "refusal", "refusal": "TEST_ONLY"}]}]}, False),
            ({"TEST_ONLY_output_text": ""}, False),
            ({"usage": None}, True),
            ({"model": "TEST_ONLY_wrong_snapshot"}, True),
            ({"service_tier": "priority"}, True),
            ({"id": None}, True),
            ({"temperature": 1.9}, False),
            ({"usage": {"input_tokens": 101, "output_tokens": 10, "total_tokens": 111,
                        "input_tokens_details": {"cached_tokens": 20}}}, True),
        ]
        for override, unresolved in cases:
            with self.subTest(override=override):
                scope = self.new_scope()
                client = FakeClient(override=override)
                with self.assertRaises(RuntimeError):
                    self.execute(client, scope=scope)
                self.assertEqual(len(client.count_calls), 1)
                self.assertEqual(len(client.create_calls), 1)
                state = mp.read_json(scope["ledger_path"])
                self.assertTrue(state["blocked"])
                self.assertEqual(bool(state["reservations"]), unresolved)
                event = mp.read_jsonl(self.last_out / "responses.jsonl")[0]
                self.assertEqual(event["status"], "error")
                self.assertEqual(len(list((self.last_out / "raw").glob("*.response.json"))), 1)

    def test_exact_count_exceeding_cap_stops_without_model_call(self):
        client = FakeClient(input_tokens=self.scope["limits"]["extraction"]["max_input_tokens"] + 1)
        with self.assertRaises(RuntimeError):
            self.execute(client)
        self.assertEqual(len(client.count_calls), 1)
        self.assertEqual(client.create_calls, [])
        self.assertTrue(mp.read_json(self.scope["ledger_path"])["blocked"])

    def test_modified_request_or_approved_materials_are_rejected(self):
        changed = copy.deepcopy(self.requests)
        changed[0]["temperature"] = 1.9
        client = FakeClient()
        with self.assertRaises(ValueError):
            self.execute(client, requests=changed)
        self.assert_no_transmission(client)
        cfg = dict(self.cfg, extraction_temperature=1.9)
        rebuilt = mp.make_extractions(self.families, self.supports, cfg)
        with self.assertRaises(ValueError):
            self.execute(client, requests=rebuilt, cfg=cfg)
        self.assert_no_transmission(client)

    def test_extraction_cannot_receive_evaluation_material(self):
        client = FakeClient()
        with self.assertRaises(ValueError):
            self.execute(client, scenarios=[{"situation": "TEST_ONLY_SECRET_EVALUATION"}])
        self.assert_no_transmission(client)

    def generation_materials(self):
        scenarios = [{"scenario_id": "TEST_ONLY_scenario_" + f["family_id"],
                      "family_id": f["family_id"], "split": "dev", "provenance": "ai_synthetic",
                      "situation": "TEST_ONLY synthetic schema situation; not research data",
                      "independent_authorship_reviewed": True, "source_support_rephrase": False}
                     for f in self.families]
        # Test-only mock responses exist only in this function's memory.
        extraction_responses = [{
            "request_id": r["request_id"], "record_origin": "automatic", "model_id": er.SUPPORTED_MODEL,
            "response_id": "TEST_ONLY_extraction_" + r["request_id"], "created_at": "TEST_ONLY",
            "usage": None, "status": "ok", "output_text": json.dumps({key: [] for key in mp.FIELDS})}
            for r in self.requests]
        records, failures = mp.import_extractions(self.requests, extraction_responses, self.cfg)
        self.assertEqual(failures, [])
        requests, failures, _ = mp.generation_requests(self.families, self.supports, scenarios, records, self.cfg)
        self.assertEqual(failures, [])
        return scenarios, records, extraction_responses, requests

    def test_b1_extra_analysis_is_rejected_even_with_recomputed_request_id(self):
        scenarios, records, extraction_responses, requests = self.generation_materials()
        changed = next(r for r in requests if r["condition"] == "B1")
        changed["messages"]["user"] += "\nTEST_ONLY forbidden additional analysis or gold answer"
        changed["request_id"] = "generate_" + mp.stable_hash(
            {k: v for k, v in changed.items() if k != "request_id"})[:20]
        client = FakeClient()
        with self.assertRaises(ValueError):
            self.execute(client, requests=requests, scope=self.new_scope(scenarios),
                         scenarios=scenarios, records=records, extraction_responses=extraction_responses)
        self.assert_no_transmission(client)

    def test_generation_requires_original_automatic_response_and_runs_all_conditions(self):
        scenarios, records, extraction_responses, requests = self.generation_materials()
        scope = self.new_scope(scenarios)
        client = FakeClient()
        with self.assertRaisesRegex(ValueError, "extraction responses"):
            self.execute(client, requests=requests, scope=scope, scenarios=scenarios, records=records)
        modified = copy.deepcopy(records)
        modified[0]["fields"]["uncertainty_notes"] = [
            {"value": "TEST_ONLY manual edit must not become automatic output",
             "evidence_ids": [], "certainty": "tentative"}]
        with self.assertRaisesRegex(ValueError, "original automatic"):
            self.execute(client, requests=requests, scope=scope, scenarios=scenarios,
                         records=modified, extraction_responses=extraction_responses)
        self.assert_no_transmission(client)
        path = self.execute(client, requests=requests, scope=scope, scenarios=scenarios,
                            records=records, extraction_responses=extraction_responses)
        self.assertEqual({r["condition"] for r in requests}, {"B1", "B2", "B3"})
        self.assertEqual(len(client.create_calls), len(requests))
        self.assertTrue(all(row["status"] == "ok" for row in mp.read_jsonl(path)))


if __name__ == "__main__":
    unittest.main()
