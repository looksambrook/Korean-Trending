"""Attach source-linked, rule-based meme evidence to retrieved text candidates.

Frequency is insufficient for a meme decision. These outputs are machine triage,
not semantic judgments, verified memes, human review or independent-use counts.
No meme-name allowlist or search is used. Input discovery modes remain visible.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from urllib.parse import quote

from research_log import DuplicateRunError, ResearchLog, hash_file
from youtube_candidates import canonical, digest, normalized, prepare_observations, timestamp

ROOT = Path(__file__).resolve().parents[1]
CUES = re.compile(r"패러디|오마주|성대모사|따라(?:하|해|한|해봤)|밈|드립|인용|parody|\bmeme\b|\bremix\b|\bPOV\b", re.I)
NOTICE = re.compile(r"편집\s*[:：]|출연\s*[:：]|촬영\s*[:：]|문의|구독.{0,12}좋아요|매주.{0,20}(?:방송|공개|업로드)|공식\s*SNS", re.I)
SENTENCE_FORM = re.compile(r"(?:수\s*(?:없|있)다|인\s*줄|(?:라|다)는\s*(?:것|건)|(?:아니라|한다면)|(?:란|이란)\s*.+(?:다|것)|(?:할|하는)\s*때)")
HASHTAGS = re.compile(r"(?<!\w)#[\w가-힣]+")
DECORATION = re.compile(r"\[[^\]]{0,50}\]|【[^】]{0,50}】")


def primary_fields(row):
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    if row.get("modality") == "video_metadata" or row.get("kind") == "video":
        title = context.get("title", context.get("video_title"))
        description = context.get("description", context.get("video_description"))
        if isinstance(title, str):
            return [("title", title)] + ([("description", description)] if isinstance(description, str) else [])
    return [(row.get("modality", "text"), row["text"])]


def title_core(title):
    # A recurring show suffix is not evidence that episode headlines are variants.
    return normalized(HASHTAGS.sub(" ", DECORATION.sub(" ", re.split(r"\s[|｜]\s", title, maxsplit=1)[0])))


def excerpt(row, phrase=None):
    matches, cue_matches = [], []
    for field, text in primary_fields(row):
        for number, line in enumerate(text.splitlines(), 1):
            match = phrase is None or (" " + normalized(phrase) + " ") in (" " + normalized(line) + " ")
            if match:
                matches.append({"field": field, "line_number": number, "text": line})
                for cue in CUES.finditer(line):
                    cue_matches.append({"field": field, "line_number": number, "text": line,
                                        "cue": cue.group(), "relation_to_phrase": "same_line_only_not_semantic_validation"})
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    return {"observation_id": row["observation_id"], "video_id": row["video_id"],
            "channel_id": row.get("channel_id"), "source_url": row.get("source_url"),
            "source_title": context.get("title", context.get("video_title")),
            "posted_at": row["posted_at"], "available_at": row["available_at"],
            "collected_at": row["collected_at"], "provenance": row["provenance"],
            "sampling_origin": row.get("sample_method", "not_recorded"),
            "matched_source_lines": matches, "explicit_cues": cue_matches}


def variation_hint(left, right):
    """Conservative shared-clause proposal, not an assertion of meme genealogy."""
    a, b = title_core(left), title_core(right)
    at, bt = a.split(), b.split()
    if a == b or min(len(at), len(bt)) < 5 or max(len(at), len(bt)) > 36:
        return None
    matcher = SequenceMatcher(None, at, bt, autojunk=False)
    blocks = [x for x in matcher.get_matching_blocks() if x.size]
    shared = sum(x.size for x in blocks)
    if not blocks or max(x.size for x in blocks) < 3 or shared / min(len(at), len(bt)) < .45:
        return None
    # Require changed material in both sentences, not merely a title prefix.
    if not any(tag == "replace" for tag, *_ in matcher.get_opcodes()):
        return None
    frame, changes = [], []
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            frame.extend(at[a0:a1])
        else:
            frame.append("{observed_difference}")
            changes.append({"left": " ".join(at[a0:a1]), "right": " ".join(bt[b0:b1])})
    return {"shared_surface_frame": " ".join(frame), "observed_differences": changes,
            "shared_token_count": shared, "relation_status": "shared_clause_needs_context_review",
            "not_a_universal_slot_model": True}


def build_review(rows, candidates, *, cutoff, max_title_pairs=20000):
    if type(max_title_pairs) is not int or max_title_pairs < 1:
        raise ValueError("max_title_pairs must be a positive integer")
    evidence, exclusions = prepare_observations(list(rows), cutoff)
    if len({row["provenance"] for row in evidence}) > 1:
        raise ValueError("Mixed provenance must be reviewed separately")
    by_id = {row["observation_id"]: row for row in evidence}
    titles = []
    for row in evidence:
        for field, value in primary_fields(row):
            if field == "title":
                titles.append((row, value))
    created_at = datetime.now(timezone.utc).isoformat()
    families, comparisons = [], 0
    truncated = False
    for i, (left, left_title) in enumerate(titles):
        for right, right_title in titles[i+1:]:
            if left["video_id"] == right["video_id"]:
                continue
            if comparisons >= max_title_pairs:
                truncated = True
                break
            comparisons += 1
            hint = variation_hint(left_title, right_title)
            if hint:
                families.append({"family_hint_id": "mfh_" + digest([left["observation_id"], right["observation_id"]])[:20],
                    **hint, "source_evidence": [excerpt(left), excerpt(right)], "linked_at": created_at,
                    "evidence_cutoff": timestamp(cutoff).isoformat(),
                    "independent_use_count": None, "independence_status": "unknown",
                    "verified_meme": False})
        if truncated:
            break
    by_observation = defaultdict(list)
    family_by_id = {family["family_hint_id"]: family for family in families}
    for family in families:
        for example in family["source_evidence"]:
            by_observation[example["observation_id"]].append(family["family_hint_id"])

    reviewed = []
    for candidate in candidates:
        phrase = candidate.get("representative_phrase", "")
        forms = candidate.get("surface_forms") or [phrase]
        source_ids = candidate.get("evidence_observation_ids", [])
        observed, mismatches, missing = [], [], []
        for oid in source_ids:
            row = by_id.get(oid)
            if row is None:
                missing.append(oid)
                continue
            matched = [excerpt(row, form) for form in forms if form]
            matched = [item for item in matched if item["matched_source_lines"]]
            if not matched:
                mismatches.append(oid)
                continue
            combined = matched[0]
            for item in matched[1:]:
                combined["matched_source_lines"].extend(x for x in item["matched_source_lines"] if x not in combined["matched_source_lines"])
                combined["explicit_cues"].extend(x for x in item["explicit_cues"] if x not in combined["explicit_cues"])
            observed.append(combined)
        video_ids = {item["video_id"] for item in observed}
        channels = sorted({item["channel_id"] for item in observed if item["channel_id"]})
        cue_evidence = [{"observation_id": item["observation_id"], "source_url": item["source_url"], **cue}
                        for item in observed for cue in item["explicit_cues"]]
        # A relation between video titles must not promote an unrelated hashtag
        # or description word merely because it occurs on the same video.
        hints = sorted({hint for item in observed for hint in by_observation[item["observation_id"]]
                        if any((" " + normalized(form) + " ") in
                               (" " + family_by_id[hint]["shared_surface_frame"] + " ")
                               or (len(normalized(form).split()) >= 5 and
                                   normalized(form) == title_core(item.get("source_title") or ""))
                               for form in forms)})
        all_lines = [line for item in observed for line in item["matched_source_lines"]]
        notice_only = bool(all_lines) and all(line["field"] == "description" and NOTICE.search(line["text"]) for line in all_lines)
        name_match = False
        suffix_matches = 0
        for item in observed:
            row = by_id[item["observation_id"]]
            context = row.get("context") if isinstance(row.get("context"), dict) else {}
            label = context.get("channel_label", "")
            name_match |= bool(label and normalized(label) == normalized(phrase))
            for field, text in primary_fields(row):
                if field == "title":
                    parts = re.split(r"\s[|｜]\s", text)
                    suffix_matches += int(len(parts) > 1 and normalized(phrase) in {normalized(p) for p in parts[1:]})
        topic_hint = name_match or bool(candidate.get("ubiquitous_hashtag_only_flag")) or suffix_matches >= 2
        why = []
        if notice_only:
            kind, decision = "production_boilerplate", "likely_topic"
            why.append("All matching source lines are production/contact/schedule notices.")
        elif topic_hint and not cue_evidence:
            kind, decision = "named_entity_or_topic", "likely_topic"
            why.append("Matched channel label, repeated show suffix, or ubiquitous channel hashtag; recurrence alone is insufficient.")
        elif cue_evidence or hints:
            kind, decision = "meme_form_hint", "possible_meme"
            why.append("Explicit source wording or a shared-clause relation warrants contextual meme review.")
        else:
            kind, decision = "insufficient_context", "requires_evidence"
            why.append("Literal repetition provides no evidence of imitation, shared use conventions, or meme meaning.")
        if len(video_ids) < 2:
            why.append("Fewer than two distinct source videos support the matched expression.")
        reviewed.append({"candidate_id": candidate.get("candidate_id"), "repeated_expression": phrase,
            "surface_forms": forms, "candidate_type": kind, "decision": decision, "why": why,
            "review_mode": "automatic_rule_based_triage", "verified_meme": False,
            "literal_recurrence": {"distinct_source_videos": len(video_ids), "observation_count": len(observed),
                                   "independent_use_count": None},
            "explicit_parody_quote_imitation_cues": cue_evidence, "variation_hint_ids": hints,
            "creator_channels": channels, "creator_channels_claim": "source_channel_ids_not_independent_creators",
            "observed_examples": observed, "missing_or_excluded_evidence_ids": missing,
            "phrase_not_found_in_source_ids": mismatches,
            "source_comparison": "normalized_literal_match_with_original_source_lines",
            "followup_search_url": "https://www.youtube.com/results?search_query=" + quote(phrase) if phrase else None,
            "followup_search_mode": "candidate_seeded_expansion_not_name_free_discovery",
            "unknowns": ["semantic_meme_relation", "independent_reuse", "origin", "current_popularity", "missed_memes"],
            "human_review_minutes": 0, "linked_at": created_at, "evidence_cutoff": timestamp(cutoff).isoformat()})
    queue = []
    for row, title in titles:
        cues = [m.group() for m in CUES.finditer(title)]
        grammar = [m.group() for m in SENTENCE_FORM.finditer(title)]
        if not cues and not grammar and not by_observation[row["observation_id"]]:
            continue
        queue.append({"seed_id": "msq_" + digest(row["observation_id"])[:20], "title": title,
                      "queue_reason": {"explicit_cues": cues, "sentence_form_hints": grammar,
                                       "variation_hint_ids": by_observation[row["observation_id"]]},
                      "decision": "requires_evidence", "verified_meme": False,
                      "minimum_recurrence_required_for_queue": False,
                      "source_evidence": excerpt(row),
                      "next_action": "Locate independent contextual uses and compare meaning, without treating the title as a verified meme."})
    summary = {"status": "machine_evidence_triage_completed_not_meme_confirmation", "reviewed_candidates": len(reviewed),
               "decisions": dict(Counter(item["decision"] for item in reviewed)), "family_hint_count": len(families),
               "seed_queue_count": len(queue), "eligible_observations": len(evidence), "excluded_observations": len(exclusions),
               "title_pairs_compared": comparisons, "title_pair_limit_reached": truncated,
               "verified_meme_count": None, "human_review_minutes": 0, "paid_api_calls": 0,
               "cutoff": timestamp(cutoff).isoformat(), "created_at": created_at,
               "limitations": ["Text metadata only; images, audio, performance and conversational meaning are not examined.",
                   "Shared sentence fragments may be coincidences, copied captions, or ordinary titles.",
                   "This triage cannot establish meme prevalence, independent reuse, origin, accuracy or recall.",
                   "Every created relation is available at created_at, never backdated to the evidence cutoff."]}
    return {"reviews": reviewed, "family_hints": families, "seed_candidate_review_queue": queue,
            "exclusions": exclusions, "summary": summary}


def run(input_paths, candidates_path, output_dir, *, cutoff, db="logs/research.sqlite3", max_title_pairs=20000):
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ValueError("Output directory already exists; existing evidence is preserved")
    paths = [Path(p) for p in input_paths]
    if candidates_path:
        paths.append(Path(candidates_path))
    snapshots = [{"id": str(path.resolve().relative_to(ROOT)) if path.resolve().is_relative_to(ROOT) else path.name,
                  "sha256": hash_file(path)} for path in paths]
    cutoff = timestamp(cutoff).isoformat()
    log = ResearchLog(db)
    spec = {"objective": "Distinguish repeated topics from source-linked possible meme features without equating frequency with meme status",
            "stage": "offline_validation", "parameters": {"algorithm": "meme_evidence_review_v01", "cutoff": cutoff,
                                                          "max_title_pairs": max_title_pairs},
            "data_snapshots": snapshots, "code_hashes": {"meme_evidence_review.py": hash_file(__file__),
                "youtube_candidates.py": hash_file(ROOT / "src/youtube_candidates.py"),
                "research_log.py": hash_file(ROOT / "src/research_log.py")},
            "prompt_hashes": {}, "config_hashes": {}, "modality": ["text"], "provenance": "mixed", "time_cutoff": cutoff}
    entry = log.begin(spec)
    try:
        def read(path):
            return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        rows = [row for path in input_paths for row in read(path)]
        candidates = read(candidates_path) if candidates_path else []
        for path, snapshot in zip(paths, snapshots):
            if hash_file(path) != snapshot["sha256"]:
                raise ValueError("Input changed during loading")
        result = build_review(rows, candidates, cutoff=cutoff, max_title_pairs=max_title_pairs)
        result["summary"]["run_id"] = entry["run_id"]
        result["summary"]["input_snapshots"] = snapshots
        output_dir.mkdir(parents=True, exist_ok=False)
        artifacts = []
        for key, value in result.items():
            path = output_dir / (key + (".json" if key == "summary" else ".jsonl"))
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n" if key == "summary"
                             else "".join(canonical(item) + "\n" for item in value))
            artifacts.append(path)
        log.finish(entry["run_id"], status="succeeded", notes=f"Machine triaged {len(result['reviews'])} text candidates and proposed {len(result['family_hints'])} surface relations; none are confirmed memes.",
                   artifacts=artifacts, actual_cost={"amount": 0, "currency": "USD", "api_units": 0, "human_minutes": 0})
        return result["summary"]
    except BaseException as exc:
        log.finish(entry["run_id"], status="failed", notes=f"Meme evidence triage failed: {type(exc).__name__}")
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, help="Observation JSONL; repeat for candidate-seeded follow-up snapshots")
    parser.add_argument("--candidates")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--db", default="logs/research.sqlite3")
    parser.add_argument("--max-title-pairs", type=int, default=20000)
    args = vars(parser.parse_args(argv))
    try:
        print(json.dumps(run(args.pop("input"), args.pop("candidates"), args.pop("output_dir"), **args), ensure_ascii=False, indent=2))
    except (DuplicateRunError, ValueError, OSError) as exc:
        parser.exit(2, f"Evidence review error: {exc}\n")


if __name__ == "__main__":
    main()
