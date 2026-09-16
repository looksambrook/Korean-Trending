"""Local research registry. SQLite is authoritative; JSONL is an audit export.

No collection, model call, or human evaluation is performed by this module.
Register before external work; preserve the returned run_id and finish with
observed outcomes. Completed entries and audit events cannot be overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

STAGES = {"desk_research", "access_probe", "offline_validation", "model_experiment", "human_evaluation"}
PROVENANCE = {"actual_collected", "researcher_authored", "ai_synthetic", "mixed", "not_applicable"}
TERMINAL = {"succeeded", "failed", "cancelled"}
REQUIRED = {"objective", "stage", "parameters", "data_snapshots", "code_hashes",
            "prompt_hashes", "config_hashes", "modality", "provenance"}
OPTIONAL = {"time_cutoff", "model_version"}
SECRET_KEYS = {"apikey", "key", "accesstoken", "refreshtoken", "idtoken", "token", "authorization",
               "password", "passwd", "clientsecret", "secret", "cookie", "cookies", "credentials",
               "privatekey", "sessionid"}
SECRET_VALUE = re.compile(
    r"(?:\bBearer\s+\S+|\bBasic\s+[A-Za-z0-9+/=]+|\bsk-[A-Za-z0-9_-]{12,}|"
    r"\bAIza[A-Za-z0-9_-]{20,}|-----BEGIN .*PRIVATE KEY-----|"
    r"https?://[^/\s]+:[^/\s]+@|[?&](?:key|api_key|token|access_token|secret)=)", re.I)


class DuplicateRunError(ValueError):
    def __init__(self, existing):
        self.existing = existing
        super().__init__(f"Duplicate specification: {existing['run_id']} ({existing['status']}). "
                         "Inspect it; a terminal run can be retried only with retry_of and retry_reason.")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _canonical(value):
    """Validate JSON, normalize Unicode/numbers, and reject recognizable secrets."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        value = unicodedata.normalize("NFC", value)
        _require(not SECRET_VALUE.search(value), "Potential credential value: do not log credentials")
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        _require(math.isfinite(value), "Non-finite JSON number")
        return int(value) if value.is_integer() else value
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    _require(isinstance(value, dict), "Only JSON-compatible values are allowed")
    result = {}
    for key, item in value.items():
        _require(isinstance(key, str), "JSON object keys must be strings")
        key = unicodedata.normalize("NFC", key)
        compact = re.sub(r"[^a-z0-9]", "", key.lower())
        sensitive = compact in SECRET_KEYS or any(part in compact for part in
                    ("apikey", "accesstoken", "refreshtoken", "clientsecret", "privatekey", "password"))
        _require(not sensitive and not SECRET_VALUE.search(key), "Potential credential field: do not log credentials")
        _require(key not in result, "Duplicate normalized JSON field")
        result[key] = _canonical(item)
    return result


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def read_json(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "Duplicate JSON field")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique_pairs)


def _nonempty(value, label):
    _require(isinstance(value, str) and bool(value.strip()), f"{label} must be a nonempty string")
    return value.strip()


def _hash(value):
    _require(isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value), "SHA-256 must have 64 hex digits")
    return value.lower()


