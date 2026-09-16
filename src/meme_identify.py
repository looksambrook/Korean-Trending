"""Identify input text against discovered, time-valid source evidence.

This is deterministic retrieval and sentence-frame matching, not an LLM call,
model training, semantic validation or human evaluation. No meme name list is
embedded here. User input is never inserted into collected source examples.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from meme_discovery_engine import _comparison, _normal
from research_log import DuplicateRunError, ResearchLog, hash_file, read_json

ROOT = Path(__file__).resolve().parents[1]
INPUT_PROVENANCE = ("researcher_authored", "ai_synthetic", "actual_collected")


def _time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def _eligibility(family, result, cutoff):
    """Fail closed for knowledge that depends on unavailable source versions."""
    knowledge = family.get("knowledge") if isinstance(family.get("knowledge"), dict) else {}
    knowledge_at = knowledge.get("available_at") or family.get("created_at") or result.get("created_at")
    for label, stamp in (("knowledge", knowledge_at), ("family", family.get("created_at")),
                         ("snapshot", result.get("created_at"))):
        if stamp is None and label != "knowledge":
            continue
        parsed = _time(stamp)
        if parsed is None:
            return None, f"{label}_availability_missing_or_invalid"
        if parsed > cutoff:
            return None, f"{label}_available_after_as_of"
    examples = []
    for example in family.get("examples", []):
        available, posted = _time(example.get("available_at")), _time(example.get("posted_at"))
        if available and posted and available <= cutoff and posted <= cutoff:
            examples.append(example)
    if not examples:
        return None, "no_time_valid_source_examples"
    available_ids = {e.get("observation_id") for e in examples if e.get("observation_id")}
    dependencies = set(knowledge.get("evidence_ids") or [])
    if dependencies - available_ids:
        return None, "stored_meaning_depends_on_unavailable_evidence"
    for relation in family.get("relationships", []):
        available = relation.get("available_at") or relation.get("created_at")
        if available and (_time(available) is None or _time(available) > cutoff):
            return None, "family_relationship_available_after_as_of"
        endpoints = {relation.get("left"), relation.get("right")} - {None}
        if endpoints - available_ids:
            return None, "family_relationship_depends_on_unavailable_evidence"
    return examples, None


def identify(result: dict, text: str, *, as_of: str | None = None,
             input_provenance: str = "researcher_authored") -> dict:
    """Return tentative, source-linked matches or an explicit unknown.

    Only a comparison frame attested in source titles, or an exact expression
    actually present in family sources, can produce a match. A match never says
    that the input is a verified meme or that the stored meaning is exhaustive.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a nonempty string")
    if input_provenance not in INPUT_PROVENANCE:
        raise ValueError("unsupported input provenance")
    now = datetime.now(timezone.utc)
    cutoff = _time(as_of) if as_of is not None else now
    if cutoff is None:
        raise ValueError("as_of must include an explicit timezone")
    normalized = _normal(text)
    input_frame = _comparison(text)
    matches, excluded, eligible_count = [], [], 0
    for family in result.get("families", []):
        examples, exclusion = _eligibility(family, result, cutoff)
        if exclusion:
            excluded.append({"family_id": family.get("family_id"), "reason": exclusion})
            continue
        eligible_count += 1
        frames = [(e, _comparison(e.get("title") or e.get("source_title") or "")) for e in examples]
        frame_matches = [(e, frame) for e, frame in frames
                         if input_frame and frame and frame["frame_key"] == input_frame["frame_key"]]
        match_type, reason, extracted, rank = None, None, None, 0
        if family.get("kind") == "sentence_comparison" and frame_matches:
            match_type = "shared_observed_sentence_frame"
            reason = "입력의 수식과 비교 구조가 저장된 실제 제목의 구조와 일치합니다. 새 대상에 같은 밈 의도가 있는지는 이 구조만으로 확정하지 않습니다."
            extracted = {"shared_modifier": input_frame["prefix"], "A": input_frame["left"], "B": input_frame["right"],
                         "provenance": input_provenance, "is_collected_source_example": False}
            rank = 2
        else:
            expression = _normal(family.get("label"))
            if (len(expression) >= 3 and "{" not in expression and expression in normalized and
                    any(expression in _normal((e.get("title") or "") + "\n" + (e.get("description") or "")) for e in examples)):
                match_type = "exact_observed_expression"
                reason = "입력에 저장된 자료에서 관찰한 표현이 포함되어 있습니다. 같은 이름이나 문구의 다른 뜻일 수 있으므로 실제 사용 의도는 확정하지 않습니다."
                rank = 1
        if match_type is None:
            continue
        knowledge = family.get("knowledge") or {}
        matches.append({"family_id": family.get("family_id"), "label": family.get("label"),
                        "kind": family.get("kind"), "family_evidence_status": family.get("status"),
                        "match_type": match_type, "decision": "tentative_match", "reason": reason,
                        "source_examples": examples,
                        "stored_meaning": knowledge.get("meaning"),
                        "stored_meaning_method": knowledge.get("method", "not_recorded"),
                        "knowledge_available_at": knowledge.get("available_at") or family.get("created_at") or result.get("created_at"),
                        "uncertainties": list(dict.fromkeys([*knowledge.get("unknowns", []),
                            "입력 문맥의 실제 밈 사용 의도", "동일 표현의 다른 의미 가능성", "사람 검수 미실시"])),
                        "input_structure": extracted, "is_definitive_identification": False,
                        "_rank": rank})
    matches.sort(key=lambda item: (-item["_rank"], str(item.get("family_id"))))
    for match in matches:
        match.pop("_rank")
    return {"schema_version": "meme-identification-v1", "created_at": now.isoformat(),
            "as_of": cutoff.isoformat(), "input": {"text": text, "provenance": input_provenance,
                                                    "is_collected_source_example": input_provenance == "actual_collected"},
            "status": "tentative_match" if matches else "unknown",
            "reason": "저장된 용례에 근거한 잠정 연결입니다." if matches else
                      "해당 시점에 이용 가능한 계열에서 실제 표현 또는 비교 구조가 일치하는 근거를 찾지 못했습니다. 밈이 아니라는 뜻은 아닙니다.",
            "matches": matches, "eligible_family_count": eligible_count,
            "excluded_knowledge": excluded, "source_run_id": result.get("run_id"),
            "method": "deterministic_source_retrieval_and_observed_frame_matching",
            "model_calls": 0, "model_weights_trained": False, "human_review_minutes": 0,
            "interpretation_notice": "저장된 근거를 조회하여 규칙으로 연결합니다. LLM의 의미 이해 평가나 가중치 학습 결과가 아닙니다.",
            "collection_cost_usd": 0}


