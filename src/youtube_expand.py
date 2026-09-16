"""Expand one observed candidate and build a source-linked report in one command.

New work is bounded to one public search page and at most three public watch
pages. --reuse-search-manifest plus --reuse-metadata-manifest makes the workflow
entirely offline. Existing collection, candidates and child-run files are never
overwritten. Expansion remains posthoc and is excluded from discovery scores.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from research_log import DuplicateRunError, ResearchLog, hash_file, read_json
from youtube_video_metadata import collect as collect_metadata
from youtube_web_search import collect as collect_search
from youtube_watch import build_report, rows

ROOT = Path(__file__).resolve().parents[1]


class WorkflowError(ValueError):
    """All messages are fixed reason codes, without source text or credentials."""


def _require(condition, reason):
    if not condition:
        raise WorkflowError(reason)


def _norm(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _path(value):
    value = Path(value)
    return value if value.is_absolute() else ROOT / value


def _save(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _source_inputs(manifest_path, candidate_dir, candidate_id):
    manifest_path, candidate_dir = Path(manifest_path).resolve(), Path(candidate_dir).resolve()
    files = {"collection_manifest": manifest_path, "collection_observations": manifest_path.parent / "observations.jsonl",
             "candidate_summary": candidate_dir / "summary.json", "candidate_records": candidate_dir / "candidates.jsonl",
             "candidate_exclusions": candidate_dir / "exclusions.jsonl"}
    if (candidate_dir / "suppressed.jsonl").exists():
        files["candidate_suppressed"] = candidate_dir / "suppressed.jsonl"
    hashes = {key: hash_file(path) for key, path in files.items()}
    manifest, summary = read_json(files["collection_manifest"]), read_json(files["candidate_summary"])
    _require(manifest.get("provenance") == "actual_collected", "collection_must_be_actual")
    _require(isinstance(manifest.get("run_id"), str) and manifest["run_id"], "missing_collection_run")
    _require(summary.get("input_sha256") == hashes["collection_observations"], "candidate_input_hash_mismatch")
    matches = [row for row in rows(files["candidate_records"]) if row.get("candidate_id") == candidate_id]
    _require(len(matches) == 1, "candidate_id_missing_or_ambiguous")
    candidate = matches[0]
    phrase = candidate.get("representative_phrase")
    _require(isinstance(phrase, str) and 1 <= len(phrase.strip()) <= 150, "invalid_candidate_phrase")
    phrase = phrase.strip()
    original_rows = rows(files["collection_observations"])
    source_by_id = {row.get("observation_id"): row for row in original_rows}
    _require(len(source_by_id) == len(original_rows), "duplicate_source_observation_id")
    evidence_ids = candidate.get("evidence_observation_ids")
    _require(isinstance(evidence_ids, list) and bool(evidence_ids), "candidate_has_no_evidence")
    _require(all(eid in source_by_id and source_by_id[eid].get("provenance") == "actual_collected" for eid in evidence_ids),
             "candidate_evidence_missing_or_not_actual")
    exact_evidence = [eid for eid in evidence_ids if isinstance(source_by_id[eid].get("text"), str)
                      and _norm(phrase) in _norm(source_by_id[eid]["text"])]
    _require(bool(exact_evidence), "candidate_phrase_absent_from_referenced_evidence")
    _require(all(hash_file(files[key]) == value for key, value in hashes.items()), "input_changed_during_read")
    return {"manifest": manifest, "summary": summary, "candidate": candidate, "phrase": phrase,
            "evidence_ids": evidence_ids, "exact_evidence_ids": exact_evidence, "source_by_id": source_by_id,
            "files": files, "hashes": hashes}


def _verified_child(manifest_path, schema):
    """Validate an existing child manifest and every immutable raw/data artifact."""
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json(manifest_path)
    _require(manifest.get("schema_version") == schema, "wrong_reuse_manifest_schema")
    _require(manifest.get("provenance") == "actual_collected" and manifest.get("test_only") is False,
             "reuse_requires_actual_collected_data")
    _require(manifest.get("status") in {"completed", "partial", "failed"}, "reuse_run_not_terminal")
    _require(isinstance(manifest.get("run_id"), str) and manifest["run_id"], "missing_reuse_run")
    artifact_records = manifest.get("artifacts", [])
    observation_path = manifest_path.parent / "observations.jsonl"
    observation_verified = False
    for artifact in artifact_records:
        path = Path(artifact.get("path", "")).resolve()
        _require(path.is_relative_to(manifest_path.parent), "reuse_artifact_outside_run_directory")
        _require(hash_file(path) == artifact.get("sha256"), "reuse_artifact_hash_mismatch")
        if path == observation_path:
            observation_verified = True
    _require(observation_verified, "reuse_observation_artifact_missing")
    child_rows = rows(observation_path)
    _require(len(child_rows) == manifest.get("observations"), "reuse_observation_count_mismatch")
    _require(all(row.get("provenance") == "actual_collected" for row in child_rows), "reuse_row_provenance_mismatch")
    return manifest, child_rows


def _verify_search(manifest_path, source):
    manifest, observations = _verified_child(manifest_path, "youtube-public-search-manifest-v1")
    config = manifest.get("config", {})
    _require(config.get("search_mode") == "discovered_phrase_expansion", "reuse_search_is_not_observed_phrase_expansion")
    _require(config.get("discovery_run_id") == source["manifest"]["run_id"], "reuse_search_collection_run_mismatch")
    _require(hash_file(_path(config["discovery_manifest"])) == source["hashes"]["collection_manifest"], "reuse_search_collection_manifest_mismatch")
    _require(hash_file(_path(config["discovery_observations"])) == source["hashes"]["collection_observations"], "reuse_search_collection_data_mismatch")
    snapshots = {item["id"]: item["sha256"] for item in manifest.get("input_snapshots", [])}
    _require(snapshots.get("discovery_manifest") == source["hashes"]["collection_manifest"] and
             snapshots.get("discovery_observations") == source["hashes"]["collection_observations"], "reuse_search_input_snapshot_mismatch")
    queries = config.get("queries", [])
    _require(len(queries) == 1 and _norm(queries[0].get("query", "")) == _norm(source["phrase"]), "reuse_query_candidate_mismatch")
    query_evidence = queries[0].get("evidence_ids", [])
    _require(bool(query_evidence) and set(query_evidence) <= set(source["exact_evidence_ids"]), "reuse_query_candidate_evidence_mismatch")
    _require(manifest.get("discovery_evaluation_eligible") is False, "reuse_search_must_remain_posthoc")
    _require(len(observations) <= 20 and manifest.get("http_attempts", 0) <= 1, "reuse_search_exceeds_workflow_scope")
    for row in observations:
        _require(row.get("discovery_evaluation_eligible") is False, "reuse_search_row_not_posthoc")
        _require(_norm(row.get("query", "")) == _norm(source["phrase"]), "reuse_row_query_mismatch")
        _require(set(row.get("discovery_evidence_ids", [])) <= set(query_evidence), "reuse_row_evidence_mismatch")
    return manifest, observations


def _verify_metadata(manifest_path, search_path, search_manifest, search_rows):
    manifest, observations = _verified_child(manifest_path, "youtube-watch-metadata-manifest-v1")
    config = manifest.get("config", {})
    _require(config.get("mode") == "posthoc_search_expansion", "reuse_metadata_is_not_posthoc")
    _require(config.get("source_run_id") == search_manifest["run_id"], "reuse_metadata_search_run_mismatch")
    current_search_path = Path(search_path).resolve()
    current_observation_path = current_search_path.parent / "observations.jsonl"
    _require(hash_file(_path(config["source_manifest"])) == hash_file(current_search_path), "reuse_metadata_search_manifest_mismatch")
    _require(hash_file(_path(config["source_observations"])) == hash_file(current_observation_path), "reuse_metadata_search_data_mismatch")
    snapshots = {item["id"]: item["sha256"] for item in manifest.get("input_snapshots", [])}
    _require(snapshots.get("source_manifest") == hash_file(current_search_path) and
             snapshots.get("source_observations") == hash_file(current_observation_path), "reuse_metadata_snapshot_mismatch")
    source_ids = {row["video_id"] for row in search_rows}
    requested_ids = config.get("video_ids", [])
    _require(isinstance(requested_ids, list) and 1 <= len(requested_ids) <= 3 and set(requested_ids) <= source_ids,
             "reuse_metadata_video_selection_mismatch")
    _require(len(observations) <= 3 and manifest.get("http_attempts", 0) <= 3, "reuse_metadata_exceeds_workflow_scope")
    source_observation_ids = {row["observation_id"] for row in search_rows}
    for row in observations:
        _require(row.get("video_id") in requested_ids and row.get("source_run_id") == search_manifest["run_id"], "reuse_metadata_row_origin_mismatch")
        _require(bool(row.get("source_observation_ids")) and set(row["source_observation_ids"]) <= source_observation_ids,
                 "reuse_metadata_row_evidence_mismatch")
        _require(row.get("discovery_evaluation_eligible") is False, "reuse_metadata_row_not_posthoc")
    return manifest, observations


def _manifest_from_completed_duplicate(error, schema):
    existing = error.existing
    _require(existing.get("status") == "succeeded", "identical_child_run_not_successful_inspect_before_retry")
    for artifact in existing.get("finish", {}).get("artifacts", []):
        path = Path(artifact["path"])
        if path.name == "manifest.json" and hash_file(path) == artifact["sha256"]:
            manifest = read_json(path)
            if manifest.get("schema_version") == schema and manifest.get("run_id") == existing["run_id"]:
                return path.resolve()
    raise WorkflowError("identical_child_run_manifest_unavailable")


def _copy_verified_existing_report(error, target):
    existing = error.existing
    _require(existing.get("status") == "succeeded", "identical_report_run_not_successful")
    candidates = [item for item in existing.get("finish", {}).get("artifacts", []) if Path(item["path"]).suffix.lower() == ".html"]
    _require(len(candidates) == 1, "identical_report_artifact_missing_or_ambiguous")
    artifact = candidates[0]
    _require(hash_file(artifact["path"]) == artifact["sha256"], "existing_report_hash_mismatch")
    with Path(artifact["path"]).open("rb") as source, Path(target).open("xb") as destination:
        shutil.copyfileobj(source, destination)
    _require(hash_file(target) == artifact["sha256"], "report_copy_hash_mismatch")
    return {"report": str(Path(target).resolve()), "run_id": existing["run_id"], "action": "copied_verified_existing_identical_report",
            "copied_from": artifact["path"]}


def run_workflow(collection_manifest, candidate_dir, candidate_id, output_dir, *, db="logs/research.sqlite3",
                 reuse_search_manifest=None, reuse_metadata_manifest=None, retry_of=None, retry_reason=None):
    output = Path(output_dir).resolve()
    _require(not output.exists(), "output_directory_exists_preserve_previous_work")
    _require(reuse_metadata_manifest is None or reuse_search_manifest is not None, "metadata_reuse_requires_search_reuse")
    source = _source_inputs(collection_manifest, candidate_dir, candidate_id)
    search_path = Path(reuse_search_manifest).resolve() if reuse_search_manifest else None
    metadata_path = Path(reuse_metadata_manifest).resolve() if reuse_metadata_manifest else None
    search_manifest, search_rows, metadata_manifest, metadata_rows = None, [], None, []
    inputs = dict(source["files"])
    if search_path:
        search_manifest, search_rows = _verify_search(search_path, source)
        inputs.update(reuse_search_manifest=search_path, reuse_search_observations=search_path.parent / "observations.jsonl")
    if metadata_path:
        metadata_manifest, metadata_rows = _verify_metadata(metadata_path, search_path, search_manifest, search_rows)
        inputs.update(reuse_metadata_manifest=metadata_path, reuse_metadata_observations=metadata_path.parent / "observations.jsonl")
    input_hashes = {key: hash_file(path) for key, path in inputs.items()}
    spec = {"objective": "Expand one real observed candidate through bounded public sources and a local evidence report",
        "stage": "offline_validation" if reuse_search_manifest and reuse_metadata_manifest else "access_probe",
        "parameters": {"candidate_id": candidate_id, "phrase": source["phrase"], "candidate_run_id": source["summary"].get("run_id"),
            "selection_reason": "candidatechosen_posthoc", "max_search_pages": 1, "max_search_results": 20, "max_watch_pages": 3,
            "watch_selection_rule": "first_three_in_search_order_with_NFC_phrase_in_observed_text",
            "reuse_search_run": search_manifest["run_id"] if search_manifest else None,
            "reuse_metadata_run": metadata_manifest["run_id"] if metadata_manifest else None},
        "data_snapshots": [{"id": key, "sha256": value} for key, value in input_hashes.items()],
        "code_hashes": {name: hash_file(ROOT / "src" / name) for name in
            ("youtube_expand.py", "youtube_web_search.py", "youtube_video_metadata.py", "youtube_watch.py", "research_log.py")},
        "config_hashes": {}, "prompt_hashes": {}, "modality": ["text", "video_metadata"], "provenance": "actual_collected"}
    log = ResearchLog(db)
    run = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    started_at = datetime.now(timezone.utc).isoformat()
    issues, artifacts, report = [], [], None
    actions = {"search": "reuse_supplied_manifest" if search_path else "not_started",
               "metadata": "reuse_supplied_manifest_with_original_selection" if metadata_path else "not_started"}
    new_http_attempts = 0
    selected_video_ids = []
    try:
        output.mkdir(parents=True, exist_ok=False)
        _require(all(hash_file(inputs[key]) == value for key, value in input_hashes.items()), "workflow_inputs_changed")
        if not search_path:
            config = {"search_mode": "discovered_phrase_expansion", "queries": [{"query": source["phrase"], "evidence_ids": source["exact_evidence_ids"]}],
                "selection_reason": "candidatechosen_posthoc; selected candidate " + candidate_id + " from detector run " + str(source["summary"].get("run_id")) + "; expansion excluded from independent discovery evaluation",
                "discovery_run_id": source["manifest"]["run_id"], "discovery_manifest": str(source["files"]["collection_manifest"]),
                "discovery_observations": str(source["files"]["collection_observations"]),
                "discovery_method": "candidatechosen_posthoc_from_recorded_candidate_output; no heldout automatic discovery claim",
                "max_results_per_query": 20, "max_results_total": 20, "timeout_seconds": 15, "max_response_bytes": 8_000_000, "retention_days": 30}
            config_path = output / "search_config.json"
            _save(config_path, config)
            artifacts.append(config_path)
            try:
                result = collect_search(config, output_dir=output / "search", logdb=db)
                new_http_attempts += result["http_attempts"]
                search_path = output / "search" / result["run_id"] / "manifest.json"
                actions["search"] = "new_bounded_collection"
            except DuplicateRunError as exc:
                search_path = _manifest_from_completed_duplicate(exc, "youtube-public-search-manifest-v1")
                actions["search"] = "reuse_identical_completed_run"
            search_manifest, search_rows = _verify_search(search_path, source)
        if search_manifest["status"] != "completed":
            issues.append({"stage": "search", "reason": "child_collection_" + search_manifest["status"]})
        if not metadata_path:
            ordered_rows = sorted(enumerate(search_rows), key=lambda pair: (pair[1].get("search_rank", pair[0] + 1), pair[0]))
            for index, row in ordered_rows:
                if _norm(source["phrase"]) in _norm(row.get("text", "")) and row["video_id"] not in selected_video_ids:
                    selected_video_ids.append(row["video_id"])
                    if len(selected_video_ids) == 3:
                        break
            if selected_video_ids:
                config = {"mode": "posthoc_search_expansion", "video_ids": selected_video_ids,
                    "source_run_id": search_manifest["run_id"], "source_manifest": str(search_path),
                    "source_observations": str(search_path.parent / "observations.jsonl"),
                    "selection_reason": "candidatechosen_posthoc; first three distinct search-ranked videos containing the NFC-normalized query in observed title/snippet; no requirement that the result be a meme or positive example",
                    "timeout_seconds": 15, "max_response_bytes": 8_000_000, "retention_days": 30}
                config_path = output / "metadata_config.json"
                _save(config_path, config)
                artifacts.append(config_path)
                try:
                    result = collect_metadata(config, output_dir=output / "metadata", logdb=db)
                    new_http_attempts += result["http_attempts"]
                    metadata_path = output / "metadata" / result["run_id"] / "manifest.json"
                    actions["metadata"] = "new_bounded_collection"
                except DuplicateRunError as exc:
                    metadata_path = _manifest_from_completed_duplicate(exc, "youtube-watch-metadata-manifest-v1")
                    actions["metadata"] = "reuse_identical_completed_run"
                metadata_manifest, metadata_rows = _verify_metadata(metadata_path, search_path, search_manifest, search_rows)
            else:
                actions["metadata"] = "not_requested_no_observed_query_match"
                issues.append({"stage": "metadata", "reason": "no_search_result_contains_observed_query"})
        else:
            selected_video_ids = metadata_manifest["config"]["video_ids"]
        if metadata_manifest and metadata_manifest["status"] != "completed":
            issues.append({"stage": "metadata", "reason": "child_collection_" + metadata_manifest["status"]})
    except Exception as exc:
        reason = str(exc) if isinstance(exc, WorkflowError) else "workflow_child_or_local_error"
        issues.append({"stage": "workflow", "reason": reason})
    # Even a blocked later source should leave the original and any successfully
    # captured evidence reviewable. Do not fabricate missing child data.
    try:
        output.mkdir(parents=True, exist_ok=True)
        report_path = output / "report.html"
        try:
            report = build_report(source["files"]["collection_manifest"], candidate_dir, report_path, db=db,
                                  expansion_manifest=search_path, metadata_manifest=metadata_path)
            report["action"] = "built_new_report"
        except DuplicateRunError as exc:
            report = _copy_verified_existing_report(exc, report_path)
        artifacts.append(report_path)
    except Exception as exc:
        issues.append({"stage": "report", "reason": str(exc) if isinstance(exc, WorkflowError) else "report_failed"})
    status = "partial" if issues and report else "failed" if issues else "completed"
    workflow_manifest = {"schema_version": "youtube-candidate-expansion-workflow-v1", "run_id": run["run_id"], "status": status,
        "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(), "candidate_id": candidate_id,
        "candidate_phrase": source["phrase"], "candidate_run_id": source["summary"].get("run_id"),
        "collection_run_id": source["manifest"]["run_id"], "source_observations": len(source["source_by_id"]),
        "candidate_evidence_ids": source["evidence_ids"], "query_evidence_ids": source["exact_evidence_ids"],
        "search_run_id": search_manifest.get("run_id") if search_manifest else None,
        "search_manifest": str(search_path) if search_path else None, "search_observations": len(search_rows),
        "metadata_run_id": metadata_manifest.get("run_id") if metadata_manifest else None,
        "metadata_manifest": str(metadata_path) if metadata_path else None, "metadata_observations": len(metadata_rows),
        "selected_metadata_video_ids": selected_video_ids, "actions": actions, "new_http_attempts": new_http_attempts,
        "paid_api_calls": 0, "model_calls": 0, "human_review_minutes": 0, "verified_meme_count": None,
        "posthoc_expansion": True, "discovery_evaluation_eligible": False, "report": report, "issues": issues,
        "selection_rule_for_new_calls": "first_three_in_search_order_with_NFC_phrase_in_observed_text",
        "reuse_selection_policy": "Keep and disclose the already-recorded selection; do not pretend it used the new automatic selection rule.",
        "input_hashes": input_hashes, "artifacts": [{"path": str(path), "sha256": hash_file(path)} for path in artifacts]}
    try:
        manifest_path = output / "workflow_manifest.json"
        _save(manifest_path, workflow_manifest)
        artifacts.append(manifest_path)
        log.finish(run["run_id"], status="failed" if issues else "succeeded",
            notes=f"Candidate expansion workflow {status}; new HTTP={new_http_attempts}, search observations={len(search_rows)}, metadata observations={len(metadata_rows)}. Existing observations preserved; posthoc expansion, not meme accuracy validation.",
            artifacts=artifacts, actual_cost={"amount": 0, "currency": "USD", "api_units": 0, "human_minutes": 0})
    except Exception:
        if log.status(run["run_id"])["status"] == "started":
            log.finish(run["run_id"], status="failed", notes="Candidate expansion workflow artifact finalization failed; inspect existing files.")
        raise WorkflowError("workflow_finalization_failed") from None
    return workflow_manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-manifest", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--reuse-search-manifest")
    parser.add_argument("--reuse-metadata-manifest")
    parser.add_argument("--db", default="logs/research.sqlite3")
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = parser.parse_args(argv)
    try:
        result = run_workflow(args.collection_manifest, args.candidate_dir, args.candidate_id, args.output_dir,
            db=args.db, reuse_search_manifest=args.reuse_search_manifest, reuse_metadata_manifest=args.reuse_metadata_manifest,
            retry_of=args.retry_of, retry_reason=args.retry_reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "completed" else 2
    except DuplicateRunError as exc:
        print(json.dumps({"error": "duplicate_workflow", "existing_run_id": exc.existing["run_id"]}))
        return 3
    except Exception as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, WorkflowError) else "invalid_input_or_local_error"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
