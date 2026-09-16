"""One-command, keyless YouTube observation -> discovery -> evidence -> report.

All seeds and follow-up video selections are computed from collected metadata.
No preselected meme names or manually written interpretations are inputs.
Public HTML is bounded and optional; access failures remain visible.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unicodedata

from research_log import ResearchLog, hash_file, read_json
from youtube_feed_collect import collect as collect_feed
from youtube_web_search import collect as collect_search
from youtube_video_metadata import collect as collect_metadata
from youtube_candidates import prepare_observations, timestamp
from meme_discovery_engine import plan_seeds, select_followups, build_families
from meme_finder_report import render_report, markdown_report

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = {"rate_limited", "consent_required", "captcha_or_traffic_block", "login_required", "redirect_blocked"}


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def save_rows(path, rows):
    with Path(path).open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def query_occurs_in_source(query, text):
    normalize = lambda value: " ".join(unicodedata.normalize("NFC", value).casefold().split())
    return normalize(query) in normalize(text)


def validate_config(config):
    allowed = {"version", "channels", "topic_queries", "max_feed_entries", "max_topic_videos", "max_seeds",
               "max_search_results", "max_followup_videos_per_seed", "max_http_requests", "timeout_seconds",
               "retention_days", "recent_days", "sampling_note"}
    if set(config) - allowed or config.get("version") != "meme-finder-v1":
        raise ValueError("Unsupported configuration")
    from youtube_feed_collect import validate_config as validate_feed
    validate_feed({"snapshot_id": "config_validation", "channels": config["channels"]})
    ranges = {"max_feed_entries": (1, 100), "max_topic_videos": (0, 3), "max_seeds": (1, 10),
              "max_search_results": (1, 20), "max_followup_videos_per_seed": (1, 3),
              "max_http_requests": (1, 60), "timeout_seconds": (1, 60), "retention_days": (1, 30),
              "recent_days": (1, 365)}
    for field, (lower, upper) in ranges.items():
        if type(config.get(field)) is not int or not lower <= config[field] <= upper:
            raise ValueError("Invalid bound: " + field)
    queries = config.get("topic_queries", [])
    if not isinstance(queries, list) or len(queries) > 2 or any(not isinstance(q, str) or not q.strip() for q in queries):
        raise ValueError("Choose at most two generic topic queries")
    if len(set(queries)) != len(queries):
        raise ValueError("Duplicate topic query")
    return dict(config)


def _origin(row, kind, manifest_path, run_id):
    # Enrichment lives in the workflow copy. Original collector files stay intact.
    return {**row, "discovery_origin": kind, "pipeline_source_manifest": str(manifest_path),
            "pipeline_source_run_id": run_id}


class Workflow:
    def __init__(self, config, out, db, run_id):
        self.config, self.out, self.db, self.run_id = config, out, db, run_id
        self.attempts, self.failures, self.children, self.manifests = [], [], [], []
        self.requests = 0
        self.blocked = False

    def room(self, count):
        return not self.blocked and self.requests + count <= self.config["max_http_requests"]

    def ingest(self, stage, manifest, path, origin):
        path = Path(path).resolve()
        count = manifest.get("http_attempts", manifest.get("counts", {}).get("requests", 0))
        self.requests += count
        self.children.append(manifest["run_id"])
        self.manifests.append(str(path))
        self.attempts.append({"stage": stage, "run_id": manifest["run_id"], "manifest": str(path),
                              "status": manifest["status"], "http_requests": count})
        for request in manifest.get("requests", []):
            if request.get("status") == "failed":
                self.failures.append({"stage": stage, "run_id": manifest["run_id"], **request})
                if request.get("reason") in BLOCKS or request.get("http_status") in (403, 429):
                    self.blocked = True
        self.failures.extend({"stage": stage, "run_id": manifest["run_id"], **issue}
                             for issue in manifest.get("issues", []) if not issue.get("video_id") and not issue.get("query"))
        return [_origin(row, origin, path, manifest["run_id"]) for row in read_rows(path.parent / "observations.jsonl")]

    def search(self, query, stage, seed=None, origin_row=None):
        if not self.room(1):
            self.attempts.append({"stage": stage, "query": query, "status": "not_requested",
                                  "reason": "access_block_or_request_budget"})
            return [], None
        cfg = {"search_mode": "discovered_phrase_expansion" if seed else "topic_query",
               "queries": [{"query": query, **({"evidence_ids": [origin_row["observation_id"]]} if seed else {})}],
               "selection_reason": "Automatic pipeline selection from observed source text; workflow " + self.run_id,
               "known_meme_seed_names": [], "max_results_per_query": self.config["max_search_results"],
               "max_results_total": self.config["max_search_results"], "timeout_seconds": self.config["timeout_seconds"],
               "max_response_bytes": 8_000_000, "retention_days": self.config["retention_days"]}
        if seed:
            source_path = Path(origin_row["pipeline_source_manifest"])
            cfg.update(discovery_run_id=origin_row["pipeline_source_run_id"], discovery_manifest=str(source_path),
                       discovery_observations=str(source_path.parent / "observations.jsonl"),
                       discovery_method="automatic_observed_form_seed; initial origin=" + origin_row["discovery_origin"])
        parent = self.out / stage
        result = collect_search(cfg, output_dir=parent, logdb=self.db)
        path = parent / result["run_id"] / "manifest.json"
        return self.ingest(stage, result, path, "observed_phrase_expansion" if seed else "generic_topic_search"), path

    def metadata(self, video_ids, source_path, stage, origin):
        allowed = max(0, self.config["max_http_requests"] - self.requests)
        selected = list(dict.fromkeys(video_ids))[:min(3, allowed)]
        if not selected or self.blocked:
            return []
        source = read_json(source_path)
        cfg = {"mode": "posthoc_search_expansion", "video_ids": selected, "source_run_id": source["run_id"],
               "source_manifest": str(Path(source_path).resolve()),
               "source_observations": str((Path(source_path).parent / "observations.jsonl").resolve()),
               "selection_reason": "Automatically selected query-relevant, diverse source videos; workflow " + self.run_id,
               "timeout_seconds": self.config["timeout_seconds"], "max_response_bytes": 8_000_000,
               "retention_days": self.config["retention_days"]}
        parent = self.out / stage
        result = collect_metadata(cfg, output_dir=parent, logdb=self.db)
        return self.ingest(stage, result, parent / result["run_id"] / "manifest.json", origin)


def run(config_path, output_dir, *, db="logs/research.sqlite3", feed_manifest=None):
    config_path = Path(config_path).resolve()
    config = validate_config(read_json(config_path))
    snapshots = []
    if feed_manifest:
        feed_manifest = Path(feed_manifest).resolve()
        snapshots = [{"id": str(p), "sha256": hash_file(p)} for p in
                     [feed_manifest, feed_manifest.parent / "observations.jsonl"]]
    registry = ResearchLog(db)
    began = now()
    record = registry.begin({"objective": "Automatically discover meme forms, collect supporting uses, group and explain with linked sources",
        "stage": "access_probe", "parameters": {"config": config, "observation_round": began,
            "initial_feed_mode": "existing_actual_snapshot" if feed_manifest else "fresh_public_channel_feeds",
            "human_candidate_selection": False, "known_meme_names_input": False},
        "data_snapshots": snapshots, "code_hashes": {name: hash_file(ROOT / "src" / name) for name in
            ("find_memes.py", "meme_discovery_engine.py", "meme_finder_report.py", "youtube_feed_collect.py",
             "youtube_web_search.py", "youtube_video_metadata.py", "research_log.py")},
        "config_hashes": {"finder": hash_file(config_path)}, "prompt_hashes": {},
        "modality": ["title_text", "description_text"], "provenance": "actual_collected"})
    out = Path(output_dir).resolve() / record["run_id"]
    out.mkdir(parents=True, exist_ok=False)
    workflow = Workflow(config, out, db, record["run_id"])
    save_json(out / "config.json", config)
    try:
        if feed_manifest:
            feed = read_json(feed_manifest)
            if feed.get("provenance") != "actual_collected":
                raise ValueError("Actual collection mode rejects synthetic input")
            initial = [_origin(row, "name_free_channel_observation", feed_manifest, feed["run_id"])
                       for row in read_rows(feed_manifest.parent / "observations.jsonl")]
            workflow.manifests.append(str(feed_manifest))
            workflow.attempts.append({"stage": "feed", "status": "reused", "run_id": feed["run_id"], "http_requests": 0})
        else:
            if not workflow.room(len(config["channels"])):
                raise ValueError("HTTP budget cannot cover configured channel observation")
            cfg = {"snapshot_id": "meme_finder_" + record["run_id"], "channels": config["channels"],
                   "max_entries_per_channel": config["max_feed_entries"], "timeout_seconds": config["timeout_seconds"],
                   "retention_days": config["retention_days"]}
            feed = collect_feed(cfg, output_dir=out / "feed", logdb=db)
            initial = workflow.ingest("feed", feed, out / "feed/manifest.json", "name_free_channel_observation")
        print(json.dumps({"stage": "observed_channels", "observations": len(initial)}, ensure_ascii=False), flush=True)
        source_rows = list(initial)
        search_cards = []
        for i, query in enumerate(config.get("topic_queries", [])):
            rows, source_path = workflow.search(query, "topic_search_" + str(i + 1))
            search_cards.extend(rows)
            if source_path and config["max_topic_videos"]:
                # Query rank is explicit sampling, never a claim of popularity.
                ids = [row["video_id"] for row in rows[:config["max_topic_videos"]]]
                source_rows.extend(workflow.metadata(ids, source_path, "topic_metadata_" + str(i + 1), "generic_topic_search"))
        eligible_seeds, seed_exclusions = prepare_observations(source_rows, now())
        seeds = plan_seeds(eligible_seeds, max_seeds=config["max_seeds"])
        # Save automatic decisions before expanding, including empty decisions.
        save_json(out / "seeds.json", seeds)
        print(json.dumps({"stage": "automatic_seeds", "queries": [seed["query"] for seed in seeds]}, ensure_ascii=False), flush=True)
        by_id = {row["observation_id"]: row for row in source_rows}
        associations = {}
        for i, seed in enumerate(seeds, 1):
            origins = [by_id[oid] for oid in seed["source_observation_ids"] if oid in by_id]
            if not origins:
                raise ValueError("Automatic seed lacks source evidence")
            origin = next((row for row in origins if query_occurs_in_source(seed["query"], row["text"])), None)
            if origin is None:
                raise ValueError("Automatic query is not observed source text")
            rows, source_path = workflow.search(seed["query"], "expansion_search_" + str(i), seed, origin)
            search_cards.extend(rows)
            if source_path:
                available_ids = {row["video_id"] for row in source_rows}
                candidates = [row for row in rows if row["video_id"] not in available_ids]
                chosen = select_followups(seed, candidates, limit=config["max_followup_videos_per_seed"])
                expanded = workflow.metadata(chosen, source_path, "expansion_metadata_" + str(i), "observed_phrase_expansion")
                source_rows.extend(expanded)
                associations[seed["seed_id"]] = [row["video_id"] for row in expanded]
            print(json.dumps({"stage": "expanded_seed", "query": seed["query"], "http_requests_so_far": workflow.requests}, ensure_ascii=False), flush=True)
        cutoff = now()
        eligible, exclusions = prepare_observations(source_rows, cutoff)
        resolved_video_ids = {row["video_id"] for row in eligible}
        navigation_only = [{"observation_id": row["observation_id"], "video_id": row["video_id"],
                            "reason": "search_card_used_for_navigation_only_not_dated_family_evidence",
                            "separate_dated_source_resolved": row["video_id"] in resolved_video_ids}
                           for row in search_cards]
        grouped = build_families(eligible, seeds, search_associations=associations)
        created = now()
        recent_since = datetime.fromisoformat(created) - timedelta(days=config["recent_days"])
        for family in grouped["families"]:
            family["linked_at"] = created
            family["knowledge"]["available_at"] = created
            family["knowledge"]["provenance"] = "automatic_rule_derived_interpretation"
            family["recent_observed_uses"] = sum(bool(example.get("posted_at")) and timestamp(example["posted_at"]) >= recent_since
                                                 for example in family["examples"])
            family["recent_window_days"] = config["recent_days"]
            family["current_popularity"] = "not_estimated"
        statuses = Counter(family["status"] for family in grouped["families"])
        result = {"schema_version": "meme-finder-result-v1", "run_id": record["run_id"], "created_at": created,
            "evidence_cutoff": cutoff, "mode": "automatic_observed_text_discovery",
            "scope": "YouTube public title and description observations; Korean-first, convenience sample",
            "counts": {"initial_feed_observations": len(initial), "search_cards": len(search_cards),
                       "resolved_source_observations": len(eligible), "seeds": len(seeds),
                       "families_by_status": dict(statuses), "http_requests": workflow.requests,
                       "excluded_observations": len(exclusions), "navigation_only_search_cards": len(navigation_only),
                       "verified_independent_meme_count": None},
            "budget": {"max_http_requests": config["max_http_requests"], "paid_api_calls": 0,
                       "model_calls": 0, "human_review_minutes": 0},
            "families": grouped["families"], "rejected": grouped["rejected"], "seeds": seeds,
            "attempts": workflow.attempts, "failures": workflow.failures, "child_run_ids": workflow.children,
            "source_manifests": workflow.manifests, "search_associations": associations,
            "limitations": ["Actual title and description reuse is examined; video, audio and gestures are not interpreted.",
                "Distinct channels or changed titles do not prove independent footage or authorship.",
                "Recent source use is not a popularity ranking; source counts are not added across platforms.",
                "Generic meme-topic search and observed-phrase expansion remain separate from name-free channel observations.",
                "Independent gold evaluation and missed-family recall are not yet available.",
                "Rule-derived explanations are separated from local language-model responses; no model weights are trained."],
            "completion_status": "partial_access" if workflow.failures else "executed"}
        save_rows(out / "observations.jsonl", eligible)
        save_rows(out / "search_cards.jsonl", search_cards)
        save_rows(out / "excluded.jsonl", exclusions)
        save_rows(out / "navigation_only.jsonl", navigation_only)
        save_rows(out / "seed_exclusions.jsonl", seed_exclusions)
        save_json(out / "result.json", result)
        render_report(result, out / "report.html")
        (out / "report.md").write_text(markdown_report(result), encoding="utf-8")
        artifacts = [path for path in out.iterdir() if path.is_file()]
        registry.finish(record["run_id"], status="succeeded" if not workflow.failures else "failed",
            notes=f"Automatic observed-seed workflow executed; {len(seeds)} seeds, family statuses {dict(statuses)}, {workflow.requests} HTTP requests. Rule-supported source reuse is not independent gold truth or full research completion.",
            artifacts=artifacts, actual_cost={"amount": 0, "currency": "USD", "human_minutes": 0})
        return result, out
    except BaseException as exc:
        save_json(out / "error.json", {"error_type": type(exc).__name__, "message": str(exc)[:500],
                                       "child_run_ids": workflow.children, "at": now()})
        if registry.status(record["run_id"])["status"] == "started":
            registry.finish(record["run_id"], status="failed", notes="Finder interrupted: " + type(exc).__name__,
                            artifacts=[path for path in out.iterdir() if path.is_file()])
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "configs/meme_finder_youtube_v01.json"))
    parser.add_argument("--output-dir", default=str(ROOT / "data/meme_finder"))
    parser.add_argument("--db", default=str(ROOT / "logs/research.sqlite3"))
    parser.add_argument("--feed-manifest", help="Optional actual source snapshot; searches remain live and automatic")
    parser.add_argument("--local-model", help="Optional cached Hugging Face model directory, local files only")
    parser.add_argument("--model-max-new-tokens", type=int, default=160)
    args = parser.parse_args(argv)
    try:
        result, out = run(args.config, args.output_dir, db=args.db, feed_manifest=args.feed_manifest)
        print(json.dumps({"result": str(out / "result.json"), "report": str(out / "report.html"),
                          "counts": result["counts"]}, ensure_ascii=False), flush=True)
        if args.local_model:
            from meme_local_understanding import run as understand
            understanding = understand(result_path=out / "result.json", output_dir=out / "understanding", model_path=args.local_model,
                                       max_families=1, max_new_tokens=args.model_max_new_tokens, db=args.db)
            print(json.dumps({"understanding_status": understanding["status"],
                              "manifest": str(out / "understanding/manifest.json"),
                              "local_model_calls": understanding.get("local_model_calls", 0)}, ensure_ascii=False), flush=True)
            if understanding["status"] != "completed":
                return 2
        supported = any(family["status"] == "supported_textual_reuse" for family in result["families"])
        return 0 if supported and not result["failures"] else 2
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
