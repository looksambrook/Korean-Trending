"""Local, token-based two-phase rating storage. Preview rows are UI fixtures.

This module has no network, recruiting, or model-calling functionality. Tokens
identify opaque evaluator slots, never names/contact details. Keep exported
preview rows separate from research ratings. Database creation never overwrites.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
A_SCORES = ("situation_fit", "naturalness", "willingness_to_use")
B_SCORES = ("identity_preservation",)
MISSING_REASONS = {"skip", "cannot_judge"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, label, maximum=100000):
    _require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum,
             "Invalid " + label)
    return value


def _opaque_id(value, label):
    _require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value),
             "Use an opaque identifier for " + label)
    return value


def _validate_bundle(bundle):
    _require(isinstance(bundle, dict), "Invalid study bundle")
    study_id = _opaque_id(bundle.get("study_id"), "study")
    mode = bundle.get("mode")
    _require(mode in {"preview", "pilot"}, "Study mode must be preview or pilot")
    authorization = bundle.get("authorization")
    _require(isinstance(authorization, dict), "Missing study authorization record")
    if mode == "pilot":
        _require(authorization.get("approved") is True and
                 isinstance(authorization.get("approval_reference"), str) and
                 bool(authorization["approval_reference"].strip()) and
                 authorization.get("institutional_procedure_confirmed") is True,
                 "Pilot requires confirmed researcher scope and institutional procedure record")
    slots = bundle.get("slots")
    _require(isinstance(slots, list) and slots, "Study needs evaluator slots")
    for slot in slots:
        _opaque_id(slot, "evaluator slot")
    _require(len(slots) == len(set(slots)), "Duplicate evaluator slot")
    seed = bundle.get("seed")
    _require(type(seed) is int and seed >= 0, "Invalid assignment seed")
    raw_items = bundle.get("items")
    _require(isinstance(raw_items, list) and raw_items, "Study needs planned items")
    items, logical, references, scenarios = {}, set(), {}, {}
    for raw in raw_items:
        _require(isinstance(raw, dict), "Invalid item")
        item = {key: raw.get(key) for key in
                ("item_id", "family_id", "condition", "scenario_id", "repeat", "status",
                 "situation", "output_text", "reference")}
        for key in ("item_id", "family_id", "scenario_id"):
            _opaque_id(item[key], key)
        _require(item["item_id"] not in items, "Duplicate item ID")
        _require(item["condition"] in {"B1", "B2", "B3"}, "Unknown condition")
        _require(type(item["repeat"]) is int and item["repeat"] >= 0, "Invalid repeat")
        _text(item["status"], "item status", 128)
        _require(isinstance(item["situation"], str), "Invalid situation")
        _require(isinstance(item["output_text"], str), "Invalid output text")
        if item["status"] == "ok":
            _text(item["situation"], "situation")
            _text(item["output_text"], "output text")
        else:
            _require(item["output_text"] == "", "Failed outputs must have empty display text")
        ref = item["reference"]
        family = item["family_id"]
        # Failed rows from the experiment export have no display material. Keep
        # their identifiers/assignments without inventing a situation or reference.
        if item["status"] == "ok" or ref is not None:
            _require(isinstance(ref, dict) and set(ref) == {"family_id", "canonical_text", "supports"},
                     "Reference must contain raw family text and support only")
            _require(ref["family_id"] == family, "Reference family differs from item")
            _text(ref["canonical_text"], "canonical text")
            _require(isinstance(ref["supports"], list) and ref["supports"], "Reference needs support")
            support_ids = set()
            for support in ref["supports"]:
                _require(isinstance(support, dict) and set(support) == {"support_id", "text", "context"},
                         "Unexpected reference support fields")
                _opaque_id(support["support_id"], "support")
                _require(support["support_id"] not in support_ids, "Duplicate reference support")
                support_ids.add(support["support_id"])
                _text(support["text"], "support text")
                _require(isinstance(support["context"], str), "Invalid support context")
            _require(family not in references or references[family] == _json(ref),
                     "Reference differs between conditions of one family")
            references[family] = _json(ref)
        if item["situation"]:
            scenario_value = (family, item["situation"])
            _require(item["scenario_id"] not in scenarios or scenarios[item["scenario_id"]] == scenario_value,
                     "Scenario differs between conditions")
            scenarios[item["scenario_id"]] = scenario_value
        cell = (family, item["scenario_id"], item["condition"], item["repeat"])
        _require(cell not in logical, "Duplicate logical output")
        logical.add(cell)
        items[item["item_id"]] = json.loads(_json(item))
    assignments = bundle.get("assignments")
    _require(isinstance(assignments, list) and assignments, "Missing assignments")
    by_slot, assigned, conditions = {slot: [] for slot in slots}, set(), {}
    for assignment in assignments:
        _require(isinstance(assignment, dict) and set(assignment) == {"slot_id", "item_id"},
                 "Invalid assignment")
        slot, item_id = assignment["slot_id"], assignment["item_id"]
        _require(slot in by_slot and item_id in items, "Unknown slot or item in assignment")
        _require((slot, item_id) not in assigned, "Duplicate assignment")
        assigned.add((slot, item_id))
        item = items[item_id]
        key = (slot, item["family_id"])
        _require(key not in conditions or conditions[key] == item["condition"],
                 "One evaluator cannot see multiple conditions of the same family")
        conditions[key] = item["condition"]
        by_slot[slot].append(item_id)
    _require(all(by_slot.values()), "Every slot needs planned assignments")
    manifest = {"schema_version": SCHEMA_VERSION, "study_id": study_id, "study_mode": mode,
                "provenance": "ui_fixture" if mode == "preview" else "human_rating",
                "created_at": _now(), "seed": seed, "slot_count": len(slots),
                "planned_item_count": len(items), "planned_assignment_count": len(assigned),
                "failed_item_count": sum(i["status"] != "ok" for i in items.values()),
                "authorization": {key: authorization.get(key) for key in
                                  ("approved", "approval_reference", "institutional_procedure_confirmed")}}
    return manifest, slots, items, by_slot


def _connect(db_path):
    path = Path(db_path).resolve()
    _require(path.is_file(), "Study database does not exist")
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def create_study(db_path, bundle):
    """Create once and return plaintext slot tokens only to the caller.

    Only SHA-256 token hashes enter SQLite. Slot IDs must be opaque pseudonyms.
    The caller is responsible for private distribution and access to exports.
    """
    manifest, slots, items, by_slot = _validate_bundle(bundle)
    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError("Study database already exists; refusing overwrite") from None
    os.close(descriptor)
    tokens = [{"slot_id": slot, "token": secrets.token_urlsafe(32)} for slot in slots]
    connection = None
    try:
        connection = _connect(path)
        connection.executescript("""
            CREATE TABLE study (id INTEGER PRIMARY KEY CHECK(id=1), manifest_json TEXT NOT NULL);
            CREATE TABLE slots (slot_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE);
            CREATE TABLE items (item_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
            CREATE TABLE assignments (
                slot_id TEXT NOT NULL REFERENCES slots(slot_id),
                item_id TEXT NOT NULL REFERENCES items(item_id),
                order_a INTEGER NOT NULL, order_b INTEGER NOT NULL,
                PRIMARY KEY(slot_id,item_id), UNIQUE(slot_id,order_a), UNIQUE(slot_id,order_b));
            CREATE TABLE ratings (
                slot_id TEXT NOT NULL, item_id TEXT NOT NULL,
                phase TEXT NOT NULL CHECK(phase IN ('A','B')),
                values_json TEXT NOT NULL, submitted_at TEXT NOT NULL,
                PRIMARY KEY(slot_id,item_id,phase),
                FOREIGN KEY(slot_id,item_id) REFERENCES assignments(slot_id,item_id));
            CREATE TABLE exposures (
                slot_id TEXT NOT NULL, item_id TEXT NOT NULL,
                phase TEXT NOT NULL CHECK(phase IN ('A','B')), shown_at TEXT NOT NULL,
                PRIMARY KEY(slot_id,item_id,phase),
                FOREIGN KEY(slot_id,item_id) REFERENCES assignments(slot_id,item_id));
        """)
        with connection:
            connection.execute("INSERT INTO study VALUES (1,?)", (_json(manifest),))
            connection.executemany("INSERT INTO slots VALUES (?,?)",
                                   [(r["slot_id"], hashlib.sha256(r["token"].encode()).hexdigest()) for r in tokens])
            connection.executemany("INSERT INTO items VALUES (?,?)",
                                   [(item_id, _json(item)) for item_id, item in items.items()])
            for slot in slots:
                order_a, order_b = sorted(by_slot[slot]), sorted(by_slot[slot])
                random.Random(str(manifest["seed"]) + ":" + slot + ":A").shuffle(order_a)
                random.Random(str(manifest["seed"]) + ":" + slot + ":B").shuffle(order_b)
                b_positions = {item_id: pos for pos, item_id in enumerate(order_b)}
                connection.executemany("INSERT INTO assignments VALUES (?,?,?,?)",
                                       [(slot, item_id, pos, b_positions[item_id]) for pos, item_id in enumerate(order_a)])
        return tokens
    except BaseException:
        if connection is not None:
            connection.close()
            connection = None
        # Only this function's newly created, exclusive database is removed.
        path.unlink(missing_ok=True)
        raise
    finally:
        if connection is not None:
            connection.close()


def _slot(connection, token):
    _require(isinstance(token, str) and 1 <= len(token) <= 1024, "Invalid evaluator token")
    row = connection.execute("SELECT slot_id FROM slots WHERE token_hash=?",
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    _require(row is not None, "Invalid evaluator token")
    return row["slot_id"]


def _state(connection, slot):
    manifest = json.loads(connection.execute("SELECT manifest_json FROM study WHERE id=1").fetchone()[0])
    _require(manifest.get("schema_version") == SCHEMA_VERSION, "Unsupported study database version")
    assigned = connection.execute(
        "SELECT a.item_id,a.order_a,a.order_b,i.payload_json FROM assignments a "
        "JOIN items i ON i.item_id=a.item_id WHERE a.slot_id=?", (slot,)).fetchall()
    visible = [(row, json.loads(row["payload_json"])) for row in assigned]
    visible = [(row, item) for row, item in visible if item["status"] == "ok"]
    completed = {(row["item_id"], row["phase"]) for row in connection.execute(
        "SELECT item_id,phase FROM ratings WHERE slot_id=?", (slot,))}
    progress = {"study_mode": manifest["study_mode"], "completed": len(completed), "total": 2 * len(visible)}
    for phase in ("A", "B"):
        pending = [(row, item) for row, item in visible if (item["item_id"], phase) not in completed]
        if pending:
            row, item = min(pending, key=lambda pair: pair[0]["order_" + phase.lower()])
            public = {key: item[key] for key in ("item_id", "situation", "output_text")}
            if phase == "B":
                public["reference"] = item["reference"]
            return {**progress, "phase": phase, "item": public}
    return {**progress, "phase": "complete", "item": None}


def _mark_shown(connection, slot, state):
    # First server presentation, not proof of reading or uninterrupted task time.
    if state["item"] is not None:
        connection.execute("INSERT OR IGNORE INTO exposures VALUES (?,?,?,?)",
                           (slot, state["item"]["item_id"], state["phase"], _now()))


def next_item(db_path, token):
    """Return only the current item; the entire A phase precedes any B reference."""
    connection = _connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        slot = _slot(connection, token)
        state = _state(connection, slot)
        _mark_shown(connection, slot, state)
        connection.commit()
        return state
    finally:
        connection.close()


def _validated_values(phase, values):
    _require(isinstance(phase, str) and phase in {"A", "B"} and isinstance(values, dict),
             "Invalid rating phase or values")
    scores = A_SCORES if phase == "A" else B_SCORES
    allowed = set(scores) | {key + "_missing_reason" for key in scores} | {"comment"}
    if phase == "B":
        allowed.add("prior_familiarity")
    _require(set(values) <= allowed, "Unexpected rating fields")
    clean = {}
    for score in scores:
        _require(score in values, "Missing score field")
        value, reason = values[score], values.get(score + "_missing_reason")
        if value is None:
            _require(isinstance(reason, str) and reason in MISSING_REASONS,
                     "A skipped score needs an explicit missing reason")
        else:
            _require(type(value) is int and 1 <= value <= 5, "Scores must be integers from 1 to 5")
            _require(reason is None, "A scored answer cannot also be marked missing")
        clean[score], clean[score + "_missing_reason"] = value, reason
    if phase == "B":
        _require(isinstance(values.get("prior_familiarity"), str) and
                 values["prior_familiarity"] in {"known", "unknown", "unsure"}, "Invalid prior familiarity")
        clean["prior_familiarity"] = values["prior_familiarity"]
    comment = values.get("comment", "")
    _require(isinstance(comment, str) and len(comment) <= 1000, "Comment must be at most 1000 characters")
    clean["comment"] = comment
    return clean


def submit_rating(db_path, token, item_id, phase, values):
    """Insert once, atomically; concurrent/replayed/out-of-order submissions fail."""
    _opaque_id(item_id, "item")
    clean = _validated_values(phase, values)
    connection = _connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        slot = _slot(connection, token)
        existing = connection.execute("SELECT 1 FROM ratings WHERE slot_id=? AND item_id=? AND phase=?",
                                      (slot, item_id, phase)).fetchone()
        _require(existing is None, "Rating was already submitted and cannot be edited or replayed")
        state = _state(connection, slot)
        _require(state["phase"] == phase and state["item"] is not None and state["item"]["item_id"] == item_id,
                 "Only the current assigned item and phase can be submitted")
        _require(connection.execute("SELECT 1 FROM exposures WHERE slot_id=? AND item_id=? AND phase=?",
                                    (slot, item_id, phase)).fetchone() is not None,
                 "Open the current item before submitting")
        connection.execute("INSERT INTO ratings VALUES (?,?,?,?,?)", (slot, item_id, phase, _json(clean), _now()))
        result = _state(connection, slot)
        _mark_shown(connection, slot, result)
        connection.commit()
        return result
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def export_rows(db_path):
    """Export one row per planned slot/item, including failures and incomplete work.

    Preview rows always have study_mode='preview' and provenance='ui_fixture'.
    A/B comments and submission times are separate. Absent ratings stay null;
    evaluation_status distinguishes unstarted, partially completed and failed work.
    """
    connection = _connect(db_path)
    try:
        connection.execute("BEGIN")
        manifest = json.loads(connection.execute("SELECT manifest_json FROM study WHERE id=1").fetchone()[0])
        values = {(r["slot_id"], r["item_id"], r["phase"]): (json.loads(r["values_json"]), r["submitted_at"])
                  for r in connection.execute("SELECT * FROM ratings")}
        exposures = {(r["slot_id"], r["item_id"], r["phase"]): r["shown_at"]
                     for r in connection.execute("SELECT * FROM exposures")}
        rows = []
        for assignment in connection.execute(
                "SELECT a.slot_id,i.payload_json FROM assignments a JOIN items i ON i.item_id=a.item_id "
                "ORDER BY a.slot_id,a.order_a"):
            item, slot = json.loads(assignment["payload_json"]), assignment["slot_id"]
            row = {"study_id": manifest["study_id"], "study_mode": manifest["study_mode"],
                   "provenance": manifest["provenance"], "rater_id": slot, "slot_id": slot,
                   **{key: item[key] for key in ("item_id", "family_id", "condition", "scenario_id", "repeat", "status")}}
            for key in A_SCORES + B_SCORES:
                row[key], row[key + "_missing_reason"] = None, None
            row.update(prior_familiarity=None, comment_A=None, comment_B=None,
                       submitted_at_A=None, submitted_at_B=None,
                       shown_at_A=exposures.get((slot, item["item_id"], "A")),
                       shown_at_B=exposures.get((slot, item["item_id"], "B")))
            present = set()
            for phase in ("A", "B"):
                stored = values.get((slot, item["item_id"], phase))
                if stored:
                    answers, timestamp = stored
                    row.update({k: v for k, v in answers.items() if k != "comment"})
                    row["comment_" + phase] = answers["comment"]
                    row["submitted_at_" + phase] = timestamp
                    present.add(phase)
            row["evaluation_status"] = ("generation_failure" if item["status"] != "ok" else
                                        "complete" if present == {"A", "B"} else
                                        "A_only" if present == {"A"} else "not_started")
            rows.append(row)
        connection.commit()
        return rows
    finally:
        connection.close()