def run(result_path, text, *, output_path=None, db=None, as_of=None,
        input_provenance="researcher_authored", retry_of=None, retry_reason=None):
    source = Path(result_path).resolve()
    result = read_json(source)
    if not isinstance(result, dict) or not isinstance(result.get("families"), list):
        raise ValueError("result must contain a families array")
    if output_path is not None and Path(output_path).exists():
        raise ValueError("output already exists; select a new path")
    cutoff = as_of or datetime.now(timezone.utc).isoformat()
    if _time(cutoff) is None:
        raise ValueError("as_of must include an explicit timezone")
    if input_provenance not in INPUT_PROVENANCE or not isinstance(text, str) or not text.strip():
        raise ValueError("invalid input text or provenance")
    example_provenance = {e.get("provenance", "not_applicable") for f in result["families"] for e in f.get("examples", [])}
    combined = example_provenance | {input_provenance}
    provenance = next(iter(combined)) if len(combined) == 1 and combined <= set(INPUT_PROVENANCE) else "mixed"
    spec = {"objective": "Retrieve time-valid discovered meme evidence for an input phrase; no model or human evaluation",
            "stage": "offline_validation", "parameters": {
                "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "input_provenance": input_provenance, "is_model_understanding_evaluation": False,
                "model_weights_trained": False, "source_run_id": result.get("run_id")},
            "data_snapshots": [{"id": str(source), "sha256": hash_file(source)}],
            "code_hashes": {name: hash_file(ROOT / "src" / name) for name in
                            ("meme_identify.py", "meme_discovery_engine.py", "research_log.py")},
            "prompt_hashes": {}, "config_hashes": {}, "modality": ["text", "video_metadata"],
            "provenance": provenance, "time_cutoff": cutoff}
    log = ResearchLog(db or ROOT / "logs" / "research.sqlite3")
    record = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    output = Path(output_path) if output_path else ROOT / "data" / "derived" / "meme_identify" / (record["run_id"] + ".json")
    try:
        identified = identify(result, text, as_of=cutoff, input_provenance=input_provenance)
        identified["run_id"] = record["run_id"]
        identified["source_snapshot_sha256"] = spec["data_snapshots"][0]["sha256"]
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(identified, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        log.finish(record["run_id"], status="succeeded",
                   notes=f"Deterministic retrieval returned {identified['status']} with {len(identified['matches'])} tentative matches. No network, model calls, training, human review, or accuracy claim.",
                   artifacts=[output], actual_cost={"amount": 0, "currency": "USD", "human_minutes": 0})
    except Exception as exc:
        log.finish(record["run_id"], status="failed", notes=f"Identification failed: {type(exc).__name__}",
                   artifacts=[], actual_cost={"amount": 0, "currency": "USD", "human_minutes": 0})
        raise
    return identified, output.resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--as-of", help="ISO timestamp with timezone; exclude later knowledge and evidence")
    parser.add_argument("--input-provenance", choices=INPUT_PROVENANCE, default="researcher_authored")
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = parser.parse_args(argv)
    try:
        identified, output = run(args.result, args.text, output_path=args.output, db=args.db,
                                 as_of=args.as_of, input_provenance=args.input_provenance,
                                 retry_of=args.retry_of, retry_reason=args.retry_reason)
        print(json.dumps({"output": str(output), "result": identified}, ensure_ascii=False, indent=2))
        return 0
    except DuplicateRunError as exc:
        print(json.dumps({"error": "duplicate_run", "existing_run_id": exc.existing["run_id"]}))
        return 3
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
