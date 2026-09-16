"""TEST_ONLY synthetic UI/storage checks; no human ratings or external calls."""
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import evaluation_store as es


def test_only_bundle():
    permutations = [(1, 2, 3), (2, 3, 1), (3, 1, 2), (1, 3, 2), (2, 1, 3), (3, 2, 1)]
    slots = ["TEST_ONLY_R" + str(n) for n in range(1, 7)]
    items = []
    for family in range(1, 4):
        for scenario in range(1, 3):
            for condition in range(1, 4):
                family_id = "TEST_ONLY_family_" + str(family)
                items.append({
                    "item_id": "TEST_ONLY_item_" + str(family) + str(scenario) + str(condition),
                    "family_id": family_id, "condition": "B" + str(condition),
                    "scenario_id": "TEST_ONLY_scenario_" + str(family) + str(scenario),
                    "repeat": 0, "status": "ok", "situation": "TEST_ONLY situation " + str(family) + str(scenario),
                    "output_text": "TEST_ONLY synthetic output " + str(family) + str(scenario) + str(condition),
                    "reference": {"family_id": family_id, "canonical_text": "TEST_ONLY_HIDDEN_CANONICAL_" + str(family),
                                  "supports": [{"support_id": "TEST_ONLY_support_" + str(family),
                                                "text": "TEST_ONLY synthetic support",
                                                "context": "TEST_ONLY not collected research data"}]}})
    assignments = []
    for slot, permutation in zip(slots, permutations):
        for family, condition in enumerate(permutation, 1):
            for scenario in range(1, 3):
                assignments.append({"slot_id": slot,
                                    "item_id": "TEST_ONLY_item_" + str(family) + str(scenario) + str(condition)})
    return {"study_id": "TEST_ONLY_preview", "mode": "preview", "seed": 20260911,
            "authorization": {"approved": False, "approval_reference": None,
                              "institutional_procedure_confirmed": False},
            "slots": slots, "items": items, "assignments": assignments}


def answers(phase):
    if phase == "A":
        return {"situation_fit": 3, "naturalness": 4, "willingness_to_use": 2}
    return {"identity_preservation": 3, "prior_familiarity": "unknown"}


class EvaluationStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TEST_ONLY_evaluation_")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = self.base / "TEST_ONLY_study.sqlite3"
        self.bundle = test_only_bundle()

    def create(self):
        self.tokens = es.create_study(self.db, self.bundle)
        return self.tokens[0]["token"]

    def complete(self, token):
        state = es.next_item(self.db, token)
        while state["phase"] != "complete":
            state = es.submit_rating(self.db, token, state["item"]["item_id"], state["phase"], answers(state["phase"]))
        return state

    def test_all_a_precedes_b_and_preview_exports_never_lose_fixture_labels(self):
        token = self.create()
        state = es.next_item(self.db, token)
        seen_a, seen_b = set(), set()
        self.assertEqual(state["total"], 12)
        for index in range(12):
            phase = "A" if index < 6 else "B"
            self.assertEqual(state["phase"], phase)
            self.assertEqual(state["completed"], index)
            self.assertEqual(state["study_mode"], "preview")
            item = state["item"]
            self.assertNotIn("condition", item)
            self.assertNotIn("scenario_id", item)
            self.assertNotIn("family_id", item)
            if phase == "A":
                self.assertEqual(set(item), {"item_id", "situation", "output_text"})
                self.assertNotIn("HIDDEN_CANONICAL", json.dumps(state))
                seen_a.add(item["item_id"])
            else:
                self.assertIn("reference", item)
                seen_b.add(item["item_id"])
            state = es.submit_rating(self.db, token, item["item_id"], phase, answers(phase))
        self.assertEqual(state, {"study_mode": "preview", "phase": "complete", "item": None,
                                 "completed": 12, "total": 12})
        self.assertEqual(seen_a, seen_b)
        for slot in self.tokens[1:]:
            self.assertEqual(self.complete(slot["token"])["completed"], 12)
        rows = es.export_rows(self.db)
        self.assertEqual(len(rows), 36)
        self.assertTrue(all(r["study_mode"] == "preview" and r["provenance"] == "ui_fixture" for r in rows))
        self.assertTrue(all(r["evaluation_status"] == "complete" for r in rows))

    def test_pilot_needs_all_confirmations_but_preview_does_not(self):
        for index, authorization in enumerate([
                {"approved": False, "approval_reference": "TEST_ONLY", "institutional_procedure_confirmed": True},
                {"approved": True, "approval_reference": "", "institutional_procedure_confirmed": True},
                {"approved": True, "approval_reference": "TEST_ONLY", "institutional_procedure_confirmed": False}]):
            with self.subTest(authorization=authorization):
                bundle = dict(self.bundle, mode="pilot", authorization=authorization)
                path = self.base / ("TEST_ONLY_rejected_" + str(index) + ".sqlite3")
                with self.assertRaisesRegex(ValueError, "scope and institutional"):
                    es.create_study(path, bundle)
                self.assertFalse(path.exists())
        self.bundle.update(mode="pilot", authorization={"approved": True, "approval_reference": "TEST_ONLY_simulated_approval",
                                                        "institutional_procedure_confirmed": True})
        token = self.create()
        self.assertEqual(es.next_item(self.db, token)["study_mode"], "pilot")
        self.assertTrue(all(r["study_mode"] == "pilot" for r in es.export_rows(self.db)))

    def test_duplicate_and_cross_condition_assignments_are_rejected(self):
        mutations = []
        duplicate_id = copy.deepcopy(self.bundle)
        duplicate_id["items"].append(copy.deepcopy(duplicate_id["items"][0]))
        mutations.append(duplicate_id)
        duplicate_assignment = copy.deepcopy(self.bundle)
        duplicate_assignment["assignments"].append(copy.deepcopy(duplicate_assignment["assignments"][0]))
        mutations.append(duplicate_assignment)
        cross_condition = copy.deepcopy(self.bundle)
        cross_condition["assignments"].append({"slot_id": self.bundle["slots"][0], "item_id": "TEST_ONLY_item_112"})
        mutations.append(cross_condition)
        for index, bundle in enumerate(mutations):
            with self.subTest(index=index):
                path = self.base / ("TEST_ONLY_invalid_" + str(index) + ".sqlite3")
                with self.assertRaises(ValueError):
                    es.create_study(path, bundle)
                self.assertFalse(path.exists())

    def test_other_evaluators_future_phases_and_replays_cannot_be_submitted(self):
        token = self.create()
        state = es.next_item(self.db, token)
        current = state["item"]["item_id"]
        own = {a["item_id"] for a in self.bundle["assignments"] if a["slot_id"] == self.tokens[0]["slot_id"]}
        other = next(i["item_id"] for i in self.bundle["items"] if i["item_id"] not in own)
        future = next(item for item in own if item != current)
        for item_id, phase in ((other, "A"), (future, "A"), (current, "B")):
            with self.subTest(item=item_id, phase=phase):
                with self.assertRaisesRegex(ValueError, "current assigned"):
                    es.submit_rating(self.db, token, item_id, phase, answers(phase))
        with self.assertRaisesRegex(ValueError, "token"):
            es.next_item(self.db, "TEST_ONLY_invalid_token")
        state = es.submit_rating(self.db, token, current, "A", answers("A"))
        with self.assertRaisesRegex(ValueError, "already submitted"):
            es.submit_rating(self.db, token, current, "A", answers("A"))
        self.assertEqual(es.next_item(self.db, token)["completed"], 1)

    def test_scores_missing_reasons_and_comments_are_strict(self):
        token = self.create()
        state = es.next_item(self.db, token)
        item_id = state["item"]["item_id"]
        variants = [dict(answers("A"), situation_fit=value) for value in (True, 2.0, 0, 6, None)]
        variants += [dict(answers("A"), situation_fit_missing_reason="skip"),
                     dict(answers("A"), situation_fit=None, situation_fit_missing_reason=["skip"]),
                     dict(answers("A"), comment="X" * 1001), dict(answers("A"), condition="B1"),
                     {"situation_fit": 3}]
        for values in variants:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    es.submit_rating(self.db, token, item_id, "A", values)
                self.assertEqual(es.next_item(self.db, token)["completed"], 0)
        values = dict(answers("A"), situation_fit=None, situation_fit_missing_reason="cannot_judge", comment="가" * 1000)
        state = es.submit_rating(self.db, token, item_id, "A", values)
        row = next(r for r in es.export_rows(self.db) if r["slot_id"] == self.tokens[0]["slot_id"] and r["item_id"] == item_id)
        self.assertIsNone(row["situation_fit"])
        self.assertEqual(row["situation_fit_missing_reason"], "cannot_judge")
        self.assertEqual(row["evaluation_status"], "A_only")
        self.assertIsNone(row["identity_preservation"])
        while state["phase"] == "A":
            state = es.submit_rating(self.db, token, state["item"]["item_id"], "A", answers("A"))
        with self.assertRaisesRegex(ValueError, "familiarity"):
            es.submit_rating(self.db, token, state["item"]["item_id"], "B",
                             {"identity_preservation": 3, "prior_familiarity": "invalid"})
        with self.assertRaisesRegex(ValueError, "phase"):
            es.submit_rating(self.db, token, state["item"]["item_id"], ["B"], answers("B"))

    def test_failed_outputs_remain_in_plan_and_are_never_shown(self):
        failed_id = "TEST_ONLY_item_111"
        next(i for i in self.bundle["items"] if i["item_id"] == failed_id).update(status="extraction_failed", output_text="")
        token = self.create()
        state = es.next_item(self.db, token)
        self.assertEqual(state["total"], 10)
        while state["phase"] != "complete":
            self.assertNotEqual(state["item"]["item_id"], failed_id)
            state = es.submit_rating(self.db, token, state["item"]["item_id"], state["phase"], answers(state["phase"]))
        rows = es.export_rows(self.db)
        self.assertEqual(len(rows), 36)
        failed = [r for r in rows if r["item_id"] == failed_id]
        self.assertEqual(len(failed), 2)
        self.assertTrue(all(r["evaluation_status"] == "generation_failure" and r["shown_at_A"] is None for r in failed))

    def test_failed_export_without_display_material_preserves_all_assignments(self):
        failed_id = "TEST_ONLY_item_111"
        next(i for i in self.bundle["items"] if i["item_id"] == failed_id).update(
            status="missing", output_text="", situation="", reference=None)
        token = self.create()
        state = es.next_item(self.db, token)
        self.assertEqual(state["total"], 10)
        while state["phase"] != "complete":
            self.assertNotEqual(state["item"]["item_id"], failed_id)
            state = es.submit_rating(self.db, token, state["item"]["item_id"], state["phase"], answers(state["phase"]))
        rows = es.export_rows(self.db)
        self.assertEqual(len(rows), 36)
        failed = [row for row in rows if row["item_id"] == failed_id]
        self.assertEqual(len(failed), 2)
        self.assertTrue(all(row["status"] == "missing" and row["evaluation_status"] == "generation_failure"
                            and row["shown_at_A"] is None and row["shown_at_B"] is None for row in failed))
        bad_bundle = copy.deepcopy(self.bundle)
        bad_bundle["items"][0]["output_text"] = "TEST_ONLY error text must never be displayed as a result"
        with self.assertRaisesRegex(ValueError, "Failed outputs"):
            es.create_study(self.base / "TEST_ONLY_rejected_failure.sqlite3", bad_bundle)

    def test_concurrent_duplicate_submissions_commit_only_once(self):
        token = self.create()
        item_id = es.next_item(self.db, token)["item"]["item_id"]

        def submit():
            try:
                return es.submit_rating(self.db, token, item_id, "A", answers("A"))
            except ValueError:
                return "TEST_ONLY_rejected_duplicate"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda unused: submit(), range(2)))
        self.assertEqual(sum(isinstance(result, dict) for result in outcomes), 1)
        self.assertEqual(es.next_item(self.db, token)["completed"], 1)
        connection = sqlite3.connect(self.db)
        try:
            self.assertEqual(connection.execute("SELECT count(*) FROM ratings").fetchone()[0], 1)
        finally:
            connection.close()

    def test_first_presentation_timestamp_survives_refresh(self):
        token = self.create()
        with patch.object(es, "_now", return_value="TEST_ONLY_first_presentation"):
            item_id = es.next_item(self.db, token)["item"]["item_id"]
        with patch.object(es, "_now", return_value="TEST_ONLY_refresh"):
            es.next_item(self.db, token)
        with patch.object(es, "_now", return_value="TEST_ONLY_submission"):
            es.submit_rating(self.db, token, item_id, "A", answers("A"))
        row = next(r for r in es.export_rows(self.db) if r["slot_id"] == self.tokens[0]["slot_id"] and r["item_id"] == item_id)
        self.assertEqual(row["shown_at_A"], "TEST_ONLY_first_presentation")
        self.assertEqual(row["submitted_at_A"], "TEST_ONLY_submission")
        self.assertIsNone(row["shown_at_B"])

    def test_tokens_are_only_hashed_and_existing_database_is_preserved(self):
        self.create()
        content = self.db.read_bytes()
        for slot in self.tokens:
            self.assertNotIn(slot["token"].encode(), content)
        with self.assertRaisesRegex(ValueError, "overwrite"):
            es.create_study(self.db, self.bundle)
        self.assertEqual(content, self.db.read_bytes())
        missing = self.base / "TEST_ONLY_missing.sqlite3"
        with self.assertRaises(ValueError):
            es.next_item(missing, "TEST_ONLY_token")
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
