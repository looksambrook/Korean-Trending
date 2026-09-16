"""Local, provenance-preserving manifests for time-limited meme experiments.

No assets are downloaded, generated, transcribed or inferred. An ingest copies
the exact bytes of existing project assets; snapshot exports share the same
permitted supervision between retrieval and fine-tuning. Test targets stay in
an explicitly protected file. This module does not establish dataset quality.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

from research_log import DuplicateRunError, ResearchLog, hash_file, read_json

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "data/schemas/multimodal_meme_record.schema.json"
PROVENANCE = {"actual_collected", "researcher_authored", "ai_synthetic"}
MODALITIES = {"text", "image", "audio", "video"}
SPLITS = {"train", "dev", "test"}
TOP_FIELDS = {"meme_id", "family_id", "version", "available_at", "published_at", "provenance", "assets",
              "relationships", "knowledge", "annotations", "creation_examples", "split", "title", "platform"}
DEFAULT_SPLIT = {"seed": "meme-study-v1", "fractions": {"train": .7, "dev": .15, "test": .15}}


class PartitionConflictError(ValueError):
    """A component links incompatible fixed partitions; never silently move it."""

    def __init__(self, message, *, conflict_meme_ids=(), requested_or_fixed_splits=()):
        super().__init__(message)
        self.conflict_meme_ids = sorted(set(conflict_meme_ids))
        self.requested_or_fixed_splits = sorted(set(requested_or_fixed_splits))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _time(value):
    _require(isinstance(value, str) and value, "timestamp must be a nonempty ISO string")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO timestamp") from exc
    _require(stamp.tzinfo is not None, "timestamp must contain a timezone")
    return stamp.astimezone(timezone.utc)


def _nonempty(value, name):
    _require(isinstance(value, str) and bool(value.strip()), name + " must be a nonempty string")


def _read_rows(path):
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL row {number}") from exc
        _require(isinstance(row, dict), f"row {number} must be an object")
        rows.append(row)
    return rows


def _write_rows(path, rows):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _project_asset(value):
    _nonempty(value, "local_path")
    path = Path(value)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    _require(path.is_relative_to(ROOT.resolve()), "asset must resolve inside the project")
    _require(path.is_file(), "asset path must identify an existing regular file")
    return path


def _author(annotation):
    author = annotation.get("author")
    _require(isinstance(author, dict), "annotation author must be a separate object")
    _nonempty(author.get("id"), "author.id")
    _require(author.get("type") in {"human", "ai", "system"}, "unknown author type")
    minutes = annotation.get("human_minutes")
    _require(minutes is None or type(minutes) in {int, float} and minutes >= 0, "invalid human_minutes")
    if author["type"] != "human":
        _require(minutes in {None, 0}, "nonhuman annotation cannot claim human review time")


def validate_records(rows, *, verify_assets=True):
    """Validate supplied fields and return a deep copy with measured hashes.

    Unknown top-level fields are rejected so evaluation labels cannot silently
    enter a free-form training record. Available-at times are supplied claims,
    not reconstructed historical observation times.
    """
    rows = json.loads(json.dumps(rows, ensure_ascii=False))
    seen_versions = set()
    for row in rows:
        _require(not set(row) - TOP_FIELDS, "unknown record fields")
        for name in ("meme_id", "family_id"):
            _nonempty(row.get(name), name)
        _require(type(row.get("version")) in {int, str} and str(row["version"]), "version required")
        identity = (row["meme_id"], str(row["version"]))
        _require(identity not in seen_versions, "duplicate meme/version")
        seen_versions.add(identity)
        record_time = _time(row.get("available_at"))
        if row.get("published_at") is not None:
            _time(row["published_at"])
        _require("published_at" in row, "published_at must be explicit, nullable if unknown")
        _require(row.get("provenance") in PROVENANCE, "record provenance required")
        _require(row.get("split") is None or row["split"] in SPLITS, "invalid requested split")
        _require(isinstance(row.get("assets"), list) and row["assets"], "assets must be a nonempty array")
        asset_ids = set()
        for asset in row["assets"]:
            _require(isinstance(asset, dict), "asset must be an object")
            allowed = {"asset_id", "source_url", "local_path", "sha256", "mime", "modality", "license_or_use_basis",
                       "status", "provenance", "purpose", "available_at", "published_at", "original_local_path", "source_group_id", "parent_asset_ids"}
            _require(not set(asset) - allowed, "unknown asset fields")
            _nonempty(asset.get("asset_id"), "asset_id")
            _require(asset["asset_id"] not in asset_ids, "duplicate asset id within record")
            asset_ids.add(asset["asset_id"])
            _nonempty(asset.get("source_group_id"), "source_group_id")
            _require(isinstance(asset.get("parent_asset_ids", []), list) and all(isinstance(p, str) and p for p in asset.get("parent_asset_ids", [])), "parent_asset_ids must be an array of ids")
            _require("source_url" in asset and (asset["source_url"] is None or isinstance(asset["source_url"], str)), "source_url must be explicit, nullable")
            _require(asset.get("modality") in MODALITIES, "unknown modality")
            _nonempty(asset.get("mime"), "mime")
            _require(asset["mime"].split("/", 1)[0] == asset["modality"], "mime must agree with declared modality")
            _nonempty(asset.get("license_or_use_basis"), "license_or_use_basis")
            _require(asset.get("status") in {"available", "restricted", "missing", "excluded"}, "unknown asset status")
            _require(asset.get("provenance") in PROVENANCE, "asset provenance required")
            _require(asset.get("purpose") in {"source_evidence", "training_target", "evaluation_only"}, "asset purpose required")
            asset.setdefault("available_at", row["available_at"])
            _require(_time(asset["available_at"]) <= record_time, "new assets require a record version available no earlier than the asset")
            if asset.get("published_at") is not None:
                _time(asset["published_at"])
            if asset.get("local_path") is not None:
                if verify_assets:
                    path = _project_asset(asset["local_path"])
                    measured = hash_file(path)
                    _require(asset.get("sha256") in {None, measured}, "asset SHA-256 mismatch")
                    asset["sha256"] = measured
                else:
                    _require(isinstance(asset.get("sha256"), str) and len(asset["sha256"]) == 64, "asset hash required")
            else:
                _require(asset["status"] != "available", "available asset must have local content")
                _require(asset.get("sha256") is None, "missing asset cannot claim a measured hash")
                asset["sha256"] = None
        for name in ("relationships", "knowledge", "annotations", "creation_examples"):
            row.setdefault(name, [])
            _require(isinstance(row[name], list), name + " must be an array")
        annotation_ids = set()
        for annotation in row["annotations"]:
            _require(set(annotation) <= {"annotation_id", "author", "role", "available_at", "content", "provenance", "human_minutes"}, "unknown annotation fields")
            _nonempty(annotation.get("annotation_id"), "annotation_id")
            _require(annotation["annotation_id"] not in annotation_ids, "duplicate annotation id")
            annotation_ids.add(annotation["annotation_id"])
            _author(annotation)
            _time(annotation.get("available_at"))
            _require(annotation.get("provenance") in PROVENANCE, "annotation provenance required")
            _require(annotation.get("role") in {"source_context", "quality_review", "evaluation_gold"}, "unknown annotation role")
        for relationship in row["relationships"]:
            _require(set(relationship) <= {"relationship_id", "target_meme_id", "relation", "available_at", "provenance", "basis_asset_ids"}, "unknown relationship fields")
            for name in ("relationship_id", "target_meme_id", "relation"):
                _nonempty(relationship.get(name), name)
            _time(relationship.get("available_at"))
            _require(relationship.get("provenance") in PROVENANCE, "relationship provenance required")
            _require(set(relationship.get("basis_asset_ids", [])) <= asset_ids, "unknown relationship asset reference")
        knowledge_versions = set()
        for knowledge in row["knowledge"]:
            allowed = {"knowledge_id", "version", "available_at", "text", "provenance", "purpose", "source_asset_ids", "derived_from_annotation_ids"}
            _require(set(knowledge) <= allowed, "unknown knowledge fields")
            _nonempty(knowledge.get("knowledge_id"), "knowledge_id")
            _nonempty(knowledge.get("text"), "knowledge.text")
            _require(type(knowledge.get("version")) in {int, str}, "knowledge version required")
            identity = (knowledge["knowledge_id"], str(knowledge["version"]))
            _require(identity not in knowledge_versions, "duplicate knowledge version")
            knowledge_versions.add(identity)
            _time(knowledge.get("available_at"))
            _require(knowledge.get("purpose") in {"source_context", "evaluation_gold"}, "knowledge purpose required")
            _require(knowledge.get("provenance") in PROVENANCE, "knowledge provenance required")
            _require(bool(knowledge.get("source_asset_ids")) and set(knowledge["source_asset_ids"]) <= asset_ids, "knowledge requires declared source assets")
            _require(set(knowledge.get("derived_from_annotation_ids", [])) <= annotation_ids, "unknown knowledge annotation reference")
        example_ids = set()
        for example in row["creation_examples"]:
            allowed = {"example_id", "brief", "target_asset_ids", "target_text", "available_at", "provenance", "purpose", "split", "author"}
            _require(set(example) <= allowed, "unknown creation example fields")
            _nonempty(example.get("example_id"), "example_id")
            _require(example["example_id"] not in example_ids, "duplicate example id")
            example_ids.add(example["example_id"])
            _nonempty(example.get("brief"), "brief")
            _time(example.get("available_at"))
            _require(example.get("provenance") in PROVENANCE, "example provenance required")
            _require(example.get("purpose") in {"training_supervision", "evaluation_only"}, "example purpose required")
            _require(example.get("split") is None or example["split"] in SPLITS, "unknown example split")
            _require(bool(example.get("target_text")) or bool(example.get("target_asset_ids")), "creation target required")
            _require(set(example.get("target_asset_ids", [])) <= asset_ids, "unknown target asset reference")
            if example.get("author") is not None:
                _author({"author": example["author"]})
    # An equal observation time cannot ambiguously select two revisions.
    by_meme = defaultdict(set)
    for row in rows:
        stamp = _time(row["available_at"])
        _require(stamp not in by_meme[row["meme_id"]], "record revisions need distinct available_at timestamps")
        by_meme[row["meme_id"]].add(stamp)
    return rows


def _provenance(rows):
    values = {row.get("provenance") for row in rows if row.get("provenance") in PROVENANCE}
    for row in rows:
        for name in ("assets", "knowledge", "relationships", "annotations", "creation_examples"):
            items = row.get(name, [])
            if isinstance(items, list):
                values.update(item.get("provenance") for item in items if isinstance(item, dict) and item.get("provenance") in PROVENANCE)
    return next(iter(values)) if len(values) == 1 else "mixed" if values else "not_applicable"


def _asset_snapshots(rows):
    """Measure readable project assets before validation, for attempt identity."""
    found, errors = {}, []
    for row in rows:
        for asset in row.get("assets", []) if isinstance(row.get("assets"), list) else []:
            if not isinstance(asset, dict) or asset.get("local_path") is None:
                continue
            try:
                path = _project_asset(asset["local_path"])
                found[str(path)] = {"id": "asset:" + str(path), "sha256": hash_file(path)}
            except (ValueError, OSError) as exc:
                errors.append(type(exc).__name__)
    return list(found.values()), errors


def _spec(operation, source, rows, parameters):
    return {"objective": "Local multimodal meme dataset " + operation + "; no fabricated observations or experiment outcomes",
            "stage": "desk_research", "parameters": parameters,
            "data_snapshots": [{"id": str(Path(source).resolve()), "sha256": hash_file(source)}],
            "code_hashes": {"multimodal_meme_dataset.py": hash_file(__file__)},
            "prompt_hashes": {}, "config_hashes": {"record_schema": hash_file(SCHEMA)},
            "modality": sorted({asset["modality"] for row in rows for asset in (row.get("assets", []) if isinstance(row.get("assets"), list) else [])
                                if isinstance(asset, dict) and asset.get("modality") in MODALITIES}) or ["text"],
            "provenance": _provenance(rows)}


def _new_output(output_dir):
    output = Path(output_dir).resolve()
    _require(output.is_relative_to(ROOT.resolve()), "dataset output must be within the project")
    _require(not output.exists(), "output directory already exists; choose a new version")
    return output


def _failure_artifact(output, record, exc):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "failure.json"
    message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
    with path.open("x", encoding="utf-8") as handle:
        json.dump({"run_id": record["run_id"], "status": "failed", "error_type": type(exc).__name__,
                   "validation_message": message, "existing_inputs_preserved": True}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def ingest(input_path, output_dir, db=None, *, retry_of=None, retry_reason=None):
    """Copy original local asset bytes to a new immutable dataset directory."""
    source, output = Path(input_path).resolve(), _new_output(output_dir)
    raw_rows = _read_rows(source)
    asset_snapshots, inspection_errors = _asset_snapshots(raw_rows)
    spec = _spec("ingest", source, raw_rows, {"operation": "ingest", "network_requests": 0, "input_timestamp_basis": "supplied_by_dataset_author", "asset_inspection_errors": inspection_errors})
    spec["data_snapshots"].extend(asset_snapshots)
    log = ResearchLog(db or ROOT / "logs/research.sqlite3")
    record = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    try:
        rows = validate_records(raw_rows)
        measured = {entry["id"].removeprefix("asset:"): entry["sha256"] for entry in asset_snapshots}
        for row in rows:
            for asset in row["assets"]:
                if asset.get("local_path"):
                    _require(measured.get(str(_project_asset(asset["local_path"]))) == asset["sha256"], "asset changed after attempt registration")
        output.mkdir(parents=True)
        asset_dir = output / "assets"
        asset_dir.mkdir()
        copied = {}
        for row in rows:
            for asset in row["assets"]:
                if asset.get("local_path") is None:
                    continue
                original = _project_asset(asset["local_path"])
                digest = asset["sha256"]
                if digest not in copied:
                    target = asset_dir / (digest + original.suffix.lower())
                    shutil.copyfile(original, target)
                    _require(hash_file(target) == digest and hash_file(original) == digest, "asset changed during ingest")
                    copied[digest] = target
                asset["original_local_path"] = str(original.relative_to(ROOT))
                asset["local_path"] = str(copied[digest].relative_to(ROOT))
        records_path = output / "records.jsonl"
        _write_rows(records_path, rows)
        manifest = {"schema_version": "multimodal-meme-dataset-v1", "run_id": record["run_id"],
                    "created_at": datetime.now(timezone.utc).isoformat(), "records_path": "records.jsonl",
                    "record_count": len(rows), "copied_unique_asset_count": len(copied), "provenance": _provenance(rows),
                    "available_asset_modalities": sorted({a["modality"] for row in rows for a in row["assets"] if a["status"] == "available"}),
                    "input_sha256": hash_file(source), "records_sha256": hash_file(records_path),
                    "source_input": str(source), "network_requests": 0, "human_review_minutes": 0,
                    "timestamp_basis": "author supplied; not independently verified historical availability",
                    "multimodal_completeness_claim": False, "media_decode_verified": False,
                    "media_validation": "Declared MIME/modality consistency and byte hashes only; native content decoding not performed"}
        manifest_path = output / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.finish(record["run_id"], status="succeeded", notes="Copied existing assets byte-for-byte and retained provenance, timestamps and authored annotations. No downloads, synthetic-to-actual relabeling, model work or evaluation.",
                   artifacts=[records_path, manifest_path, *copied.values()], actual_cost={"amount": 0, "currency": "USD", "human_minutes": 0})
        return manifest
    except Exception as exc:
        failure = _failure_artifact(output, record, exc)
        log.finish(record["run_id"], status="failed", notes="Dataset ingest failed: " + type(exc).__name__ + "; see failure.json for the validation reason", artifacts=[failure], actual_cost={"amount": 0, "currency": "USD", "human_minutes": 0})
        raise


def _latest(items, id_field, cutoff):
    selected = {}
    for item in items:
        if _time(item["available_at"]) > cutoff:
            continue
        identity = item[id_field]
        previous = selected.get(identity)
        if previous is None or _time(item["available_at"]) > _time(previous["available_at"]):
            selected[identity] = item
    return list(selected.values())


def _split_groups(rows, config, prior=None):
    _require(set(config) <= {"seed", "fractions"}, "unknown split configuration")
    _nonempty(config.get("seed"), "split seed")
    fractions = config.get("fractions", {})
    _require(set(fractions) == SPLITS and all(type(v) in {int, float} and 0 <= v <= 1 for v in fractions.values()) and abs(sum(fractions.values()) - 1) < 1e-8, "split fractions must sum to one")
    parent = {row["meme_id"]: row["meme_id"] for row in rows}
    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item
    def join(left, right):
        a, b = find(left), find(right)
        parent[max(a, b)] = min(a, b)
    family_members, asset_members, source_members, asset_id_members = {}, {}, {}, {}
    for row in rows:
        mid = row["meme_id"]
        family = row["family_id"]
        if family in family_members:
            join(mid, family_members[family])
        family_members[family] = mid
        for asset in row["assets"]:
            asset_id_members[asset["asset_id"]] = mid
            source_group = asset["source_group_id"]
            if source_group in source_members:
                join(mid, source_members[source_group])
            source_members[source_group] = mid
            digest = asset.get("sha256")
            if digest:
                if digest in asset_members:
                    join(mid, asset_members[digest])
                asset_members[digest] = mid
        for relation in row["relationships"]:
            if relation["relation"] in {"repost_of", "same_source_as", "variant_of", "same_family", "derived_from", "cross_platform_match"} and relation["target_meme_id"] in parent:
                join(mid, relation["target_meme_id"])
    for row in rows:
        for asset in row["assets"]:
            for parent_asset in asset.get("parent_asset_ids", []):
                if parent_asset in asset_id_members:
                    join(row["meme_id"], asset_id_members[parent_asset])
    groups = defaultdict(list)
    for mid in parent:
        groups[find(mid)].append(mid)
    desired = defaultdict(set)
    prior_anchors = (prior or {}).get("anchors", {})
    def anchors_for(row):
        return {"meme_id": [row["meme_id"]], "family_id": [row["family_id"]],
                "asset_sha256": [a["sha256"] for a in row["assets"] if a.get("sha256")],
                "source_group_id": [a["source_group_id"] for a in row["assets"]],
                "asset_id": [a["asset_id"] for a in row["assets"]] + [p for a in row["assets"] for p in a.get("parent_asset_ids", [])]}
    for row in rows:
        group = find(row["meme_id"])
        for anchor_type, values in anchors_for(row).items():
            for value in values:
                existing = prior_anchors.get(anchor_type, {}).get(value)
                if existing:
                    desired[group].add(existing)
        # Prior manifests made before anchors were introduced still lock meme ids.
        for old_group in (prior or {}).get("assignments", []):
            if row["meme_id"] in old_group["meme_ids"]:
                desired[group].add(old_group["split"])
        if row.get("split"):
            desired[group].add(row["split"])
        for example in row["creation_examples"]:
            requested = example.get("split") or ("test" if example["purpose"] == "evaluation_only" else None)
            if requested:
                desired[group].add(requested)
            _require(not (example["purpose"] == "evaluation_only" and requested == "train"), "evaluation-only examples cannot enter train")
    result, assignments = {}, []
    for group, members in sorted(groups.items()):
        if len(desired[group]) > 1:
            raise PartitionConflictError("split conflict: one family, media or reuse component connects incompatible fixed/requested partitions; reset the study from an uncontaminated base instead of moving examples",
                                         conflict_meme_ids=members, requested_or_fixed_splits=desired[group])
        point = int(hashlib.sha256((config["seed"] + "|" + group).encode()).hexdigest(), 16) / 2**256
        split = next(iter(desired[group])) if desired[group] else "train" if point < fractions["train"] else "dev" if point < fractions["train"] + fractions["dev"] else "test"
        group_id = hashlib.sha256("|".join(sorted(members)).encode()).hexdigest()[:20]
        for member in members:
            result[member] = split
        assignments.append({"group_id": group_id, "meme_ids": sorted(members), "split": split})
    anchors = json.loads(json.dumps(prior_anchors))
    for row in rows:
        for anchor_type, values in anchors_for(row).items():
            entries = anchors.setdefault(anchor_type, {})
            for value in values:
                split = result[row["meme_id"]]
                if value in entries and entries[value] != split:
                    raise PartitionConflictError("split conflict: a fixed partition anchor would move",
                                                 conflict_meme_ids=[row["meme_id"]], requested_or_fixed_splits=[entries[value], split])
                entries[value] = split
    return result, assignments, anchors


def _snapshot_impl(dataset_path, cutoff, output_dir, db=None, *, split_config=None, prior_partition=None, audit):
    """Create a time-safe snapshot and equal-information training exports."""
    dataset = Path(dataset_path).resolve()
    manifest_path = dataset / "manifest.json" if dataset.is_dir() else dataset
    manifest = read_json(manifest_path)
    records_path = (manifest_path.parent / manifest["records_path"]).resolve()
    _require(records_path.is_relative_to(manifest_path.parent), "manifest records path escapes its dataset")
    _require(hash_file(records_path) == manifest["records_sha256"], "dataset records changed after ingest")
    rows = validate_records(_read_rows(records_path))
    cutoff_time = _time(cutoff)
    output = _new_output(output_dir)
    prior, prior_sources = None, []
    if prior_partition is not None:
        prior_path = Path(prior_partition).resolve()
        if prior_path.is_dir():
            prior_path = prior_path / "manifest.json"
        previous_manifest = read_json(prior_path)
        _require(previous_manifest.get("schema_version") == "multimodal-meme-snapshot-v1", "prior partition must be an immutable snapshot manifest")
        _require(_time(previous_manifest["cutoff"]) <= cutoff_time, "prior partition comes from a later cutoff")
        prior_split = prior_path.parent / "split_config.json"
        _require(hash_file(prior_split) == previous_manifest["split_config_sha256"], "prior partition file changed")
        prior = read_json(prior_split)
        _require(all(group.get("split") in SPLITS for group in prior.get("assignments", [])), "invalid prior partition assignments")
        _require(all(split in SPLITS for entries in prior.get("anchors", {}).values() for split in entries.values()), "invalid prior partition anchors")
        prior_sources = [prior_path, prior_split]
    default_config = {"seed": prior["seed"], "fractions": prior["fractions"]} if prior else DEFAULT_SPLIT
    config = read_json(split_config) if isinstance(split_config, (str, Path)) else split_config or default_config
    if prior:
        _require(config == default_config, "split seed/fractions must match the fixed prior partition")
    selected = _latest(rows, "meme_id", cutoff_time)
    excluded, temporal_rows = [], []
    for row in selected:
        if row.get("published_at") and _time(row["published_at"]) > cutoff_time:
            excluded.append({"meme_id": row["meme_id"], "reason": "published_after_cutoff"})
            continue
        row["assets"] = [a for a in row["assets"] if _time(a["available_at"]) <= cutoff_time and (not a.get("published_at") or _time(a["published_at"]) <= cutoff_time)]
        assets = {a["asset_id"] for a in row["assets"]}
        row["annotations"] = [a for a in row["annotations"] if _time(a["available_at"]) <= cutoff_time]
        annotations = {a["annotation_id"] for a in row["annotations"]}
        row["knowledge"] = [k for k in _latest(row["knowledge"], "knowledge_id", cutoff_time) if set(k["source_asset_ids"]) <= assets and set(k.get("derived_from_annotation_ids", [])) <= annotations]
        row["relationships"] = [r for r in row["relationships"] if _time(r["available_at"]) <= cutoff_time and set(r.get("basis_asset_ids", [])) <= assets]
        row["creation_examples"] = [e for e in row["creation_examples"] if _time(e["available_at"]) <= cutoff_time and set(e.get("target_asset_ids", [])) <= assets]
        temporal_rows.append(row)
    selected_ids = {r["meme_id"] for r in temporal_rows}
    for row in temporal_rows:
        row["relationships"] = [r for r in row["relationships"] if r["target_meme_id"] in selected_ids]
    spec = _spec("snapshot", records_path, temporal_rows, {"operation": "snapshot", "cutoff": cutoff, "split_config": config, "network_requests": 0,
                 "gold_policy": "evaluation targets excluded from retrieval and training; both arms receive identical permitted supervision"})
    spec["data_snapshots"].extend({"id": str(path), "sha256": hash_file(path)} for path in prior_sources)
    spec["time_cutoff"] = cutoff
    log, record = audit
    try:
        output.mkdir(parents=True)
        assignments, groups, anchors = _split_groups(temporal_rows, config, prior)
        contexts, shared, eval_inputs, eval_targets = [], [], [], []
        for row in temporal_rows:
            split = assignments[row["meme_id"]]
            row["split"] = split
            gold_ids = {a["annotation_id"] for a in row["annotations"] if a["role"] == "evaluation_gold"}
            safe_assets = [a for a in row["assets"] if a["status"] == "available" and a["purpose"] != "evaluation_only"]
            safe_ids = {a["asset_id"] for a in safe_assets}
            safe_knowledge = [k for k in row["knowledge"] if k["purpose"] == "source_context" and set(k["source_asset_ids"]) <= safe_ids and not set(k.get("derived_from_annotation_ids", [])) & gold_ids]
            if split == "train":
                contexts.append({"meme_id": row["meme_id"], "family_id": row["family_id"], "version": row["version"],
                                 "available_at": row["available_at"], "provenance": row["provenance"], "assets": [a for a in safe_assets if a["purpose"] == "source_evidence"],
                                 "knowledge": safe_knowledge, "relationships": [r for r in row["relationships"] if assignments[r["target_meme_id"]] == "train"]})
            for example in row["creation_examples"]:
                base = {"example_id": example["example_id"], "meme_id": row["meme_id"], "family_id": row["family_id"],
                        "brief": example["brief"], "available_at": example["available_at"], "provenance": example["provenance"], "split": split}
                targets = [a for a in row["assets"] if a["asset_id"] in example.get("target_asset_ids", [])]
                if split == "train" and example["purpose"] == "training_supervision":
                    _require(all(a["status"] == "available" and a["purpose"] != "evaluation_only" for a in targets), "training target is unavailable or evaluation-only")
                    shared.append({**base, "target_text": example.get("target_text"), "target_assets": targets, "purpose": "training_supervision"})
                elif split in {"dev", "test"}:
                    eval_inputs.append(base)
                    eval_targets.append({**base, "target_text": example.get("target_text"), "target_assets": targets, "annotations": [a for a in row["annotations"] if a["role"] == "evaluation_gold"], "purpose": "evaluation_only"})
        files = {"records.jsonl": temporal_rows, "rag_context.jsonl": contexts, "shared_training_examples.jsonl": shared,
                 "rag_examples.jsonl": shared, "finetune_examples.jsonl": shared,
                 "evaluation_inputs.jsonl": eval_inputs, "evaluation_targets.protected.jsonl": eval_targets}
        for name, values in files.items():
            _write_rows(output / name, values)
        _require(hash_file(output / "rag_examples.jsonl") == hash_file(output / "finetune_examples.jsonl"), "training information differs between arms")
        split_path = output / "split_config.json"
        split_path.write_text(json.dumps({**config, "grouping": ["family_id", "asset_sha256", "source_group_id", "parent_asset_ids", "time_eligible_reuse_relationships"], "assignments": groups, "anchors": anchors,
                                         "prior_partition_manifest": str(prior_sources[0]) if prior_sources else None,
                                         "purpose": "preparatory family partition; not the final heldout-family historical-support/evaluation split"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        final = {"schema_version": "multimodal-meme-snapshot-v1", "run_id": record["run_id"], "created_at": datetime.now(timezone.utc).isoformat(),
                 "cutoff": cutoff_time.isoformat(), "source_manifest": str(manifest_path), "source_records_sha256": hash_file(records_path),
                 "record_count": len(temporal_rows), "records_path": "records.jsonl", "records_sha256": hash_file(output / "records.jsonl"),
                 "files": {name: {"count": len(values), "sha256": hash_file(output / name)} for name, values in files.items()},
                 "split_config_sha256": hash_file(split_path), "group_count": len(groups), "exclusions": excluded,
                 "permitted_for_both_arms": ["rag_context.jsonl", "shared_training_examples.jsonl"],
                 "protected_not_for_retrieval_or_training": ["records.jsonl", "evaluation_targets.protected.jsonl"],
                 "evaluation_inputs": "evaluation_inputs.jsonl", "multimodal_completeness_claim": False, "network_requests": 0,
                 "human_review_minutes": 0, "provenance": _provenance(temporal_rows)}
        final["split_status"] = "preparatory_group_partition; adaptation_support_vs_evaluation_axis_not_assigned"
        final["partition_policy"] = "Existing assignments fixed by prior manifest; new bridges across partitions block export and require a study reset. Always pass --prior-partition for later snapshots."
        final["prior_partition_manifest"] = str(prior_sources[0]) if prior_sources else None
        final["research_readiness"] = "Preparation only: not a completed scientific comparison or benchmark; temporal adaptation support/evaluation split and actual multimodal dataset remain separate requirements."
        final["media_decode_verified"] = False
        destination = output / "manifest.json"
        destination.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.finish(record["run_id"], status="succeeded", notes="Exported a time-filtered dataset with family/asset/reuse-group splits, identical permitted training supervision for retrieval and fine-tuning, and protected evaluation targets. No training or evaluation was performed.",
                   artifacts=[destination, split_path, *[output / name for name in files]], actual_cost={"amount": 0, "currency": "USD", "human_minutes": 0})
        return final
    except Exception as exc:
        artifacts = [_failure_artifact(output, record, exc)]
        if isinstance(exc, PartitionConflictError) and output.exists():
            conflict_path = output / "partition_conflict.json"
            conflict_path.write_text(json.dumps({"run_id": record["run_id"], "export_blocked": True, "reason": str(exc),
                                                "conflict_meme_ids": exc.conflict_meme_ids,
                                                "requested_or_fixed_splits": exc.requested_or_fixed_splits,
                                                "prior_partition_manifest": str(prior_sources[0]) if prior_sources else None,
                                                "resolution": "Explicit new study partition and reset from uncontaminated model state; no automatic reassignment"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            artifacts.append(conflict_path)
        log.finish(record["run_id"], status="failed", notes="Dataset snapshot failed: " + type(exc).__name__ + ("; incompatible partitions were blocked, never reassigned" if isinstance(exc, PartitionConflictError) else ""), artifacts=artifacts, actual_cost={"amount": 0, "currency": "USD", "human_minutes": 0})
        raise


def snapshot(dataset_path, cutoff, output_dir, db=None, *, split_config=None, prior_partition=None,
             retry_of=None, retry_reason=None):
    """Register measurable inputs before validation and time/partition checks."""
    dataset = Path(dataset_path).resolve()
    manifest_path = dataset / "manifest.json" if dataset.is_dir() else dataset
    manifest = read_json(manifest_path)
    records_path = (manifest_path.parent / manifest["records_path"]).resolve()
    _require(records_path.is_relative_to(manifest_path.parent), "manifest records path escapes its dataset")
    raw_rows = _read_rows(records_path)
    _new_output(output_dir)
    assets, inspection_errors = _asset_snapshots(raw_rows)
    config = read_json(split_config) if isinstance(split_config, (str, Path)) else split_config
    sources = [manifest_path]
    if isinstance(split_config, (str, Path)):
        sources.append(Path(split_config).resolve())
    if prior_partition is not None:
        previous = Path(prior_partition).resolve()
        previous = previous / "manifest.json" if previous.is_dir() else previous
        sources.extend([previous, previous.parent / "split_config.json"])
    spec = _spec("snapshot", records_path, raw_rows, {"operation": "snapshot", "cutoff": cutoff,
                 "requested_split_config": config, "prior_partition": str(prior_partition) if prior_partition else None,
                 "asset_inspection_errors": inspection_errors, "network_requests": 0,
                 "gold_policy": "Evaluation-only labels excluded from both arms; same permitted supervision"})
    spec["data_snapshots"].extend(assets)
    spec["data_snapshots"].extend({"id": str(path), "sha256": hash_file(path)} for path in dict.fromkeys(sources) if path.is_file())
    if isinstance(cutoff, str):
        try:
            spec["time_cutoff"] = _time(cutoff).isoformat()
        except ValueError:
            pass
    log = ResearchLog(db or ROOT / "logs/research.sqlite3")
    record = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    try:
        return _snapshot_impl(dataset_path, cutoff, output_dir, db, split_config=split_config,
                              prior_partition=prior_partition, audit=(log, record))
    except Exception as exc:
        if log.status(record["run_id"])["status"] == "started":
            failure = _failure_artifact(output_dir, record, exc)
            log.finish(record["run_id"], status="failed", notes="Dataset snapshot validation failed: " + type(exc).__name__ + "; see failure.json", artifacts=[failure],
                       actual_cost={"amount": 0, "currency": "USD", "human_minutes": 0})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "ingest"):
        command = commands.add_parser(name)
        command.add_argument("--input", required=True, type=Path)
        if name == "ingest":
            command.add_argument("--output-dir", required=True, type=Path)
            command.add_argument("--db", type=Path)
            command.add_argument("--retry-of")
            command.add_argument("--retry-reason")
    command = commands.add_parser("snapshot")
    command.add_argument("--dataset", required=True, type=Path)
    command.add_argument("--cutoff", required=True)
    command.add_argument("--output-dir", required=True, type=Path)
    command.add_argument("--db", type=Path)
    command.add_argument("--split-config", type=Path)
    command.add_argument("--prior-partition", type=Path, help="Previous immutable snapshot manifest; mandatory by protocol for later continual snapshots")
    command.add_argument("--retry-of")
    command.add_argument("--retry-reason")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            rows = validate_records(_read_rows(args.input))
            result = {"valid": True, "record_count": len(rows), "provenance": _provenance(rows), "asset_bytes_verified": True}
        elif args.command == "ingest":
            result = ingest(args.input, args.output_dir, args.db, retry_of=args.retry_of, retry_reason=args.retry_reason)
        else:
            result = snapshot(args.dataset, args.cutoff, args.output_dir, args.db, split_config=args.split_config, prior_partition=args.prior_partition,
                              retry_of=args.retry_of, retry_reason=args.retry_reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except DuplicateRunError as exc:
        print(json.dumps({"error": "duplicate_run", "existing_run_id": exc.existing["run_id"]}))
        return 3
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc) if isinstance(exc, ValueError) else "filesystem operation failed"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
