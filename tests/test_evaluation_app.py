"""TEST_ONLY loopback integration checks. All scores are synthetic software fixtures."""
import copy
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import evaluation_app as app
import evaluation_store as es
import meme_pipeline as mp


def values(phase):
    if phase == "A":
        return {"situation_fit": 3, "naturalness": None,
                "naturalness_missing_reason": "cannot_judge", "willingness_to_use": 4}
    return {"identity_preservation": None, "identity_preservation_missing_reason": "skip",
            "prior_familiarity": "unsure"}


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TEST_ONLY_http_")
        self.directory = Path(self.tmp.name) / "preview"
        self.db = app.initialize(self.directory, app.preview_bundle())
        self.tokens = mp.read_json(self.directory / "access_codes.PRIVATE.json")
        self.server = app.make_server(self.db, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def request(self, method, path, data=None, cookie=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        body = None if data is None else json.dumps(data).encode()
        h = {"Content-Type": "application/json"} if body is not None else {}
        if cookie:
            h["Cookie"] = cookie
        h.update(headers or {})
        conn.request(method, path, body, h)
        response = conn.getresponse()
        content = response.read()
        result = (response.status, dict(response.getheaders()), content)
        conn.close()
        return result

    def login(self):
        status, headers, body = self.request("POST", "/api/session", {"token": self.tokens[0]["token"]})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        return cookie.split(";", 1)[0], json.loads(body)

    def test_static_assets_and_private_paths(self):
        for path in ("/", "/app.js", "/style.css"):
            status, headers, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertTrue(body)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for path in ("/study.sqlite3", "/access_codes.PRIVATE.json", "/../evaluation_store.py", "/api/items"):
            self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.request("GET", "/api/next")[0], 403)

    def test_session_resumes_without_disclosing_reference_or_condition(self):
        cookie, state = self.login()
        self.assertEqual(state["study_mode"], "preview")
        self.assertEqual(set(state["item"]), {"item_id", "situation", "output_text"})
        self.assertEqual(json.loads(self.request("GET", "/api/next", cookie=cookie)[2]), state)
        self.assertNotIn("condition", json.dumps(state))
        self.assertNotIn("reference", json.dumps(state))
        self.assertEqual(self.request("POST", "/api/session", {"token": "TEST_ONLY_invalid"})[0], 400)

    def test_wrong_phase_and_foreign_assignment_rejected(self):
        cookie, state = self.login()
        early = {"item_id": state["item"]["item_id"], "phase": "B", "values": values("B")}
        self.assertEqual(self.request("POST", "/api/rating", early, cookie)[0], 400)
        own = {r["item_id"] for r in app.preview_bundle()["assignments"] if r["slot_id"] == self.tokens[0]["slot_id"]}
        foreign = next(i["item_id"] for i in app.preview_bundle()["items"] if i["item_id"] not in own)
        self.assertEqual(self.request("POST", "/api/rating", {"item_id": foreign, "phase": "A", "values": values("A")}, cookie)[0], 400)
        self.assertEqual(json.loads(self.request("GET", "/api/next", cookie=cookie)[2])["completed"], 0)

    def test_complete_flow_locks_scores_and_keeps_preview_provenance(self):
        cookie, state = self.login()
        for n in range(12):
            phase = "A" if n < 6 else "B"
            self.assertEqual(state["phase"], phase)
            self.assertEqual("reference" in state["item"], phase == "B")
            payload = {"item_id": state["item"]["item_id"], "phase": phase, "values": values(phase)}
            status, _, body = self.request("POST", "/api/rating", payload, cookie)
            self.assertEqual(status, 200)
            self.assertEqual(self.request("POST", "/api/rating", payload, cookie)[0], 400)
            state = json.loads(body)
        self.assertEqual(state["phase"], "complete")
        rows = es.export_rows(self.db)
        self.assertEqual(len(rows), 36)
        self.assertEqual(sum(r["evaluation_status"] == "complete" for r in rows), 6)
        self.assertTrue(all(r["provenance"] == "ui_fixture" and r["study_mode"] == "preview" for r in rows))

    def test_cross_origin_and_invalid_content_rejected(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "example.invalid"})[0], 403)
        self.assertEqual(self.request("POST", "/api/session", {"token": self.tokens[0]["token"]}, headers={"Origin": "https://example.invalid"})[0], 400)
        self.assertEqual(self.request("POST", "/api/session", {}, headers={"Content-Type": "text/plain"})[0], 400)


class ExportBindingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TEST_ONLY_export_")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        bundle = app.preview_bundle()
        self.plan = {"rater_slots": [{"planning_slot_id": s} for s in bundle["slots"]], "assignments": []}
        indexed = {i["item_id"]: i for i in bundle["items"]}
        for assignment in bundle["assignments"]:
            item = indexed[assignment["item_id"]]
            self.plan["assignments"].append({"planning_slot_id": assignment["slot_id"], **{k: item[k] for k in ("family_id", "scenario_id", "condition", "repeat")}})
        self.key = [{k: i[k] for k in ("item_id", "family_id", "scenario_id", "condition", "repeat", "status")} for i in bundle["items"]]
        self.a = [{k: i[k] for k in ("item_id", "situation", "output_text")} for i in bundle["items"]]
        self.b = [{k: i[k] for k in ("item_id", "output_text", "reference")} for i in bundle["items"]]
        # Only a temporary TEST_ONLY contract fixture; never persisted as research/model evidence.
        self.notes = {"mode": "pilot", "expected_output_origin": "automatic", "imported_ok_fixture_responses": 0, "imported_ok_model_responses": 18}
        self.authorization = {"study_id": "TEST_ONLY_contract", "approved": False,
                              "approval_reference": None, "institutional_procedure_confirmed": False}
        self.write()

    def write(self):
        # These are mutable temporary TEST_ONLY inputs, not preserved research artifacts.
        (self.directory / "export_notes.json").write_text(json.dumps(self.notes), encoding="utf-8")
        for name, rows in (("PRIVATE_key", self.key), ("stage_a", self.a), ("stage_b", self.b)):
            (self.directory / (name + ".jsonl")).write_text("".join(json.dumps(r)+"\n" for r in rows), encoding="utf-8")

    def build(self):
        return app.bundle_from_export(self.directory, self.plan, self.authorization)

    def test_complete_binding_retains_key_and_real_init_requires_authorization(self):
        bundle = self.build()
        self.assertEqual(len(bundle["assignments"]), 36)
        self.assertEqual(bundle["source_condition_key"], self.key)
        with self.assertRaises(ValueError):
            es.create_study(self.directory / "TEST_ONLY_no_approval.sqlite3", bundle)

    def test_missing_condition_and_modified_b_output_rejected(self):
        original_plan = copy.deepcopy(self.plan)
        self.plan["assignments"] = [a for a in self.plan["assignments"] if a["condition"] != "B3"]
        with self.assertRaises(ValueError):
            self.build()
        self.plan = original_plan
        self.b[0]["output_text"] = "TEST_ONLY changed"
        self.write()
        with self.assertRaises(ValueError):
            self.build()

    def test_fixture_origin_cannot_be_loaded_as_research_study(self):
        self.notes["imported_ok_fixture_responses"] = 1
        self.write()
        with self.assertRaises(ValueError):
            self.build()

    def test_main_experiment_cannot_be_mislabeled_as_development_pilot(self):
        self.notes["mode"] = "main"
        self.write()
        with self.assertRaises(ValueError):
            self.build()

    def test_generation_failure_stays_in_planned_denominator(self):
        failed = self.key[0]["item_id"]
        self.key[0]["status"] = "TEST_ONLY_extraction_failed"
        self.a = [r for r in self.a if r["item_id"] != failed]
        self.b = [r for r in self.b if r["item_id"] != failed]
        self.notes["imported_ok_model_responses"] = 17
        self.write()
        bundle = self.build()
        bundle["mode"] = "preview"  # A software check, never human/model evidence.
        db = self.directory / "TEST_ONLY_failure.sqlite3"
        es.create_study(db, bundle)
        rows = es.export_rows(db)
        self.assertEqual(len(rows), 36)
        self.assertEqual(sum(r["evaluation_status"] == "generation_failure" for r in rows), 2)


if __name__ == "__main__":
    unittest.main()
