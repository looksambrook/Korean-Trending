"""TEST_ONLY local registry checks: no research data, API calls, or human ratings."""
import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from research_log import DuplicateRunError, ResearchLog, fingerprint, hash_file, read_json


def test_only_spec():
    return {
        "objective": "TEST_ONLY registry behavior",
        "stage": "offline_validation",
        "parameters": {"seed": 7, "purpose": "TEST_ONLY", "limits": {"max_tokens": 30, "items": 2}},
        "data_snapshots": [{"id": "TEST_ONLY_b", "sha256": "b" * 64}, {"id": "TEST_ONLY_a", "sha256": "a" * 64}],
        "code_hashes": {"TEST_ONLY_code": "c" * 64},
        "prompt_hashes": {"TEST_ONLY_prompt": "d" * 64},
        "config_hashes": {"TEST_ONLY_config": "e" * 64},
        "modality": ["text", "metadata"],
        "provenance": "ai_synthetic",
        "time_cutoff": "2026-09-15T00:00:00Z",
        "model_version": "TEST_ONLY_no_model_called",
    }


class ResearchLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TEST_ONLY_research_log_")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = self.base / "TEST_ONLY.sqlite3"
        self.log = ResearchLog(self.db)
        self.spec = test_only_spec()

    def counts(self):
        with closing(sqlite3.connect(self.db)) as con:
            return tuple(con.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in ("runs", "events"))

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "src/research_log.py"), "--db", str(self.db), *args],
                              text=True, capture_output=True, timeout=30)

    def test_semantic_canonicalization(self):
        equivalent = dict(reversed(list(self.spec.items())))
        equivalent["parameters"] = {"limits": {"items": 2.0, "max_tokens": 30}, "purpose": "TEST_ONLY", "seed": 7.0}
        equivalent["data_snapshots"] = list(reversed(self.spec["data_snapshots"]))
        equivalent["modality"] = ["metadata", "text", "text"]
        equivalent["time_cutoff"] = "2026-09-15T09:00:00+09:00"
        equivalent["code_hashes"] = {"TEST_ONLY_code": "C" * 64}
        self.assertEqual(fingerprint(self.spec), fingerprint(equivalent))
        self.assertEqual(fingerprint({**self.spec, "model_version": None}),
                         fingerprint({key: value for key, value in self.spec.items() if key != "model_version"}))
        self.assertEqual(self.spec["modality"], ["text", "metadata"])

    def test_identity_covers_semantic_inputs(self):
        changes = [
            ("objective", "TEST_ONLY different objective"), ("stage", "desk_research"),
            ("parameters", {"seed": 8}), ("data_snapshots", [{"id": "TEST_ONLY_a", "sha256": "f" * 64}]),
            ("code_hashes", {"TEST_ONLY_code": "f" * 64}), ("prompt_hashes", {}), ("config_hashes", {}),
            ("modality", ["audio"]), ("provenance", "researcher_authored"),
            ("time_cutoff", "2026-09-14T00:00:00Z"), ("model_version", "TEST_ONLY_other"),
        ]
        for field, value in changes:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.spec)
                changed[field] = value
                self.assertNotEqual(fingerprint(self.spec), fingerprint(changed))

    def test_duplicate_blocks_every_state_and_restart(self):
        for index, state in enumerate(["started", "succeeded", "failed", "cancelled"]):
            spec = {**self.spec, "parameters": {"TEST_ONLY_case": index}}
            run = self.log.begin(spec)
            if state != "started":
                self.log.finish(run["run_id"], status=state, notes="TEST_ONLY transition; no experiment")
            with self.subTest(state=state), self.assertRaises(DuplicateRunError) as failure:
                ResearchLog(self.db).begin(spec)
            self.assertEqual(failure.exception.existing["run_id"], run["run_id"])
            self.assertTrue(self.log.check(spec)["duplicate"])
        self.assertEqual(len(self.log.list(status="started")), 1)

    def test_retry_requires_terminal_latest_run_and_reason(self):
        first = self.log.begin(self.spec)
        with self.assertRaisesRegex(ValueError, "still started"):
            self.log.begin(self.spec, retry_of=first["run_id"], retry_reason="TEST_ONLY explicit repeat")
        self.log.finish(first["run_id"], status="failed", notes="TEST_ONLY known simulated failure")
        with self.assertRaises(ValueError):
            self.log.begin(self.spec, retry_of=first["run_id"])
        with self.assertRaises(ValueError):
            self.log.begin(self.spec, retry_of=first["run_id"], retry_reason=" ")
        retry = self.log.begin(self.spec, retry_of=first["run_id"], retry_reason="TEST_ONLY explicit repeat after inspected failure")
        self.assertEqual(retry["retry_of"], first["run_id"])
        self.assertNotEqual(retry["run_id"], first["run_id"])
        with self.assertRaisesRegex(ValueError, "latest"):
            self.log.begin(self.spec, retry_of=first["run_id"], retry_reason="TEST_ONLY invalid branch")
        self.log.finish(retry["run_id"], status="cancelled", notes="TEST_ONLY cancelled")
        third = self.log.begin(self.spec, retry_of=retry["run_id"], retry_reason="TEST_ONLY intended repeat")
        self.assertEqual(third["retry_of"], retry["run_id"])
        self.assertEqual(self.counts(), (3, 5))

    def test_invalid_input_creates_no_database(self):
        for name, value in [("stage", "experiment"), ("data_snapshots", [{"id": "x", "sha256": "not-a-hash"}]),
                            ("parameters", {"bad": float("nan")}), ("time_cutoff", "2026-09-15"),
                            ("modality", []), ("provenance", "unknown")]:
            with self.subTest(field=name), self.assertRaises(ValueError):
                self.log.begin({**self.spec, name: value})
            self.assertFalse(self.db.exists())
        with self.assertRaises(ValueError):
            self.log.begin({**self.spec, "code_hashes": {"x": "a" * 64, " x ": "b" * 64}})
        self.assertFalse(self.db.exists())

    def test_credentials_rejected_before_persistence(self):
        candidates = [{"api_key": "TEST_ONLY_secret"}, {"headers": {"X-Goog-Api-Key": "TEST_ONLY_secret"}},
                      {"url": "https://example.test/?key=TEST_ONLY_secret"},
                      {"description": "Bearer TEST_ONLY_fake_credential"},
                      {"url": "https://TEST_ONLY_user:TEST_ONLY_password@example.test/"}]
        for parameters in candidates:
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.log.begin({**self.spec, "parameters": parameters})
            self.assertFalse(self.db.exists())
        run = self.log.begin(self.spec)
        with self.assertRaises(ValueError):
            self.log.finish(run["run_id"], status="failed", notes="Bearer TEST_ONLY_fake_credential")
        self.assertEqual(self.counts(), (1, 1))
        self.assertNotIn(b"TEST_ONLY_fake_credential", self.db.read_bytes())

    def test_finish_artifacts_actual_cost_and_no_overwrite(self):
        artifact = self.base / "TEST_ONLY_output.txt"
        artifact.write_text("TEST_ONLY synthetic artifact, not observed research evidence", encoding="utf-8")
        run = self.log.begin(self.spec)
        result = self.log.finish(run["run_id"], status="succeeded", notes="TEST_ONLY storage test completed",
                                 artifacts=[artifact], actual_cost={"amount": 0, "currency": "USD", "human_minutes": None})
        self.assertEqual(result["finish"]["artifacts"][0]["sha256"], hash_file(artifact))
        self.assertEqual(result["finish"]["actual_cost"]["amount"], 0)
        with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
            self.log.finish(run["run_id"], status="failed", notes="TEST_ONLY invalid overwrite")
        self.assertEqual(self.log.status(run["run_id"])["status"], "succeeded")
        self.assertEqual(self.counts(), (1, 2))

    def test_invalid_finish_and_database_failure_are_atomic(self):
        run = self.log.begin(self.spec)
        for options in [{"artifacts": [self.base / "absent"]}, {"actual_cost": {"amount": -1, "currency": "USD"}},
                        {"actual_cost": {"amount": 1}}, {"actual_cost": {"amount": True, "currency": "USD"}},
                        {"artifacts": [self.db]}]:
            with self.subTest(options=options), self.assertRaises((ValueError, OSError)):
                self.log.finish(run["run_id"], status="succeeded", notes="TEST_ONLY invalid finish", **options)
            self.assertEqual(self.log.status(run["run_id"])["status"], "started")
            self.assertEqual(self.counts(), (1, 1))
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("CREATE TRIGGER TEST_ONLY_reject_finish BEFORE INSERT ON events WHEN NEW.event_type='finish' BEGIN SELECT RAISE(ABORT,'TEST_ONLY injected write failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.log.finish(run["run_id"], status="failed", notes="TEST_ONLY database failure injection")
        self.assertEqual(self.log.status(run["run_id"])["status"], "started")
        self.assertEqual(self.counts(), (1, 1))

    def test_begin_event_failure_rolls_back_registry(self):
        self.log.list()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("CREATE TRIGGER TEST_ONLY_reject_begin BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'TEST_ONLY injected event failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.log.begin(self.spec)
        self.assertEqual(self.counts(), (0, 0))

    def test_append_only_events_and_immutable_runs(self):
        run = self.log.begin(self.spec)
        self.log.finish(run["run_id"], status="failed", notes="TEST_ONLY immutable result")
        statements = ["DELETE FROM events", "UPDATE events SET event_type='begin'", "DELETE FROM runs",
                      "UPDATE runs SET status='succeeded'", "UPDATE runs SET fingerprint='changed'"]
        with closing(sqlite3.connect(self.db)) as con:
            for statement in statements:
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    con.execute(statement)
        self.assertEqual(self.counts(), (1, 2))

    def test_jsonl_is_non_destructive_export(self):
        run = self.log.begin(self.spec)
        self.log.finish(run["run_id"], status="cancelled", notes="TEST_ONLY export check")
        output = self.base / "TEST_ONLY_events.jsonl"
        result = self.log.export(output)
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(result["events"], 2)
        self.assertEqual([row["event_type"] for row in rows], ["begin", "finish"])
        self.assertIsNone(rows[1]["payload"]["actual_cost"])
        self.assertEqual(rows[0]["payload"]["spec"]["provenance"], "ai_synthetic")
        with self.assertRaises(FileExistsError):
            self.log.export(output)
        with self.assertRaises(ValueError):
            self.log.export(self.db)
        output.write_text("TEST_ONLY altered export", encoding="utf-8")
        self.assertEqual(self.log.status(run["run_id"])["status"], "cancelled")

    def test_cli_cross_process_concurrent_begin_only_one_winner(self):
        spec_path = self.base / "TEST_ONLY_spec.json"
        spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        gate = self.base / "TEST_ONLY_start_gate"
        ready = [self.base / f"TEST_ONLY_ready_{index}" for index in range(6)]
        worker = ("import sys,time\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n"
                  "from research_log import main\nPath(sys.argv[2]).write_text('TEST_ONLY ready')\n"
                  "while not Path(sys.argv[3]).exists(): time.sleep(0.01)\n"
                  "raise SystemExit(main(sys.argv[4:]))\n")
        processes = [subprocess.Popen([sys.executable, "-c", worker, str(ROOT / "src"), str(path), str(gate),
                                       "--db", str(self.db), "begin", "--spec", str(spec_path)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for path in ready]
        try:
            deadline = time.monotonic() + 20
            while not all(path.exists() for path in ready) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(all(path.exists() for path in ready), "All six independent workers must be ready")
            gate.write_text("TEST_ONLY start together", encoding="utf-8")
            outcomes = [(process.communicate(timeout=45), process.returncode) for process in processes]
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        self.assertEqual(sorted(code for _, code in outcomes), [0, 3, 3, 3, 3, 3], outcomes)
        self.assertEqual(self.counts(), (1, 1))
        checked = self.cli("check", "--spec", str(spec_path))
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertTrue(json.loads(checked.stdout)["duplicate"])
        listed = json.loads(self.cli("list", "--status", "started").stdout)
        finished = self.cli("finish", "--run-id", listed[0]["run_id"], "--status", "cancelled", "--notes", "TEST_ONLY CLI check")
        self.assertEqual(finished.returncode, 0, finished.stderr)
        result = json.loads(self.cli("status", "--run-id", listed[0]["run_id"]).stdout)
        self.assertEqual(result["status"], "cancelled")

    def test_duplicate_json_keys_are_not_silently_accepted(self):
        path = self.base / "TEST_ONLY_duplicate.json"
        path.write_text('{"objective":"TEST_ONLY one","objective":"TEST_ONLY two"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            read_json(path)


if __name__ == "__main__":
    unittest.main()
