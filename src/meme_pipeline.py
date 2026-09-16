"""Offline research preparation. No provider SDK, network calls, or invented model outputs."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

FIELDS = (
    "form_labels", "functions", "preservation_constraints", "editable_elements",
    "adaptation_modes", "usage_context", "modality_dependency", "uncertainty_notes",
)
FIELD_LABELS = {
    "form_labels": "표현 형태", "functions": "의사소통 기능",
    "preservation_constraints": "보존해야 할 특징", "editable_elements": "변경할 수 있는 요소",
    "adaptation_modes": "응용 방식", "usage_context": "사용 맥락",
    "modality_dependency": "매체 의존성", "uncertainty_notes": "분석의 불확실성",
}
STEMS = {
    "form_labels": "표현 형태에 관한 분석은",
    "functions": "의사소통 기능에 관한 분석은",
    "preservation_constraints": "보존해야 할 특징에 관한 분석은",
    "editable_elements": "변경할 수 있는 요소에 관한 분석은",
    "adaptation_modes": "응용 방식에 관한 분석은",
    "usage_context": "사용 맥락에 관한 분석은",
    "modality_dependency": "매체 의존성에 관한 분석은",
    "uncertainty_notes": "분석의 불확실성에 관한 설명은",
}
CERTAINTY = {"supported": "지원 용례에 의해 뒷받침된다", "tentative": "잠정적이다"}
PROVENANCE = {"observed_web", "researcher_authored", "ai_synthetic", "user_supplied_unverified"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines()
            if line.strip()]


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    require(not path.exists(), f"Refusing to overwrite {path}; choose a new run directory.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path, rows):
    path = Path(path)
    require(not path.exists(), f"Refusing to overwrite {path}; choose a new run directory.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8")


def index_unique(rows, key):
    result = {}
    for row in rows:
        require(row[key] not in result, f"Duplicate {key}: {row[key]}")
        result[row[key]] = row
    return result


def load_config(path):
    cfg = read_json(path)
    require(cfg["mode"] in {"smoke", "pilot", "main"}, "Invalid mode")
    require(cfg["conditions"] == ["B1", "B2", "B3"], "Main preparation requires B1/B2/B3")
    require(cfg["allow_network"] is False, "This offline tool does not allow network execution")
    require(cfg["generation_repeats"] >= 1, "Invalid generation repeats")
    expected = "smoke_fixture" if cfg["mode"] == "smoke" else "automatic"
    require(cfg["record_origin"] == expected, "Human-reviewed or fixture record cannot be main automatic method")
    require(cfg["mode"] == "smoke" or cfg["model_id"] != "SMOKE_NO_MODEL", "Smoke model sentinel is not a real model")
    return cfg


def validate_dataset(families, supports, cfg):
    fi = index_unique(families, "family_id")
    si = index_unique(supports, "support_id")
    lineage_splits = {}
    source_splits = {}
    normalized_text_splits = {}
    for f in families:
        require(f["split"] in {"dev", "validation", "test"}, "Invalid family split")
        group = f["lineage_group_id"]
        require(bool(group), "Missing lineage group")
        if group in lineage_splits:
            require(lineage_splits[group] == f["split"], "Lineage crosses splits")
        lineage_splits[group] = f["split"]
    for s in supports:
        require(s["family_id"] in fi, "Support has unknown family")
        require(s["provenance"] in PROVENANCE, "Unknown provenance")
        require(bool(s["text"].strip()) and isinstance(s["context"], str), "Empty support text/context")
        if cfg["mode"] != "smoke":
            require(s["provenance"] == "observed_web", "Real pilot/main supports must be observed_web")
            require(s["verification_status"] == "text_verified", "Unverified text is not experimental support")
            require(s.get("source_url", "").startswith(("https://", "http://")), "Missing source URL")
            require(s.get("verification_scope"), "Missing verification scope")
        split = fi[s["family_id"]]["split"]
        keys = (s.get("source_group_id", s["source_id"]),)
        for key in keys:
            require(key not in source_splits or source_splits[key] == split,
                    "Same source/repost group crosses splits")
            source_splits[key] = split
        normalized = "".join(s["text"].split()).casefold()
        require(normalized not in normalized_text_splits or normalized_text_splits[normalized] == split,
                "Exact normalized support duplicate crosses splits")
        normalized_text_splits[normalized] = split
    for f in families:
        require(any(s["family_id"] == f["family_id"] for s in supports), "Family has no support")
        if cfg["mode"] == "main":
            require(f.get("eligibility") == "main_ready", "Main family has not passed collection audit")
            require(f.get("independence_reviewed") is True, "Source independence audit is required")
    return fi, si


def support_bundle(family, supports):
    # Explicit allowlist: arbitrary annotations/query/gold/results columns cannot enter extraction.
    items = [{"support_id": s["support_id"], "text": s["text"], "context": s["context"]}
             for s in sorted(supports, key=lambda x: x["support_id"])
             if s["family_id"] == family["family_id"]]
    return {"family_id": family["family_id"], "canonical_text": family["canonical_text"],
            "supports": items}


def make_extractions(families, supports, cfg):
    validate_dataset(families, supports, cfg)
    instruction = Path(cfg["extract_prompt"]).read_text(encoding="utf-8")
    out = []
    for family in sorted(families, key=lambda x: x["family_id"]):
        bundle = support_bundle(family, supports)
        payload = {"system": instruction, "user": json.dumps(bundle, ensure_ascii=False)}
        request = {
            "request_id": "extract_" + stable_hash([payload, cfg])[:20],
            "stage": "extraction", "family_id": family["family_id"],
            "split": family["split"], "mode": cfg["mode"],
            "support_ids": [s["support_id"] for s in bundle["supports"]],
            "support_bundle_hash": stable_hash(bundle),
            "model_id": cfg["model_id"], "temperature": cfg["extraction_temperature"],
            "max_output_tokens": cfg["extraction_max_output_tokens"],
            "seed_requested": cfg["seed"], "messages": payload,
            "input_characters": len(payload["system"]) + len(payload["user"]),
            "input_tokens": None, "execution_ready": False,
            "model_selected": cfg["model_id"] is not None,
            "execution_note": "Offline request only; model, source-use scope, tokenizer and budget require preflight.",
        }
        out.append(request)
    return out


def validate_fields(fields, support_ids):
    require(isinstance(fields, dict) and set(fields) == set(FIELDS), "Record must have exactly eight fields")
    allowed = set(support_ids)
    for name in FIELDS:
        require(isinstance(fields[name], list), f"{name} must be a list (empty allowed)")
        for fact in fields[name]:
            require(isinstance(fact, dict) and set(fact) == {"value", "evidence_ids", "certainty"},
                    f"Malformed fact in {name}")
            require(isinstance(fact["value"], str) and fact["value"].strip(), "Fact value must be nonempty text")
            require(fact["certainty"] in CERTAINTY, "Invalid certainty")
            ids = fact["evidence_ids"]
            require(isinstance(ids, list) and all(isinstance(x, str) for x in ids), "Bad evidence IDs")
            require(len(ids) == len(set(ids)) and set(ids) <= allowed, "Unknown/duplicate evidence ID")
            require(bool(ids) or (name == "uncertainty_notes" and fact["certainty"] == "tentative"),
                    "Unsupported assertion: mark uncertainty instead")
    return fields


def import_extractions(requests, responses, cfg):
    ri = index_unique(requests, "request_id")
    index_unique(responses, "request_id")
    records, failures = [], []
    for response in responses:
        rid = response["request_id"]
        require(rid in ri, "Response has unknown request ID")
        request = ri[rid]
        require(request["stage"] == "extraction", "Wrong request stage")
        require(request["mode"] == cfg["mode"], "Request mode mismatch")
        require(response["record_origin"] == cfg["record_origin"], "Record origin mismatch")
        require(cfg["model_id"] is not None and response["model_id"] == cfg["model_id"],
                "Set exact model ID before accepting responses")
        require(request["model_id"] == response["model_id"], "Response model differs from prepared request")
        for key in ("response_id", "created_at", "usage", "status", "output_text"):
            require(key in response, f"Missing response metadata: {key}")
        require(response["status"] in {"ok", "error"}, "Unknown response status")
        if response["status"] != "ok":
            failures.append({"request_id": rid, "family_id": request["family_id"],
                             "reason": response.get("error", "provider_error")})
            continue
        try:
            fields = validate_fields(json.loads(response["output_text"]), request["support_ids"])
        except (ValueError, TypeError) as err:
            failures.append({"request_id": rid, "family_id": request["family_id"],
                             "reason": f"invalid_record: {err}"})
            continue
        records.append({
            "schema_version": "0.1", "record_id": "record_" + stable_hash(response)[:20],
            "family_id": request["family_id"], "record_origin": response["record_origin"],
            "support_ids": request["support_ids"], "support_bundle_hash": request["support_bundle_hash"],
            "request_id": rid, "response_id": response["response_id"],
            "model_id": response["model_id"], "created_at": response["created_at"],
            "raw_response_hash": stable_hash(response), "fields": fields,
        })
    received = {r["request_id"] for r in responses}
    failures.extend({"request_id": rid, "family_id": r["family_id"], "reason": "response_missing"}
                    for rid, r in ri.items() if rid not in received)
    return records, failures


def render_narrative(fields):
    """No LLM summary: preserve each value, evidence link, certainty, order, and empty field."""
    sentences = []
    for name in FIELDS:
        facts = fields[name]
        if not facts:
            sentences.append(f"{STEMS[name]} 제공되지 않았다.")
        for fact in facts:
            evidence = "、".join(fact["evidence_ids"]) or "없음"
            sentences.append(f'{STEMS[name]} 「{fact["value"]}」이다. '
                             f'이 분석의 근거 용례는 {evidence}이며, 이 판단은 {CERTAINTY[fact["certainty"]]}.')
    return " ".join(sentences)


def render_structured(fields):
    # Same Korean labels/certainty meanings as B2; avoid English-key vs Korean-prose confound.
    return json.dumps({
        FIELD_LABELS[name]: [{"내용": fact["value"], "근거 용례": fact["evidence_ids"],
                             "판단": CERTAINTY[fact["certainty"]]} for fact in fields[name]]
        for name in FIELDS
    }, ensure_ascii=False, indent=2)


def content_ledger(fields):
    return [{"field": key, "ordinal": i, **fact}
            for key in FIELDS for i, fact in enumerate(fields[key])]


def generation_requests(families, supports, scenarios, records, cfg):
    fi, si = validate_dataset(families, supports, cfg)
    index_unique(scenarios, "scenario_id")
    recs = index_unique(records, "family_id")
    instruction = Path(cfg["generate_prompt"]).read_text(encoding="utf-8")
    requests, failures, audits = [], [], []
    for scenario in scenarios:
        family = fi[scenario["family_id"]]
        require(scenario["provenance"] in PROVENANCE, "Unknown scenario provenance")
        require(scenario["split"] == family["split"], "Scenario split differs from family split")
        require(bool(scenario["situation"].strip()), "Empty situation")
        if cfg["mode"] != "smoke":
            require(scenario.get("independent_authorship_reviewed") is True,
                    "Scenario needs independent-authorship/answer-leakage review")
            require(scenario.get("source_support_rephrase") is False, "Support rephrase is not an independent query")
        if family["split"] == "test" and cfg["mode"] != "smoke":
            require(scenario.get("locked") is True and scenario.get("lock_id"), "Test query must be locked")
        bundle = support_bundle(family, supports)
        # Only user-facing situation is passed, never gold, ratings, or administrative labels.
        base = {"situation": scenario["situation"], **bundle}
        base_text = json.dumps(base, ensure_ascii=False)
        record = recs.get(family["family_id"])
        if record:
            require(record["record_origin"] == cfg["record_origin"], "Human/fixture record excluded from automatic main method")
            require(record["model_id"] == cfg["model_id"], "Record model differs from configured extraction model")
            require(record["support_bundle_hash"] == stable_hash(bundle), "Record is from different support bundle")
            require(set(record["support_ids"]) == {s["support_id"] for s in bundle["supports"]},
                    "Record support IDs differ from raw conditions")
            fields = validate_fields(record["fields"], record["support_ids"])
            narrative = render_narrative(fields)
            structured = render_structured(fields)
            audits.append({"scenario_id": scenario["scenario_id"], "record_id": record["record_id"],
                          "fact_ledger": content_ledger(fields), "fields": list(FIELDS),
                          "display_field_labels": FIELD_LABELS,
                          "empty_fields": [k for k in FIELDS if not fields[k]],
                          "raw_bundle_hash": stable_hash(bundle), "base_hash": stable_hash(base),
                          "B2_extra_characters": len(narrative), "B3_extra_characters": len(structured),
                          "note": "Exact fact values/links/certainty shared; token count and framing are not identical."})
        for repeat in range(cfg["generation_repeats"]):
            for condition in cfg["conditions"]:
                item = {"stage": "generation", "condition": condition,
                        "family_id": family["family_id"], "lineage_group_id": family["lineage_group_id"],
                        "scenario_id": scenario["scenario_id"], "split": family["split"],
                        "repeat": repeat, "mode": cfg["mode"], "base_hash": stable_hash(base),
                        "model_id": cfg["model_id"], "temperature": cfg["temperature"],
                        "max_output_tokens": cfg["max_output_tokens"],
                        "seed_requested": cfg["seed"] + repeat, "candidate_count": 1,
                        "record_origin": None if condition == "B1" else cfg["record_origin"]}
                item["request_id"] = "generate_" + stable_hash([item, cfg, instruction, base,
                                                               record and record["fields"]])[:20]
                if condition != "B1" and not record:
                    failures.append({**item, "reason": "extraction_missing_or_failed"})
                    continue
                extra = "" if condition == "B1" else "\n\n추가 분석:\n" + (narrative if condition == "B2" else structured)
                item["messages"] = {"system": instruction, "user": base_text + extra}
                item["input_characters"] = len(instruction) + len(base_text + extra)
                item["input_tokens"] = None
                item["execution_ready"] = False
                item["model_selected"] = cfg["model_id"] is not None
                item["request_id"] = "generate_" + stable_hash(
                    {k: v for k, v in item.items() if k != "request_id"})[:20]
                requests.append(item)
    random.Random(cfg["seed"]).shuffle(requests)
    return requests, failures, audits


def export_evaluation(requests, responses, upstream_failures, scenarios, families, supports, cfg, output_dir):
    ri = index_unique(requests + upstream_failures, "request_id")
    request_ids = {r["request_id"] for r in requests}
    upstream_ids = {r["request_id"] for r in upstream_failures}
    re = index_unique(responses, "request_id")
    qi = index_unique(scenarios, "scenario_id")
    fi = index_unique(families, "family_id")
    require(set(re) <= request_ids, "Unexpected generation response")
    expected_cells = {(q["scenario_id"], c, n) for q in scenarios
                      for c in cfg["conditions"] for n in range(cfg["generation_repeats"])}
    actual_cells = [(r["scenario_id"], r["condition"], r["repeat"]) for r in ri.values()]
    require(len(actual_cells) == len(set(actual_cells)), "Duplicate logical experiment cell")
    require(set(actual_cells) == expected_cells, "Incomplete or unexpected B1/B2/B3 experiment grid")
    stage_a, stage_b, private = [], [], []
    validate_dataset(families, supports, cfg)
    for rid, request in ri.items():
        require(request["stage"] == "generation", "Wrong evaluation stage")
        require(request["mode"] == cfg["mode"], "Evaluation request mode mismatch")
        require(request["model_id"] == cfg["model_id"], "Evaluation request model mismatch")
        family = fi[request["family_id"]]
        require(request["lineage_group_id"] == family["lineage_group_id"], "Evaluation lineage mismatch")
        require(request["record_origin"] == (None if request["condition"] == "B1" else cfg["record_origin"]),
                "Evaluation record origin mismatch")
        scenario = qi[request["scenario_id"]]
        require(scenario["family_id"] == family["family_id"] and scenario["split"] == family["split"]
                and request["split"] == family["split"], "Evaluation family/split mismatch")
        base = {"situation": scenario["situation"], **support_bundle(family, supports)}
        require(stable_hash(base) == request["base_hash"], "Evaluation data changed after generation preparation")
        response = re.get(rid)
        status = ("extraction_failed" if rid in upstream_ids else "missing") if response is None else response["status"]
        if response:
            for key in ("response_id", "created_at", "usage", "output_text"):
                require(key in response, f"Missing generation response metadata: {key}")
            require(cfg["model_id"] is not None, "A model ID is required for actual response import")
            require(response["model_id"] == request["model_id"], "Generation model mismatch")
            require(response["record_origin"] == ("smoke_fixture" if cfg["mode"] == "smoke" else "automatic"),
                    "Generated output origin mismatch")
            require(status in {"ok", "error"}, "Unknown generation status")
        text = response.get("output_text", "") if response else ""
        if status == "ok" and not text.strip():
            status = "empty_output"
        item_id = "item_" + stable_hash([cfg["seed"], rid])[:16]
        private.append({"item_id": item_id, "request_id": rid, "condition": request["condition"],
                        "family_id": request["family_id"], "lineage_group_id": request["lineage_group_id"],
                        "scenario_id": request["scenario_id"], "repeat": request["repeat"], "status": status})
        if status != "ok":
            continue
        stage_a.append({"item_id": item_id, "situation": qi[request["scenario_id"]]["situation"],
                        "output_text": text, "situation_fit": "", "naturalness": "",
                        "willingness_to_use": "", "spontaneous_recognition": "", "comment": ""})
        stage_b.append({"item_id": item_id, "output_text": text,
                        "reference": support_bundle(fi[request["family_id"]], supports),
                        "identity_preservation": "", "prior_familiarity": "", "comment": ""})
    rng = random.Random(cfg["seed"])
    rng.shuffle(stage_a)
    rng.shuffle(stage_b)
    output_dir = Path(output_dir)
    for name in ("stage_a.jsonl", "stage_b.jsonl", "PRIVATE_key.jsonl", "export_notes.json"):
        require(not (output_dir / name).exists(), "Evaluation export must use a new directory")
    write_jsonl(output_dir / "stage_a.jsonl", stage_a)
    write_jsonl(output_dir / "stage_b.jsonl", stage_b)
    write_jsonl(output_dir / "PRIVATE_key.jsonl", private)
    write_json(output_dir / "export_notes.json", {
        "mode": cfg["mode"], "expected_output_origin": cfg["record_origin"],
        "imported_ok_model_responses": len(stage_a) if cfg["mode"] != "smoke" else 0,
        "imported_ok_fixture_responses": len(stage_a) if cfg["mode"] == "smoke" else 0,
        "warning": "Not a survey app or rater assignment. Never send PRIVATE_key. Finish ALL stage A items before releasing stage B.",
        "rater_count": cfg.get("evaluator_count"), "planned_outputs": len(ri),
        "rateable_outputs": len(stage_a), "failed_or_missing": len(ri) - len(stage_a),
        "note": "Upstream extraction failures are included in PRIVATE_key and the planned-output denominator."
    })
    return {"planned_requests": len(ri), "rateable": len(stage_a)}


def manifest(outdir, command, input_paths, cfg, counts):
    snapshot_dir = Path(outdir) / "input_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshots = {}
    for path in input_paths:
        source = Path(path)
        target = snapshot_dir / (file_hash(source)[:16] + "_" + source.name)
        if not target.exists():
            target.write_bytes(source.read_bytes())
        snapshots[str(path)] = str(target.relative_to(outdir))
    return {
        "created_at": datetime.now(timezone.utc).isoformat(), "command": command,
        "mode": cfg["mode"], "model_experiment_executed": False,
        "software_version": "0.1", "python_version": sys.version.split()[0],
        "inputs": {str(p): file_hash(p) for p in input_paths},
        "input_snapshots": snapshots,
        "config": cfg, "counts": counts,
        "tokens": None, "cost_usd": None,
        "note": "Offline preparation/import only; actual timing, usage, error/retry and provider metadata must come from raw responses.",
        "artifacts": {p.name: file_hash(p) for p in Path(outdir).glob("*") if p.is_file()},
    }


def summarize_ratings(ratings, key_rows, n_boot=2000, seed=20260911):
    """Descriptive only: average raters, pair scenarios, equal-weight families, resample lineage clusters."""
    keys = index_unique(key_rows, "item_id")
    observed = {}
    duplicates = set()
    for r in ratings:
        require(r["item_id"] in keys, "Unknown evaluation item")
        require((r["item_id"], r["rater_id"]) not in duplicates, "Duplicate rater/item")
        duplicates.add((r["item_id"], r["rater_id"]))
        require(keys[r["item_id"]]["status"] == "ok", "Rating attached to failed output")
        for metric in ("situation_fit", "identity_preservation"):
            value = r.get(metric)
            if value in ("", None):
                continue
            number = float(value)
            require(number in (1, 2, 3, 4, 5), "Ratings must be integer 1..5")
            observed.setdefault((r["item_id"], metric), []).append(number)
    contrasts = {}
    rng = random.Random(seed)
    for metric in ("situation_fit", "identity_preservation"):
        pair_items = {}
        for item, key in keys.items():
            vals = observed.get((item, metric))
            if vals:
                group = (key["lineage_group_id"], key["family_id"], key["scenario_id"], key["repeat"])
                pair_items.setdefault(group, {})[key["condition"]] = sum(vals) / len(vals)
        for comparison in ("B3-B1", "B3-B2", "B2-B1"):
            a, b = comparison.split("-")
            groups = {}
            incomplete = 0
            for group, vals in pair_items.items():
                if a in vals and b in vals:
                    groups.setdefault(group[0], {}).setdefault(group[1], []).append(vals[a] - vals[b])
                elif (a in vals) != (b in vals):
                    incomplete += 1
            clusters = [[sum(v) / len(v) for v in families.values()] for families in groups.values()]
            family_means = [x for cluster in clusters for x in cluster]
            estimate = sum(family_means) / len(family_means) if family_means else None
            ci = None
            if len(clusters) >= 2:
                samples = []
                for _ in range(n_boot):
                    chosen = [x for cluster in rng.choices(clusters, k=len(clusters)) for x in cluster]
                    samples.append(sum(chosen) / len(chosen))
                samples.sort()
                ci = [samples[int(0.025 * (n_boot - 1))], samples[int(0.975 * (n_boot - 1))]]
            planned_groups = {}
            for key in keys.values():
                group = (key["lineage_group_id"], key["family_id"], key["scenario_id"], key["repeat"])
                planned_groups.setdefault(group, set()).add(key["condition"])
            planned_pairs = {g for g, conditions in planned_groups.items() if a in conditions and b in conditions}
            completely_unrated = sum(not (a in pair_items.get(g, {}) or b in pair_items.get(g, {}))
                                      for g in planned_pairs)
            contrasts[metric + ":" + comparison] = {
                "planned_pairs": len(planned_pairs), "completely_unrated_pairs": completely_unrated,
                "equal_family_weighted_difference": estimate, "lineage_clusters": len(clusters),
                "families": len(family_means),
                "complete_pairs": sum(len(v) for families in groups.values() for v in families.values()),
                "partially_rated_pairs": incomplete,
                "descriptive_cluster_bootstrap_95_interval": ci,
                "confirmatory_test": False,
            }
    return {"contrasts": contrasts, "rating_rows": len(ratings), "planned_items": len(keys),
            "failed_or_missing_items": sum(k["status"] != "ok" for k in keys.values()),
            "unrated_ok_items": sum(k["status"] == "ok" and not any(i == item for i, m in observed)
                                   for item, k in keys.items()),
            "limitations": ["Complete-pair descriptive summaries are not the primary all-planned analysis.",
                            "No p-values, no multiplicity correction, no rater population uncertainty.",
                            "Small pilot cluster intervals must not be used to assert efficacy."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "prepare-extraction", "prepare-generation", "export-evaluation"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        p.add_argument("--families", required=True)
        p.add_argument("--supports", required=True)
        if name != "validate":
            p.add_argument("--out", required=True)
        if name in {"prepare-generation", "export-evaluation"}:
            p.add_argument("--scenarios", required=True)
        if name == "prepare-generation":
            p.add_argument("--records", required=True)
        if name == "export-evaluation":
            p.add_argument("--requests", required=True)
            p.add_argument("--responses", required=True)
            p.add_argument("--failures", required=True)
    p = sub.add_parser("import-extractions")
    for flag in ("config", "requests", "responses", "out"):
        p.add_argument("--" + flag, required=True)
    p = sub.add_parser("analyze")
    for flag in ("ratings", "key", "out"):
        p.add_argument("--" + flag, required=True)
    p = sub.add_parser("budget")
    p.add_argument("--config", required=True)
    p.add_argument("--families", type=int, required=True)
    p.add_argument("--scenarios-per-family", type=int, required=True)
    args = parser.parse_args()
    if args.command == "analyze":
        with Path(args.ratings).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        report = summarize_ratings(rows, read_jsonl(args.key))
        write_json(args.out, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    cfg = load_config(args.config)
    if args.command == "budget":
        f = args.families
        q = args.scenarios_per_family
        require(f >= 0 and q >= 0, "Counts cannot be negative")
        generations = f * q * len(cfg["conditions"]) * cfg["generation_repeats"]
        output = {
            "proposal_not_executed": True, "families": f, "scenarios": f * q,
            "extraction_calls_if_no_failures": f, "generation_calls_if_no_failures": generations,
            "total_calls_if_no_failures": f + generations,
            "rating_assignments": None if cfg["evaluator_count"] is None else generations * cfg["evaluator_count"],
            "cost_usd": None, "reason": "Model/prices and tokenizer usage are not fixed.",
            "formula": "sum(input_tokens * input_price/1e6 + output_tokens * output_price/1e6); add extraction once per family.",
            "no_claim": "Character counts are not token counts. Retries and rater compensation are separate.",
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    inputs = [args.config, cfg["extract_prompt"], cfg["generate_prompt"], __file__]
    if args.command != "import-extractions":
        families, supports = read_jsonl(args.families), read_jsonl(args.supports)
        validate_dataset(families, supports, cfg)
        inputs += [args.families, args.supports]
    if args.command == "validate":
        print(json.dumps({"valid": True, "families": len(families), "supports": len(supports),
                          "scope": "Schema/lineage/exact duplicates only; source authenticity and semantic near-duplicates need human review."}))
        return
    out = Path(args.out)
    require(not out.exists(), "Choose a new output directory to preserve prior runs")
    if args.command == "prepare-extraction":
        rows = make_extractions(families, supports, cfg)
        write_jsonl(out / "extraction.requests.jsonl", rows)
        counts = {"extraction_requests": len(rows)}
    elif args.command == "import-extractions":
        reqs, responses = read_jsonl(args.requests), read_jsonl(args.responses)
        records, failures = import_extractions(reqs, responses, cfg)
        write_jsonl(out / "automatic.records.jsonl", records)
        write_jsonl(out / "extraction.failures.jsonl", failures)
        inputs += [args.requests, args.responses]
        counts = {"records": len(records), "failures": len(failures)}
    elif args.command == "prepare-generation":
        scenarios, records = read_jsonl(args.scenarios), read_jsonl(args.records)
        rows, failures, audits = generation_requests(families, supports, scenarios, records, cfg)
        write_jsonl(out / "generation.requests.jsonl", rows)
        write_jsonl(out / "generation.failures.jsonl", failures)
        write_jsonl(out / "B2_B3_content_audit.jsonl", audits)
        inputs += [args.scenarios, args.records]
        counts = {"generation_requests": len(rows), "upstream_failures": len(failures)}
    else:
        counts = export_evaluation(read_jsonl(args.requests), read_jsonl(args.responses),
                                   read_jsonl(args.failures), read_jsonl(args.scenarios),
                                   families, supports, cfg, out)
        inputs += [args.requests, args.responses, args.failures, args.scenarios]
    write_json(out / "manifest.json", manifest(out, args.command, inputs, cfg, counts))
    print(json.dumps({"output_directory": str(out), **counts}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(f"VALIDATION ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
