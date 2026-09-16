"""Prepare matched U0/U1 evidence bundles; no model or understanding experiment.

U0 contains observed text and context. U1 adds only automatically computed
structure over exactly that same evidence. Meaning and intent remain unknown.
New structures are timestamped now and cannot be backdated to discovery cutoff.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from research_log import DuplicateRunError, ResearchLog, hash_file, read_json
from youtube_candidates import normalized, timestamp

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {"knowledge": ROOT / "prompts/youtube_knowledge_v01.txt",
           "understanding": ROOT / "prompts/youtube_understanding_v01.txt"}
RAW_FIELDS = {"observation_id", "platform", "source_id", "parent_id", "video_id", "channel_id", "kind",
              "text", "modality", "posted_at", "updated_at", "collected_at", "available_at", "provenance",
              "source_url", "sample_method", "original_or_repost", "independence_status", "evidence_links"}
CONTEXT_FIELDS = {"title", "description", "channel_label", "video_title", "video_description", "video_posted_at",
                  "default_language", "default_audio_language", "thread_id", "parent_text", "parent_source_id"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _before(value, limit, name):
    if timestamp(value) > limit:
        raise ValueError(name + " is later than evaluation/preparation time")


def _raw_observation(row, limit, source_cutoff):
    if {"gold", "gold_label", "gold_interpretation", "annotation", "evaluation_result", "question"} & row.keys():
        raise ValueError("Gold, annotation and evaluation fields are not accepted")
    for name in ("posted_at", "collected_at", "available_at"):
        _before(row.get(name), min(limit, source_cutoff), name)
    if row.get("updated_at"):
        _before(row["updated_at"], min(limit, source_cutoff), "updated_at")
    if row.get("meme_label") not in (None, "unassessed") or row.get("human_review_status") not in (None, "not_reviewed", "unreviewed"):
        raise ValueError("Annotated or human-reviewed evidence cannot enter automatic conditions")
    if row.get("provenance") not in {"actual_collected", "researcher_authored", "ai_synthetic"}:
        raise ValueError("Evidence provenance must be explicit")
    if not isinstance(row.get("text"), str) or not isinstance(row.get("observation_id"), str):
        raise ValueError("Evidence requires observed text and observation_id")
    result = {key: row[key] for key in sorted(RAW_FIELDS) if key in row}
    context = row.get("context", {})
    if not isinstance(context, dict) or set(context) - CONTEXT_FIELDS:
        raise ValueError("Context must contain observed collection fields only; no gold or enrichment")
    result["context"] = context
    return result


def build_bundles(candidates, evidence, summary, *, candidate_ids=(), max_candidates=3, evaluation_t=None):
    if type(max_candidates) is not int or max_candidates < 1 or max_candidates > 100:
        raise ValueError("max_candidates must be an integer from 1 to 100")
    generated_at = _now()
    generated = timestamp(generated_at)
    evaluation = timestamp(evaluation_t) if evaluation_t else generated
    # Newly prepared structure cannot be given to a model at a historical T.
    _before(generated_at, evaluation, "New automatic structure")
    limit = min(generated, evaluation)
    method = summary.get("method")
    if method not in {"D0", "D1"}:
        raise ValueError("Candidate summary requires D0 or D1")
    source_cutoff = timestamp(summary.get("cutoff"))
    _before(summary.get("generated_at"), limit, "Candidate generation")
    _before(summary.get("cutoff"), limit, "Source evidence cutoff")
    by_id = {}
    for row in evidence:
        if row.get("observation_id") in by_id:
            raise ValueError("Duplicate observation ID")
        by_id[row.get("observation_id")] = row
    candidate_map = {row["candidate_id"]: row for row in candidates}
    if len(candidate_map) != len(candidates):
        raise ValueError("Duplicate candidate ID")
    if len(set(candidate_ids)) != len(candidate_ids) or set(candidate_ids) - candidate_map.keys():
        raise ValueError("Selected candidate IDs must be unique and present")
    if len(candidate_ids) > max_candidates:
        raise ValueError("Selected candidate count exceeds max_candidates")
    selected = ([candidate_map[oid] for oid in candidate_ids] if candidate_ids else
                sorted(candidates, key=lambda row: (row["rank"], row["candidate_id"]))[:max_candidates])
    selection = "posthoc_candidate_id_selection" if candidate_ids else "top_ranked_bounded_preparation"
    bundles, knowledge_inputs = [], []
    provenance = set()
    for candidate in selected:
        family_id = candidate["candidate_id"]
        if candidate.get("method") != method or candidate.get("verified_meme") is not False:
            raise ValueError("Expected an unverified candidate from the source discovery condition")
        if candidate.get("meaning") is not None or candidate.get("origin") is not None or candidate.get("human_review_minutes", 0) != 0:
            raise ValueError("Candidate includes semantic enrichment or human review")
        _before(candidate.get("linked_at"), limit, "Candidate links")
        _before(candidate.get("evidence_cutoff"), source_cutoff, "Candidate evidence cutoff")
        ids = candidate.get("evidence_observation_ids")
        if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids) or set(ids) - by_id.keys():
            raise ValueError("Candidate evidence IDs must resolve uniquely inside the source evidence")
        raw = [_raw_observation(by_id[oid], limit, source_cutoff) for oid in sorted(ids)]
        provenance.update(row["provenance"] for row in raw)
        if len(provenance) > 1:
            raise ValueError("Do not mix collected, researcher-authored and synthetic evidence")
        forms = candidate.get("surface_forms", [])
        if not isinstance(forms, list) or not forms or not all(isinstance(form, str) for form in forms):
            raise ValueError("Observed surface forms are required")
        form_support = []
        for form in forms:
            support = [row["observation_id"] for row in raw if
                       (" " + normalized(form) + " ") in (" " + normalized(row["text"]) + " ")]
            if not support:
                raise ValueError("Surface form has no evidence in the matched raw bundle")
            form_support.append({"value": form, "evidence_ids": support, "certainty": "supported_as_observed_text_only"})
        links = []
        for link in candidate.get("possible_variant_links", []):
            _before(link.get("linked_at"), limit, "Variant link")
            if link.get("evidence_cutoff"):
                _before(link["evidence_cutoff"], source_cutoff, "Variant link evidence cutoff")
            if link.get("left") not in forms or link.get("right") not in forms:
                raise ValueError("Variant link refers outside the selected candidate")
            links.append({key: link[key] for key in ("left", "right", "sequence_similarity", "character_bigram_jaccard",
                          "shared_tokens", "relation_status", "linked_at", "evidence_cutoff") if key in link})
        raw_hash = digest(raw)
        structure = {
            "family_id": family_id, "record_origin": "automatic_rule_based", "structure_status": "observed_metadata_only",
            "knowledge_version": "observed_" + digest({"raw": raw_hash, "forms": forms, "links": links})[:20],
            "generated_at": generated_at, "available_at": generated_at,
            "source_discovery_evidence_cutoff": summary["cutoff"], "source_links_created_at": candidate["linked_at"],
            "source_evidence_ids": sorted(ids), "raw_bundle_sha256": raw_hash,
            "surface_forms": form_support, "provisional_family_links": links,
            "counts_within_matched_raw_bundle": {"observations": len(raw),
                "source_documents": len({(row["platform"], row["source_id"]) for row in raw}),
                "videos": len({row["video_id"] for row in raw}),
                "channels": len({row["channel_id"] for row in raw if row.get("channel_id")}),
                "distinct_normalized_texts": len({normalized(row["text"]) for row in raw}),
                "independent_uses": None},
            "provenance": raw[0]["provenance"], "modalities": dict(sorted(Counter(row["modality"] for row in raw).items())),
            "sources": [{"observation_id": row["observation_id"], "source_url": row.get("source_url"),
                         "posted_at": row["posted_at"], "available_at": row["available_at"]} for row in raw],
            "meaning": None, "intent": None, "usage_conditions": None, "editable_parts": None,
            "preservation_rules": None, "semantic_status": "needs_model_or_review",
            "verified_meme": False, "independence_status": "unknown", "origin": None,
            "audio_visual_content_analyzed": False, "human_review_minutes": 0,
        }
        for understanding in ("U0", "U1"):
            support = {"family_id": family_id, "observations": raw}
            if understanding == "U1":
                support["automatic_structure"] = structure
            bundles.append({"condition": method + understanding, "family_id": family_id,
                            "evaluation_t": evaluation.isoformat(), "generated_at": generated_at,
                            "sample_selection": selection, "raw_bundle_sha256": raw_hash,
                            "support_bundle": support, "execution_status": "prepared_not_executed",
                            "understanding_question": None, "model_weights_updated": False})
        knowledge_inputs.append({"family_id": family_id, "evaluation_cutoff": evaluation.isoformat(),
                                 "observations": raw, "family_links": links, "raw_bundle_sha256": raw_hash,
                                 "execution_status": "prepared_not_executed",
                                 "result_availability_rule": "Assign semantic record available_at only after a future model response is received"})
    manifest = {"status": "paired_inputs_prepared_not_understanding_experiment", "discovery_condition": method,
                "generated_at": generated_at, "evaluation_t": evaluation.isoformat(),
                "source_discovery_evidence_cutoff": summary["cutoff"], "source_candidate_generated_at": summary["generated_at"],
                "selected_candidate_ids": [row["candidate_id"] for row in selected], "sample_selection": selection,
                "candidate_count": len(selected), "paired_input_count": len(bundles),
                "provenance": next(iter(provenance)) if provenance else "not_applicable",
                "record_origin": "automatic_rule_based", "raw_evidence_identical_across_u_conditions": True,
                "semantic_structure_generated": False, "model_calls": 0, "model_weights_updated": False,
                "human_review_minutes": 0, "independent_questions_prepared": 0,
                "limitations": ["Observed metadata structure only; the planned semantic U1 condition remains incomplete.",
                                "No model execution, correctness score, independent gold or understanding success.",
                                "Candidate-selected bundles do not measure missed memes or false candidates.",
                                "Any future semantic model output needs its own response-time availability and evaluation cutoff."]}
    return {"bundles": bundles, "knowledge_inputs": knowledge_inputs, "manifest": manifest}


def run(candidate_dir, output_dir, *, candidate_ids=(), max_candidates=3, evaluation_t=None,
        db="logs/research.sqlite3", retry_of=None, retry_reason=None):
    source, output = Path(candidate_dir), Path(output_dir)
    if output.exists():
        raise ValueError("Output directory exists; preserve previous artifacts")
    paths = {name: source / name for name in ("candidates.jsonl", "evidence.jsonl", "summary.json")}
    hashes = {name: hash_file(path) for name, path in paths.items()}
    candidates = [json.loads(line) for line in paths["candidates.jsonl"].read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    evidence = [json.loads(line) for line in paths["evidence.jsonl"].read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    summary = read_json(paths["summary.json"])
    if hashes != {name: hash_file(path) for name, path in paths.items()}:
        raise ValueError("Source artifact changed while loading")
    prompt_hashes = {path.name: hash_file(path) for path in PROMPTS.values()}
    prompt_text = {name: path.read_text(encoding="utf-8-sig") for name, path in PROMPTS.items()}
    if prompt_hashes != {path.name: hash_file(path) for path in PROMPTS.values()}:
        raise ValueError("Prompt changed while loading")
    settings = {"candidate_ids": sorted(candidate_ids), "max_candidates": max_candidates,
                "evaluation_t": timestamp(evaluation_t).isoformat() if evaluation_t else None,
                "default_time_policy": "actual_structure_preparation_time_not_discovery_cutoff"}
    observed_provenance = {row.get("provenance") for row in evidence}
    provenance = next(iter(observed_provenance)) if len(observed_provenance) == 1 else "mixed"
    spec = {"objective": "Prepare matched U0/U1 raw and automatic-observed-structure bundles without model execution",
            "stage": "offline_validation", "parameters": settings,
            "data_snapshots": [{"id": name, "sha256": value} for name, value in hashes.items()],
            "code_hashes": {name: hash_file(ROOT / "src" / name) for name in
                            ("youtube_context_bundle.py", "youtube_candidates.py", "research_log.py")},
            "prompt_hashes": prompt_hashes, "config_hashes": {}, "modality": ["text"],
            "provenance": provenance if provenance in {"actual_collected", "researcher_authored", "ai_synthetic", "mixed"} else "not_applicable",
            "time_cutoff": summary["cutoff"]}
    log = ResearchLog(db)
    entry = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    try:
        result = build_bundles(candidates, evidence, summary, candidate_ids=tuple(sorted(candidate_ids)),
                               max_candidates=max_candidates, evaluation_t=evaluation_t)
        result["manifest"].update({"run_id": entry["run_id"], "source_artifact_hashes": hashes,
                                   "prompt_hashes": prompt_hashes})
        output.mkdir(parents=True, exist_ok=False)
        artifacts = []
        for name in ("bundles", "knowledge_inputs"):
            path = output / (name + ".jsonl")
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                for row in result[name]:
                    handle.write(canonical(row) + "\n")
            artifacts.append(path)
        for name, text in prompt_text.items():
            path = output / (name + "_prompt.txt")
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            artifacts.append(path)
        path = output / "manifest.json"
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(result["manifest"], ensure_ascii=False, indent=2) + "\n")
        artifacts.append(path)
        log.finish(entry["run_id"], status="succeeded", notes="Prepared matched evidence and observed-structure inputs only; no understanding/model experiment performed.",
                   artifacts=artifacts, actual_cost={"amount": 0, "currency": "USD", "api_units": 0, "human_minutes": 0})
        return result["manifest"]
    except BaseException as exc:
        log.finish(entry["run_id"], status="failed", notes="Bundle preparation failed: " + type(exc).__name__)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--candidate-id", action="append", default=[])
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--evaluation-t")
    parser.add_argument("--db", default="logs/research.sqlite3")
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = vars(parser.parse_args(argv))
    args["candidate_ids"] = args.pop("candidate_id")
    try:
        print(json.dumps(run(**args), ensure_ascii=False, indent=2))
    except (DuplicateRunError, ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"Bundle preparation error: {exc}\n")


if __name__ == "__main__":
    main()
