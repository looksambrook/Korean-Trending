"""Two-axis, time-limited creation data preparation; no model execution.

Family partitions remain train/dev/test. A caller must explicitly choose
whether historical support from selected locked test families is available to
both CR and CF. Declared usage policy is recorded, not legally adjudicated.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from multimodal_meme_dataset import (ROOT, SCHEMA, PartitionConflictError, _asset_snapshots, _latest,
    _new_output, _project_asset, _provenance, _read_rows, _require, _split_groups, _time,
    _write_rows, validate_records)
from research_log import DuplicateRunError, ResearchLog, hash_file, read_json

MODES = {"train_only", "locked_family_past_support"}


class EvaluationExposureConflict(ValueError):
    def __init__(self, conflicts):
        super().__init__("evaluation item was already prepared as adaptation supervision; explicit retention-probe design is required instead of treating it as unseen evaluation")
        self.conflicts = conflicts


def _exposure(example, targets, *, phase):
    target = {"text": example.get("target_text"), "asset_hashes": sorted(a["sha256"] for a in targets if a.get("sha256"))}
    digest = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"meme_id": example["meme_id"], "example_id": example["example_id"],
            "brief_sha256": digest(example["brief"]), "target_sha256": digest(target),
            "complete_target_signature": bool(example.get("target_text")) or bool(targets) and all(a.get("sha256") for a in targets),
            "exposure_basis": phase, "actual_model_exposure_claim": False}


def _manifest_path(path):
    path = Path(path).resolve()
    return path / "manifest.json" if path.is_dir() else path


def _temporal_rows(rows, cutoff):
    """Select a whole historical record revision before examining its fields."""
    selected = copy.deepcopy(_latest(rows, "meme_id", cutoff))
    result = []
    for row in selected:
        if row.get("published_at") and _time(row["published_at"]) > cutoff:
            continue
        row["assets"] = [a for a in row["assets"] if _time(a["available_at"]) <= cutoff and
                         (not a.get("published_at") or _time(a["published_at"]) <= cutoff)]
        ids = {a["asset_id"] for a in row["assets"]}
        row["annotations"] = [a for a in row["annotations"] if _time(a["available_at"]) <= cutoff]
        annotation_ids = {a["annotation_id"] for a in row["annotations"]}
        row["knowledge"] = [k for k in _latest(row["knowledge"], "knowledge_id", cutoff)
                            if set(k["source_asset_ids"]) <= ids and set(k.get("derived_from_annotation_ids", [])) <= annotation_ids]
        row["relationships"] = [r for r in row["relationships"] if _time(r["available_at"]) <= cutoff and set(r.get("basis_asset_ids", [])) <= ids]
        row["creation_examples"] = [e for e in row["creation_examples"] if _time(e["available_at"]) <= cutoff and set(e.get("target_asset_ids", [])) <= ids]
        result.append(row)
    mids = {r["meme_id"] for r in result}
    for row in result:
        row["relationships"] = [r for r in row["relationships"] if r["target_meme_id"] in mids]
    return result


def _family_partition(rows, config, prior):
    partition_rows = copy.deepcopy(rows)
    for row in partition_rows:
        # A creation example's purpose/split is never a family assignment.
        row["creation_examples"] = []
    assigned, groups, anchors = _split_groups(partition_rows, config, prior)
    return assigned, {**config, "assignments": groups, "anchors": anchors}


def _load_partition(path, source_cutoff):
    manifest_path = _manifest_path(path)
    manifest = read_json(manifest_path)
    schema = manifest.get("schema_version")
    if schema == "multimodal-meme-snapshot-v1":
        config_path = manifest_path.parent / "split_config.json"
        digest = manifest["split_config_sha256"]
        available = manifest["cutoff"]
    elif schema == "multimodal-creation-two-axis-v1":
        config_path = manifest_path.parent / "partition_manifest.json"
        digest = manifest["partition_manifest_sha256"]
        available = manifest["partition_assignment_basis_cutoff"]
    else:
        raise ValueError("partition input must be an immutable supported snapshot manifest")
    _require(_time(available) <= source_cutoff, "partition contains assignments based on a later cutoff")
    _require(hash_file(config_path) == digest, "partition hash mismatch")
    prior = read_json(config_path)
    _require(bool(prior.get("anchors", {}).get("family_id")), "partition requires fixed family anchors")
    return manifest_path, config_path, prior


def _usage_policy(value):
    value = read_json(value) if isinstance(value, (str, Path)) else copy.deepcopy(value or {})
    _require(isinstance(value, dict) and set(value) <= {"default", "assets", "examples", "declared_by", "basis"}, "invalid support usage policy")
    policies = [value.get("default", {})]
    for field in ("assets", "examples"):
        _require(isinstance(value.get(field, {}), dict), "usage exceptions must map IDs to permissions")
        policies.extend(value.get(field, {}).values())
    for policy in policies:
        _require(isinstance(policy, dict) and set(policy) <= {"retrieval", "training"}, "usage policy supports retrieval and training only")
        _require(all(v is None or isinstance(v, bool) for v in policy.values()), "usage permission must be true, false or null")
    return value


def _allowed(policy, category, identity):
    declared = {**policy.get("default", {}), **policy.get(category, {}).get(identity, {})}
    return declared.get("retrieval") is True and declared.get("training") is True


def _support_assets(rows, policy):
    """Reject known gold derivatives and unavailable/undeclared-use content."""
    assets = {}
    tainted, reasons = set(), {}
    for row in rows:
        for asset in row["assets"]:
            previous = assets.get(asset["asset_id"])
            _require(previous is None or previous == asset, "same asset ID has conflicting current content")
            assets[asset["asset_id"]] = asset
            if asset["purpose"] == "evaluation_only":
                tainted.add(asset["asset_id"])
                reasons[asset["asset_id"]] = "evaluation_only_asset"
        for example in row["creation_examples"]:
            if example["purpose"] == "evaluation_only":
                for identity in example.get("target_asset_ids", []):
                    tainted.add(identity)
                    reasons[identity] = "evaluation_target_asset"
    changed = True
    while changed:
        changed = False
        hashes = {assets[a].get("sha256") for a in tainted if a in assets} - {None}
        for identity, asset in assets.items():
            if identity not in tainted and (asset.get("sha256") in hashes or set(asset.get("parent_asset_ids", [])) & tainted):
                tainted.add(identity)
                reasons[identity] = "known_evaluation_asset_bytes_or_declared_descendant"
                changed = True
    allowed = set()
    for identity, asset in assets.items():
        if identity in tainted:
            continue
        if asset["status"] != "available":
            reasons[identity] = "asset_not_available"
        elif not _allowed(policy, "assets", identity):
            reasons[identity] = "retrieval_and_training_use_not_both_declared_allowed"
        else:
            allowed.add(identity)
    # A hidden future/missing/restricted parent cannot be laundered through OCR.
    changed = True
    while changed:
        changed = False
        for identity in list(allowed):
            if not set(assets[identity].get("parent_asset_ids", [])) <= allowed:
                allowed.remove(identity)
                reasons[identity] = "parent_asset_not_eligible_for_support"
                changed = True
    return assets, allowed, reasons


def _metadata(row, partition):
    return {"meme_id": row["meme_id"], "family_id": row["family_id"], "family_partition": partition,
            "record_version": row["version"], "record_available_at": row["available_at"], "provenance": row["provenance"]}


def prepare(dataset_path, partition_manifest, output_dir, *, mode, source_cutoff, evaluation_at,
            locked_family_ids, support_permissions=None, db=None, retry_of=None, retry_reason=None):
    """Build shared adaptation inputs and separate evaluation requests/targets.

    mode has no default. locked_family_ids does not modify family assignments.
    An absent usage declaration is unknown, not implicitly permitted.
    """
    output = _new_output(output_dir)
    dataset_manifest_path = _manifest_path(dataset_path)
    dataset_manifest = read_json(dataset_manifest_path)
    records_path = (dataset_manifest_path.parent / dataset_manifest["records_path"]).resolve()
    _require(records_path.is_relative_to(dataset_manifest_path.parent), "dataset records path escapes manifest directory")
    raw_rows = _read_rows(records_path)
    measured_assets, inspection_errors = _asset_snapshots(raw_rows)
    sources = [records_path, dataset_manifest_path, _manifest_path(partition_manifest)]
    partition_dir = sources[-1].parent
    sources.extend(p for p in [partition_dir / "split_config.json", partition_dir / "partition_manifest.json"] if p.exists())
    if isinstance(support_permissions, (str, Path)):
        sources.append(Path(support_permissions).resolve())
    spec = {"objective": "Prepare explicitly chosen two-axis meme creation adaptation/evaluation data; no research or model execution",
            "stage": "desk_research", "parameters": {"mode": mode, "source_cutoff": source_cutoff, "evaluation_at": evaluation_at,
                "locked_family_ids": locked_family_ids, "support_permissions": str(support_permissions) if isinstance(support_permissions, (str, Path)) else support_permissions,
                "registration": "before validation and export", "asset_inspection_errors": inspection_errors},
            "data_snapshots": [{"id": str(p), "sha256": hash_file(p)} for p in dict.fromkeys(sources) if p.is_file()] + measured_assets,
            "code_hashes": {name: hash_file(ROOT / "src" / name) for name in ("multimodal_creation_split.py", "multimodal_meme_dataset.py", "research_log.py")},
            "prompt_hashes": {}, "config_hashes": {"record_schema": hash_file(SCHEMA)},
            "modality": sorted({a.get("modality") for r in raw_rows for a in r.get("assets", []) if isinstance(a, dict) and a.get("modality") in {"text", "image", "audio", "video"}}) or ["text"],
            "provenance": _provenance(raw_rows)}
    log = ResearchLog(db or ROOT / "logs/research.sqlite3")
    record = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    try:
        _require(mode in MODES, "mode must be explicitly train_only or locked_family_past_support")
        _require(isinstance(locked_family_ids, list) and locked_family_ids and all(isinstance(x, str) and x for x in locked_family_ids), "locked_family_ids must be explicit nonempty family IDs")
        cutoff, evaluation_time = _time(source_cutoff), _time(evaluation_at)
        _require(cutoff <= evaluation_time, "source_cutoff cannot exceed evaluation_at")
        _require(hash_file(records_path) == dataset_manifest["records_sha256"], "dataset records hash mismatch")
        rows = validate_records(raw_rows)
        registered = {s["id"].removeprefix("asset:"): s["sha256"] for s in measured_assets}
        for row in rows:
            for asset in row["assets"]:
                if asset.get("local_path"):
                    _require(registered.get(str(_project_asset(asset["local_path"]))) == asset["sha256"], "asset changed after run registration")
        policy = _usage_policy(support_permissions)
        partition_path, partition_config_path, prior = _load_partition(partition_manifest, cutoff)
        previous_manifest = read_json(partition_path)
        prior_exposure = previous_manifest.get("prepared_adaptation_exposure", [])
        family_anchors = prior["anchors"]["family_id"]
        _require(all(family_anchors.get(f) == "test" for f in locked_family_ids), "selected locked families must already have fixed test assignments")
        config = {"seed": prior["seed"], "fractions": prior["fractions"]}
        support_rows = _temporal_rows(rows, cutoff)
        evaluation_rows = _temporal_rows(rows, evaluation_time)
        support_partition, support_partition_state = _family_partition(support_rows, config, prior)
        evaluation_partition, final_partition = _family_partition(evaluation_rows, config, support_partition_state)
        assets, permitted_assets, asset_reasons = _support_assets(support_rows, policy)
        contexts, supervision, briefs, gold, exclusions = [], [], [], [], []
        permitted_families = set(locked_family_ids) if mode == "locked_family_past_support" else set()
        support_mids = {r["meme_id"] for r in support_rows if support_partition[r["meme_id"]] == "train" or r["family_id"] in permitted_families}
        for row in support_rows:
            mid, family = row["meme_id"], row["family_id"]
            if mid not in support_mids:
                exclusions.append({"meme_id": mid, "reason": "family_not_permitted_for_adaptation_in_selected_mode"})
                continue
            meta = _metadata(row, support_partition[mid])
            row_assets = [a for a in row["assets"] if a["asset_id"] in permitted_assets and a["purpose"] == "source_evidence"]
            gold_annotations = {a["annotation_id"] for a in row["annotations"] if a["role"] == "evaluation_gold"}
            knowledge = []
            for item in row["knowledge"]:
                if (item["purpose"] == "source_context" and set(item["source_asset_ids"]) <= permitted_assets and
                        not set(item.get("derived_from_annotation_ids", [])) & gold_annotations):
                    knowledge.append(item)
                else:
                    exclusions.append({"meme_id": mid, "knowledge_id": item["knowledge_id"], "reason": "gold_derived_or_ineligible_source_knowledge"})
            if row_assets or knowledge:
                contexts.append({**meta, "temporal_role": "adaptation_support", "assets": row_assets, "knowledge": knowledge,
                                 "relationships": [r for r in row["relationships"] if r["target_meme_id"] in support_mids and set(r.get("basis_asset_ids", [])) <= permitted_assets]})
            for example in row["creation_examples"]:
                identity = example["example_id"]
                if example["purpose"] != "training_supervision":
                    exclusions.append({"meme_id": mid, "example_id": identity, "reason": "evaluation_only_example_never_adaptation"})
                elif not _allowed(policy, "examples", identity):
                    exclusions.append({"meme_id": mid, "example_id": identity, "reason": "example_use_not_both_declared_allowed"})
                elif not set(example.get("target_asset_ids", [])) <= permitted_assets:
                    exclusions.append({"meme_id": mid, "example_id": identity, "reason": "target_asset_not_eligible_for_shared_support"})
                else:
                    supervision.append({**meta, "temporal_role": "adaptation_support", "example_id": identity,
                        "brief": example["brief"], "target_text": example.get("target_text"),
                        "target_assets": [assets[a] for a in example.get("target_asset_ids", [])],
                        "available_at": example["available_at"], "provenance": example["provenance"],
                        "author": example.get("author"), "declared_example_split": example.get("split"), "purpose": "training_supervision"})
        for identity, reason in sorted(asset_reasons.items()):
            exclusions.append({"asset_id": identity, "reason": reason})
        # Evaluation material is derived independently from the evaluation-time
        # revision, never spliced into the historical support record.
        for row in evaluation_rows:
            if row["family_id"] not in locked_family_ids:
                continue
            meta = _metadata(row, evaluation_partition[row["meme_id"]])
            for example in row["creation_examples"]:
                if example["purpose"] != "evaluation_only":
                    continue
                brief = {**meta, "temporal_role": "evaluation_brief", "example_id": example["example_id"],
                         "brief": example["brief"], "available_at": example["available_at"], "provenance": example["provenance"],
                         "author": example.get("author"), "declared_example_split": example.get("split")}
                briefs.append(brief)
                gold.append({**meta, "temporal_role": "evaluation_target_and_annotations", "example_id": example["example_id"],
                             "target_text": example.get("target_text"),
                             "target_assets": [a for a in row["assets"] if a["asset_id"] in example.get("target_asset_ids", [])],
                             "provided_annotations": [a for a in row["annotations"] if a["role"] == "evaluation_gold"],
                             "provided_gold_knowledge": [k for k in row["knowledge"] if k["purpose"] == "evaluation_gold"],
                             "available_at": example["available_at"], "provenance": example["provenance"],
                             "independent_gold_verified": False})
        prepared_exposure = list(prior_exposure)
        prepared_exposure.extend(_exposure(item, item["target_assets"], phase="current_prepared_support") for item in supervision)
        conflicts = []
        for brief, target in zip(briefs, gold):
            current = _exposure({**brief, "target_text": target["target_text"]}, target["target_assets"], phase="evaluation")
            for old in prepared_exposure:
                same_identity = (old["meme_id"], old["example_id"]) == (current["meme_id"], current["example_id"])
                same_pair = (old.get("complete_target_signature") and current["complete_target_signature"] and
                             old["brief_sha256"] == current["brief_sha256"] and old["target_sha256"] == current["target_sha256"])
                if same_identity or same_pair:
                    conflicts.append({"evaluation_meme_id": current["meme_id"], "evaluation_example_id": current["example_id"],
                                      "adaptation_meme_id": old["meme_id"], "adaptation_example_id": old["example_id"],
                                      "reason": "same_example_identity" if same_identity else "same_exact_brief_and_target",
                                      "exposure_basis": old.get("exposure_basis"), "actual_model_exposure_claim": False})
        if conflicts:
            raise EvaluationExposureConflict(conflicts)
        output.mkdir(parents=True)
        exported = {"shared_context.jsonl": contexts, "shared_supervision.jsonl": supervision,
                    "evaluation_briefs.jsonl": briefs, "protected_gold.jsonl": gold, "exclusions.jsonl": exclusions}
        for name, values in exported.items():
            _write_rows(output / name, values)
        final_partition.update({"parent_manifest": str(partition_path), "parent_manifest_sha256": hash_file(partition_path),
                                "assignment_basis_cutoff": evaluation_at, "family_partition_and_temporal_role_are_distinct": True})
        partition_output = output / "partition_manifest.json"
        partition_output.write_text(json.dumps(final_partition, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        shared = {name: {"path": name, "sha256": hash_file(output / name), "count": len(exported[name])}
                  for name in ("shared_context.jsonl", "shared_supervision.jsonl")}
        result = {"schema_version": "multimodal-creation-two-axis-v1", "run_id": record["run_id"],
                  "created_at": datetime.now(timezone.utc).isoformat(), "source_cutoff": cutoff.isoformat(), "evaluation_at": evaluation_time.isoformat(),
                  "mode": mode, "mode_status": "explicit_caller_choice_not_a_user_study_confirmation", "locked_family_ids": locked_family_ids,
                  "source_dataset_manifest": str(dataset_manifest_path), "source_records_sha256": hash_file(records_path),
                  "prior_partition_manifest": str(partition_path), "prior_partition_sha256": hash_file(partition_path),
                  "partition_manifest_sha256": hash_file(partition_output), "partition_assignment_basis_cutoff": evaluation_time.isoformat(),
                  "support_record_versions": [{"meme_id": r["meme_id"], "version": r["version"]} for r in support_rows],
                  "evaluation_record_versions": [{"meme_id": r["meme_id"], "version": r["version"]} for r in evaluation_rows],
                  "arms": {"CR": copy.deepcopy(shared), "CF": copy.deepcopy(shared)},
                  "prepared_adaptation_exposure": list({(e["meme_id"], e["example_id"], e["brief_sha256"], e["target_sha256"]): e for e in prepared_exposure}.values()),
                  "prepared_exposure_is_actual_training": False,
                  "files": {name: {"count": len(values), "sha256": hash_file(output / name)} for name, values in exported.items()},
                  "provenance": _provenance(rows), "support_permissions": policy,
                  "usage_policy_notice": "Dataset-author/caller declaration; this module does not decide or guarantee legal usage rights.",
                  "evaluation_files_forbidden_in_adaptation": ["evaluation_briefs.jsonl", "protected_gold.jsonl", "exclusions.jsonl"],
                  "methods_locked_by_this_export": False, "is_zero_shot_family_evaluation": False if mode == "locked_family_past_support" else None,
                  "actual_research_run_performed": False, "independent_gold_verified": False,
                  "model_calls": 0, "model_weights_trained": False, "network_requests": 0, "media_decode_verified": False,
                  "monetary_cost": None, "local_compute_cost_measured": False, "human_review_minutes": 0,
                  "readiness": "Bounded dataset preparation only; actual data/model execution, method locks and independent evaluation remain unverified."}
        manifest_output = output / "manifest.json"
        manifest_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.finish(record["run_id"], status="succeeded", notes="Prepared separate fixed-family and temporal-role views. Both arms reference identical permitted context/supervision files. Evaluation-only data is separate. No model, actual research execution, independent gold verification or native media decoding.",
                   artifacts=[manifest_output, partition_output, *[output / name for name in exported]],
                   actual_cost={"amount": None, "currency": None, "human_minutes": 0})
        return result
    except Exception as exc:
        output.mkdir(parents=True, exist_ok=True)
        failure = {"run_id": record["run_id"], "status": "failed", "error_type": type(exc).__name__,
                   "reason": str(exc) if isinstance(exc, ValueError) else type(exc).__name__, "export_blocked": True,
                   "actual_research_run_performed": False, "model_calls": 0}
        if isinstance(exc, PartitionConflictError):
            failure.update(conflict_meme_ids=exc.conflict_meme_ids, requested_or_fixed_splits=exc.requested_or_fixed_splits)
        if isinstance(exc, EvaluationExposureConflict):
            failure["evaluation_exposure_conflicts"] = exc.conflicts
        failure_path = output / "failure.json"
        failure_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.finish(record["run_id"], status="failed", notes="Two-axis preparation blocked: " + type(exc).__name__ + "; see failure.json; originals preserved",
                   artifacts=[failure_path], actual_cost={"amount": None, "currency": None, "human_minutes": 0})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--partition", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=sorted(MODES))
    parser.add_argument("--source-cutoff", required=True)
    parser.add_argument("--evaluation-at", required=True)
    parser.add_argument("--locked-family", action="append", required=True)
    parser.add_argument("--support-permissions", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.dataset, args.partition, args.output_dir, mode=args.mode,
                         source_cutoff=args.source_cutoff, evaluation_at=args.evaluation_at,
                         locked_family_ids=args.locked_family, support_permissions=args.support_permissions,
                         db=args.db, retry_of=args.retry_of, retry_reason=args.retry_reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except DuplicateRunError as exc:
        print(json.dumps({"error": "duplicate_run", "existing_run_id": exc.existing["run_id"]}))
        return 3
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": type(exc).__name__, "reason": str(exc) if isinstance(exc, ValueError) else "filesystem failure"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