def normalize_spec(spec):
    spec = _canonical(spec)
    _require(isinstance(spec, dict) and REQUIRED <= spec.keys(), "Missing required specification fields")
    _require(not spec.keys() - REQUIRED - OPTIONAL, "Unknown specification fields; place experiment settings in parameters")
    spec["objective"] = _nonempty(spec["objective"], "objective")
    _require(isinstance(spec["stage"], str) and spec["stage"] in STAGES, "Invalid stage")
    _require(isinstance(spec["provenance"], str) and spec["provenance"] in PROVENANCE, "Invalid provenance")
    _require(isinstance(spec["parameters"], dict), "parameters must be an object")
    for name in ("code_hashes", "prompt_hashes", "config_hashes"):
        _require(isinstance(spec[name], dict), f"{name} must be an object")
        hashes = {}
        for label, digest in spec[name].items():
            label = _nonempty(label, name)
            _require(label not in hashes, "Duplicate normalized hash label")
            hashes[label] = _hash(digest)
        spec[name] = hashes
    _require(isinstance(spec["data_snapshots"], list), "data_snapshots must be an array")
    snapshots, ids = [], set()
    for item in spec["data_snapshots"]:
        _require(isinstance(item, dict) and set(item) == {"id", "sha256"}, "Snapshot needs exactly id and sha256")
        item_id = _nonempty(item["id"], "snapshot id")
        _require(item_id not in ids, "Duplicate snapshot id")
        ids.add(item_id)
        snapshots.append({"id": item_id, "sha256": _hash(item["sha256"])})
    spec["data_snapshots"] = sorted(snapshots, key=lambda item: item["id"])
    _require(isinstance(spec["modality"], list) and bool(spec["modality"]), "modality must be a nonempty array")
    spec["modality"] = sorted({_nonempty(item, "modality") for item in spec["modality"]})
    if spec.get("time_cutoff") is not None:
        value = _nonempty(spec["time_cutoff"], "time_cutoff")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            _require(parsed.tzinfo is not None, "time_cutoff needs an explicit timezone")
        except (TypeError, ValueError) as exc:
            raise ValueError("time_cutoff must be an ISO timestamp with timezone") from exc
        spec["time_cutoff"] = parsed.astimezone(timezone.utc).isoformat()
    else:
        spec.pop("time_cutoff", None)
    if spec.get("model_version") is not None:
        spec["model_version"] = _nonempty(spec["model_version"], "model_version")
    else:
        spec.pop("model_version", None)
    return spec


def fingerprint(spec):
    return hashlib.sha256(_json(normalize_spec(spec)).encode("utf-8")).hexdigest()


