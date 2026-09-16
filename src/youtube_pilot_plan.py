"""Validate and cost a proposed YouTube pilot without collecting or calling models.

The example counts are planning assumptions, never observations or performance.
Every CLI run is registered before writing an artifact. Duplicate specifications
are rejected by the shared research log, regardless of the output path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from research_log import DuplicateRunError, ResearchLog, hash_file, read_json

ROOT = Path(__file__).resolve().parents[1]


def _count(value, label, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"Invalid count: {label}")
    return value


def build_plan(config):
    if config["status"] != "proposal_not_authorized_execution" or config["access"]["mode"] != "planning_only":
        raise ValueError("This tool only prepares a proposal; it cannot authorize execution")
    if config["platforms"] != ["youtube"]:
        raise ValueError("This first-stage planner supports YouTube only")
    s, e, c, q = (config[name] for name in ("sampling", "experiment", "cost_assumptions", "quota_reference"))
    if e["discovery_conditions"] != {
        "D0": "simple_phrase_frequency_and_growth", "D1": "variant_structure_independent_use_and_context"
    } or e["understanding_conditions"] != {
        "U0": "discovered_raw_examples_and_context", "U1": "same_raw_examples_and_context_plus_automatic_structure"
    }:
        raise ValueError("D/U definitions must match the current research handoff")
    if e["manual_record_correction_in_main_conditions"] is not False:
        raise ValueError("Human corrections must be a separate condition")
    if s["known_meme_seed_names"] or s["search_query"] is not None:
        raise ValueError("This example is name-free; seed or topic searches need a separate sampling specification")
    if s["comment_order"] != "time":
        raise ValueError("This sampling proposal uses time-ordered comments")
    channels = _count(s["channel_count_example"], "channels", 1)
    videos = _count(s["max_videos_example"], "videos", 1)
    rounds = _count(s["observation_rounds_example"], "rounds", 1)
    pages = {name: _count(s[name], name) for name in (
        "playlist_pages_per_channel_per_round", "top_level_pages_per_video_per_round",
        "reply_pages_total_per_round", "chart_pages_per_round", "search_pages_per_round")}
    comment_size = _count(s["comment_page_size"], "comment page size", 1, 100)
    reply_size = _count(s["reply_page_size"], "reply page size", 1, 100)
    unit = _count(q["read_endpoint_units"], "read endpoint units", 1)
    if unit != 1:
        raise ValueError("Re-check endpoint-specific quota assumptions before changing read unit costs")
    k = _count(e["candidate_review_cap_per_discovery"], "candidate cap", 1)
    questions = _count(e["evaluation_questions_example"], "questions", 1)
    repeats = _count(e["generation_repeats_example"], "repeats", 1)
    if type(e["no_support_diagnostic"]) is not bool:
        raise ValueError("no_support_diagnostic must be boolean")
    calls = {
        "channels_list": math.ceil(channels / 50),
        "playlistItems_list": channels * pages["playlist_pages_per_channel_per_round"] * rounds,
        "videos_list_by_id": math.ceil(videos / 50) * rounds,
        "commentThreads_list": videos * pages["top_level_pages_per_video_per_round"] * rounds,
        "comments_list": pages["reply_pages_total_per_round"] * rounds,
        "videos_list_chart": pages["chart_pages_per_round"] * rounds,
        "search_list": pages["search_pages_per_round"] * rounds,
    }
    search_queries = calls["search_list"]
    other_units = sum(value for name, value in calls.items() if name != "search_list") * unit
    model_calls = {
        "D1_candidate_review": k,
        "automatic_structure_two_discovery_sets": 2 * k,
        "four_DU_cells": 4 * questions * repeats,
        "no_support_diagnostic": questions * repeats if e["no_support_diagnostic"] else 0,
    }
    token_pairs = {
        "D1_candidate_review": "candidate_review",
        "automatic_structure_two_discovery_sets": "structure",
        "four_DU_cells": "understanding",
        "no_support_diagnostic": "no_support",
    }
    tokens = {direction: sum(
        count * _count(c[f"{token_pairs[stage]}_{direction}_tokens"], f"{stage} {direction} tokens")
        for stage, count in model_calls.items()) for direction in ("input", "output")}
    rates = [c[name] for name in ("input_usd_per_million_tokens", "output_usd_per_million_tokens")]
    for rate in rates:
        if rate is not None and (type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0):
            raise ValueError("Invalid model price; unselected prices must be null")
    amount = None if None in rates else round((tokens["input"] * rates[0] + tokens["output"] * rates[1]) / 1_000_000, 8)
    entries_per_round = videos + videos * pages["top_level_pages_per_video_per_round"] * comment_size + pages["reply_pages_total_per_round"] * reply_size
    annotators = _count(c["independent_annotators_example"], "annotators example", 1)
    minutes = _count(c["human_review_minutes_per_document_example"], "review minutes example", 1)
    unfinished = [
        "actual_API_project_access_and_quota", "analysis_use_and_retention_scope",
        "channel_cohort_and_selection_rule", "observation_times_and_evaluation_cutoff",
        "independent_gold_questions_and_annotation_arrangements", "D0_D1_implementation_and_development_tuning",
        "retrieval_and_temporal_validation_implementation", "model_version_transfer_scope_and_budget",
        "actual_token_counts_and_expense_guard", "media_access_if_needed",
    ]
    return {
        "plan_version": config["version"], "artifact_type": "offline_proposal_arithmetic",
        "execution_ready": False, "observed_videos": 0, "model_calls_executed": 0,
        "youtube_api_calls_executed": 0, "estimated_api_calls": calls,
        "quota_estimate": {"search_queries_separate_bucket": search_queries, "other_units": other_units,
            "reference_checked_on": q["checked_on"], "actual_project_allocation_verified": False,
            "interpretation": "Totals across all proposed rounds, not daily usage or a dollar price; extra pages/failures excluded"},
        "cells": [{"condition": d + u, "discovery": d, "understanding": u,
                   "questions_each_repeat": questions, "repeats": repeats,
                   "include_missed_families": True} for d in ("D0", "D1") for u in ("U0", "U1")],
        "model_estimate": {"calls_by_stage": model_calls, "total_calls": sum(model_calls.values()),
            "input_tokens": tokens["input"], "output_tokens": tokens["output"], "model_only_usd": amount,
            "total_cash_cost_usd": None,
            "note": "Example token assumptions, not guaranteed caps. Reuse may reduce extraction calls. Embeddings/OCR/ASR/human time/storage are separate."},
        "annotation_estimate": {"entries_per_round_max_assuming_full_pages": entries_per_round,
            "entry_versions_all_rounds_max": entries_per_round * rounds,
            "independent_annotators_example": annotators,
            "hours_if_every_entry_version_reviewed": round(entries_per_round * rounds * annotators * minutes / 60, 2),
            "note": "Conservative workload before deduplicating unchanged snapshots; excludes viewing, guideline training, disagreements, question writing and final output evaluation. Not recruited people."},
        "unfinished_before_live_experiment": unfinished,
    }


def make_spec(config, project_root=ROOT):
    project_root = Path(project_root)
    canonical_config = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "objective": "YouTube first-stage D0/D1 x U0/U1 proposal arithmetic",
        "stage": "offline_validation", "parameters": {"purpose": "plan_only", "configuration": config},
        "data_snapshots": [], "code_hashes": {
            "planner": hash_file(Path(__file__)), "research_log": hash_file(Path(__file__).with_name("research_log.py"))},
        "prompt_hashes": {p: hash_file(project_root / p) for p in config["experiment"]["prompt_paths"]},
        "config_hashes": {"semantic_config": hashlib.sha256(canonical_config.encode("utf-8")).hexdigest()},
        "modality": ["text"], "provenance": "not_applicable",
    }


def prepare(config_path, output, db="logs/research.sqlite3", project_root=ROOT, retry_of=None, retry_reason=None):
    config, output = read_json(config_path), Path(output)
    spec = make_spec(config, project_root)
    log = ResearchLog(db)
    run = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    try:
        plan = build_plan(config)
        plan["run_id"], plan["fingerprint"] = run["run_id"], run["fingerprint"]
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        log.finish(run["run_id"], status="succeeded", artifacts=[output],
                   notes="Validated proposal definitions and computed API/model/workload estimates only; no collection, model or human evaluation.")
    except Exception as exc:
        log.finish(run["run_id"], status="failed", notes="Offline preparation failed: " + type(exc).__name__)
        raise
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/youtube_pilot_v01.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--db", default="logs/research.sqlite3")
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = parser.parse_args()
    try:
        plan = prepare(args.config, args.out, args.db, retry_of=args.retry_of, retry_reason=args.retry_reason)
    except DuplicateRunError as exc:
        print(str(exc))
        return 2
    print(json.dumps({"run_id": plan["run_id"], "execution_ready": False,
                      "quota_estimate": plan["quota_estimate"], "model_estimate": plan["model_estimate"],
                      "annotation_estimate": plan["annotation_estimate"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