def hash_file(path):
    """Content hash; fail if basic file metadata changes during this read."""
    path = Path(path)
    with path.open("rb") as handle:
        before = path.stat()
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
        after = path.stat()
    _require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
             "File changed during hashing")
    return digest


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, spec_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('started','succeeded','failed','cancelled')),
  started_at TEXT NOT NULL, finished_at TEXT, retry_of TEXT REFERENCES runs(run_id),
  retry_reason TEXT, finish_json TEXT,
  CHECK ((retry_of IS NULL AND retry_reason IS NULL) OR (retry_of IS NOT NULL AND length(trim(retry_reason)) > 0)),
  CHECK ((status = 'started' AND finished_at IS NULL AND finish_json IS NULL) OR
         (status != 'started' AND finished_at IS NOT NULL AND finish_json IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_initial_run ON runs(fingerprint) WHERE retry_of IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS one_retry_per_run ON runs(retry_of) WHERE retry_of IS NOT NULL;
CREATE INDEX IF NOT EXISTS runs_by_fingerprint ON runs(fingerprint);
CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id),
  occurred_at TEXT NOT NULL, event_type TEXT NOT NULL CHECK(event_type IN ('begin','finish')),
  payload_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS immutable_runs_delete BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, 'Runs cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS immutable_runs_update BEFORE UPDATE ON runs
WHEN OLD.status != 'started' OR NEW.status = 'started' OR
     NEW.run_id IS NOT OLD.run_id OR NEW.fingerprint IS NOT OLD.fingerprint OR
     NEW.spec_json IS NOT OLD.spec_json OR NEW.started_at IS NOT OLD.started_at OR
     NEW.retry_of IS NOT OLD.retry_of OR NEW.retry_reason IS NOT OLD.retry_reason
BEGIN SELECT RAISE(ABORT, 'Only a started run can be finished; identity is immutable'); END;
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


class ResearchLog:
    def __init__(self, db="logs/research.sqlite3"):
        self.db = Path(db)

    def _connect(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.db), timeout=30, isolation_level=None)
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("PRAGMA synchronous = FULL")
            con.executescript(SCHEMA)
        except BaseException:
            con.close()
            raise
        return con

    @staticmethod
    def _row(row):
        if row is None:
            return None
        result = dict(row)
        result["spec"] = json.loads(result.pop("spec_json"))
        result["finish"] = json.loads(result.pop("finish_json")) if result["finish_json"] is not None else None
        result.pop("finish_json", None)
        return result

    def begin(self, spec, *, retry_of=None, retry_reason=None):
        spec = normalize_spec(spec)
        digest = fingerprint(spec)
        _require((retry_of is None) == (retry_reason is None), "retry_of and retry_reason are required together")
        if retry_of is not None:
            retry_of = _nonempty(_canonical(retry_of), "retry_of")
            retry_reason = _nonempty(_canonical(retry_reason), "retry_reason")
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            latest = con.execute("SELECT * FROM runs WHERE fingerprint = ? ORDER BY rowid DESC LIMIT 1", (digest,)).fetchone()
            if latest is not None:
                if retry_of is None:
                    raise DuplicateRunError(self._row(latest))
                _require(latest["run_id"] == retry_of, "retry_of must name the latest run for this specification")
                _require(latest["status"] != "started", "Run is still started; inspect live work and explicitly finish it first")
            else:
                _require(retry_of is None, "No matching prior run exists for retry_of")
            run_id, now = uuid.uuid4().hex, _now()
            con.execute("INSERT INTO runs(run_id,fingerprint,spec_json,status,started_at,retry_of,retry_reason) VALUES(?,?,?,'started',?,?,?)",
                        (run_id, digest, _json(spec), now, retry_of, retry_reason))
            payload = {"fingerprint": digest, "spec": spec, "retry_of": retry_of, "retry_reason": retry_reason}
            con.execute("INSERT INTO events(run_id,occurred_at,event_type,payload_json) VALUES(?,?,'begin',?)", (run_id, now, _json(payload)))
            result = self._row(con.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())
            con.commit()
            return result
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def check(self, spec):
        digest = fingerprint(spec)
        con = self._connect()
        try:
            rows = con.execute("SELECT * FROM runs WHERE fingerprint = ? ORDER BY rowid", (digest,)).fetchall()
            return {"fingerprint": digest, "duplicate": bool(rows), "runs": [self._row(row) for row in rows]}
        finally:
            con.close()

    def status(self, run_id):
        con = self._connect()
        try:
            result = self._row(con.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())
            _require(result is not None, "Unknown run_id")
            return result
        finally:
            con.close()

    def list(self, *, status=None):
        _require(status is None or status in TERMINAL | {"started"}, "Invalid status")
        con = self._connect()
        try:
            query, args = ("SELECT * FROM runs ORDER BY rowid", ()) if status is None else ("SELECT * FROM runs WHERE status = ? ORDER BY rowid", (status,))
            return [self._row(row) for row in con.execute(query, args).fetchall()]
        finally:
            con.close()

    def finish(self, run_id, *, status, notes, artifacts=(), actual_cost=None):
        _require(status in TERMINAL, "finish status must be succeeded, failed, or cancelled")
        notes = _nonempty(_canonical(notes), "factual notes")
        cost = _canonical(actual_cost)
        if cost is not None:
            numeric = {"amount", "api_units", "input_tokens", "output_tokens", "human_minutes"}
            _require(isinstance(cost, dict) and bool(cost) and not cost.keys() - numeric - {"currency"}, "Invalid actual_cost fields")
            for name in numeric & cost.keys():
                _require(cost[name] is None or (type(cost[name]) in (int, float) and cost[name] >= 0), "Actual cost quantities must be nonnegative or null")
            if cost.get("amount") is not None:
                _require(isinstance(cost.get("currency"), str) and re.fullmatch(r"[A-Z]{3}", cost["currency"]), "An amount needs a three-letter currency")
            if "currency" in cost:
                _require(cost["currency"] is None or (isinstance(cost["currency"], str) and re.fullmatch(r"[A-Z]{3}", cost["currency"])), "Invalid currency")
        entries = []
        _require(not isinstance(artifacts, (str, bytes)), "artifacts must be a sequence of paths")
        for artifact in artifacts:
            path = Path(artifact).resolve()
            _require(str(path) not in {str(self.db.resolve()) + suffix for suffix in ("", "-wal", "-shm", "-journal")}, "The authoritative database cannot be a result artifact")
            entry = {"path": _canonical(str(path)), "sha256": hash_file(path), "bytes": path.stat().st_size}
            entries.append(entry)
        _require(len({entry["path"] for entry in entries}) == len(entries), "Duplicate artifact path")
        payload = {"status": status, "notes": notes, "artifacts": entries, "actual_cost": cost}
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            _require(row is not None, "Unknown run_id")
            _require(row["status"] == "started", "Completed runs cannot be overwritten")
            now = _now()
            con.execute("UPDATE runs SET status = ?, finished_at = ?, finish_json = ? WHERE run_id = ?", (status, now, _json(payload), run_id))
            con.execute("INSERT INTO events(run_id,occurred_at,event_type,payload_json) VALUES(?,?,'finish',?)", (run_id, now, _json(payload)))
            result = self._row(con.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())
            con.commit()
            return result
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def export(self, output):
        """Create a new JSONL file; an export never replaces the registry."""
        output = Path(output)
        _require(str(output.resolve()) not in {str(self.db.resolve()) + suffix for suffix in ("", "-wal", "-shm", "-journal")}, "Cannot export over the database")
        con = self._connect()
        try:
            rows = con.execute("SELECT * FROM events ORDER BY event_id").fetchall()
        finally:
            con.close()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                record = dict(row)
                record["payload"] = json.loads(record.pop("payload_json"))
                handle.write(_json(record) + "\n")
        return {"output": str(output.resolve()), "events": len(rows), "sha256": hash_file(output)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="logs/research.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("begin", "check"):
        command = sub.add_parser(name)
        command.add_argument("--spec", required=True)
        if name == "begin":
            command.add_argument("--retry-of")
            command.add_argument("--retry-reason")
    command = sub.add_parser("status")
    command.add_argument("--run-id", required=True)
    command = sub.add_parser("list")
    command.add_argument("--status", choices=sorted(TERMINAL | {"started"}))
    command = sub.add_parser("finish")
    command.add_argument("--run-id", required=True)
    command.add_argument("--status", required=True, choices=sorted(TERMINAL))
    command.add_argument("--notes", required=True)
    command.add_argument("--artifact", action="append", default=[])
    command.add_argument("--cost-json", help="Path to a JSON object of measured actual costs; omit for unknown")
    command = sub.add_parser("export")
    command.add_argument("--output", required=True)
    command = sub.add_parser("hash")
    command.add_argument("path")
    args = parser.parse_args(argv)
    log = ResearchLog(args.db)
    try:
        if args.command == "hash":
            result = {"sha256": hash_file(args.path)}
        elif args.command == "begin":
            result = log.begin(read_json(args.spec), retry_of=args.retry_of, retry_reason=args.retry_reason)
        elif args.command == "check":
            result = log.check(read_json(args.spec))
        elif args.command == "status":
            result = log.status(args.run_id)
        elif args.command == "list":
            result = log.list(status=args.status)
        elif args.command == "finish":
            result = log.finish(args.run_id, status=args.status, notes=args.notes, artifacts=args.artifact,
                                actual_cost=read_json(args.cost_json) if args.cost_json else None)
        else:
            result = log.export(args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except DuplicateRunError as exc:
        print(_json({"error": str(exc), "existing_run_id": exc.existing["run_id"]}))
        return 3
    except (ValueError, OSError, sqlite3.Error) as exc:
        # JSON parser/OS exceptions can contain user input or paths. Do not echo them.
        safe = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
        print(_json({"error": safe}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
