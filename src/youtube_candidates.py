"""Offline, name-free retrieval of repeated YouTube text and possible variants.

This is a candidate retriever, not a validated meme classifier or understanding
model. It never assigns meaning, origin, independent authorship or gold labels.
All retained evidence must have been posted, collected and available by cutoff.
The CLI registers its immutable input/config/code fingerprint before retrieval.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from research_log import DuplicateRunError, ResearchLog, hash_file

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"[가-힣A-Za-z0-9]+")
PROVENANCE = {"actual_collected", "researcher_authored", "ai_synthetic"}
HASHTAG = re.compile(r"(?<![\w#])#([가-힣A-Za-z0-9_]+)")
CONTACT = re.compile(r"https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", re.I)
PRODUCTION_NOTICE = re.compile(
    r"^[\W_]*(?:출연|편집|썸네일|촬영|카메라|마이크|장비|협업\s*제안|광고\s*문의|비즈니스\s*문의|"
    r"business\s*(?:inquiries|contact)|문의\s*메일)\s*[:：]", re.I)
UPLOAD_NOTICE = re.compile(
    r"(?:같이\s*보면\s*좋은\s*추천\s*영상|생방송\s*원본|방송분\s*[:：]|"
    r"(?:매주|매일).{0,30}(?:업로드|방송|공개)|구독.{0,12}좋아요|좋아요.{0,12}구독)", re.I)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def timestamp(value):
    if not isinstance(value, str) or not value:
        raise ValueError("missing timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO timestamp") from exc
    if result.tzinfo is None:
        raise ValueError("timestamp requires explicit timezone")
    return result.astimezone(timezone.utc)


def normalized(text):
    return " ".join(TOKEN.findall(unicodedata.normalize("NFKC", text).lower()))


def _source(row):
    # Title and description of one video do not constitute two documents.
    return (row["platform"].lower(), row["source_id"])


def prepare_observations(rows, cutoff):
    """Return as-observed snapshots plus an explicit record of every exclusion."""
    cutoff = timestamp(cutoff)
    valid, excluded, seen = [], [], {}
    for position, original in enumerate(rows, 1):
        if not isinstance(original, dict):
            excluded.append({"input_line": position, "reason": "not_an_object"})
            continue
        row = dict(original)
        oid = row.get("observation_id")
        rejection = {"input_line": position, "observation_id": oid}
        try:
            for name in ("observation_id", "platform", "source_id", "video_id", "modality", "text"):
                if not isinstance(row.get(name), str) or not row[name].strip():
                    raise ValueError("missing_or_invalid_" + name)
            if row["platform"].lower() != "youtube":
                raise ValueError("unsupported_platform")
            if row.get("provenance") not in PROVENANCE:
                raise ValueError("missing_or_invalid_provenance")
            for name in ("posted_at", "collected_at", "available_at"):
                try:
                    instant = timestamp(row.get(name))
                except ValueError:
                    raise ValueError("missing_or_invalid_" + name)
                if instant > cutoff:
                    raise ValueError(name + "_after_cutoff")
            if row.get("updated_at"):
                try:
                    updated = timestamp(row["updated_at"])
                except ValueError:
                    raise ValueError("invalid_updated_at")
                if updated > cutoff:
                    raise ValueError("updated_at_after_cutoff")
            # A first-seen timestamp cannot make a later fetched text snapshot
            # available historically. collected_at is checked independently.
            if timestamp(row["posted_at"]) > timestamp(row["collected_at"]):
                raise ValueError("posted_after_collection")
            if row.get("updated_at") and timestamp(row["updated_at"]) > timestamp(row["collected_at"]):
                raise ValueError("updated_after_collection")
            if timestamp(row["available_at"]) < timestamp(row["posted_at"]):
                raise ValueError("available_before_posted")
            row["_normalized"] = normalized(row["text"])
            if not row["_normalized"]:
                raise ValueError("empty_tokenized_text")
            identity = digest(original)
            if oid in seen:
                if seen[oid] == identity:
                    raise ValueError("duplicate_observation")
                raise ValueError("conflicting_observation_id")
            seen[oid] = identity
            valid.append(row)
        except ValueError as exc:
            excluded.append({**rejection, "reason": str(exc)})
    # A conflicting ID is unsafe as an evidence key: exclude its first row too.
    conflicts = {item["observation_id"] for item in excluded if item["reason"] == "conflicting_observation_id"}
    survivors = []
    for row in valid:
        if row["observation_id"] in conflicts:
            excluded.append({"observation_id": row["observation_id"], "reason": "conflicting_observation_id"})
        else:
            survivors.append(row)
    # Keep latest fetched snapshot of each source/modality. Older snapshots stay
    # in the immutable input and are accounted for, but do not multiply counts.
    latest = {}
    for row in survivors:
        key = (*_source(row), row["modality"])
        old = latest.get(key)
        if old is None or (timestamp(row["collected_at"]), row["observation_id"]) > (
                timestamp(old["collected_at"]), old["observation_id"]):
            if old:
                excluded.append({"observation_id": old["observation_id"], "reason": "superseded_source_snapshot"})
            latest[key] = row
        else:
            excluded.append({"observation_id": row["observation_id"], "reason": "superseded_source_snapshot"})
    return sorted(latest.values(), key=lambda row: row["observation_id"]), excluded


def phrases(text, max_words):
    tokens = text.split()
    result = set()
    for size in range(2, min(max_words, len(tokens)) + 1):
        for start in range(len(tokens) - size + 1):
            phrase = " ".join(tokens[start:start + size])
            if re.search(r"[가-힣]", phrase):
                result.add(phrase)
    # Single Korean expressions remain possible; slot structure is not required.
    result.update(token for token in tokens if len(token) >= 3 and re.fullmatch(r"[가-힣]+", token))
    return result


def _maximal_phrases(index, protected=()):
    """Remove nested ngrams only when they have exactly the same evidence set."""
    groups = defaultdict(list)
    for phrase, support in index.items():
        groups[tuple(sorted(support))].append(phrase)
    result = {}
    for support, group in groups.items():
        accepted = []
        for phrase in sorted(group, key=lambda p: (-len(p.split()), -len(p), p)):
            if phrase in protected or not any((" " + phrase + " ") in (" " + longer + " ") for longer in accepted):
                accepted.append(phrase)
                result[phrase] = set(support)
    return result


def _metadata_fields(row):
    """Primary text fields only: a comment's parent title is context, not a use."""
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    modality = row["modality"].lower()
    title = context.get("title", context.get("video_title"))
    description = context.get("description", context.get("video_description"))
    if (modality == "video_metadata" or row.get("kind") == "video") and isinstance(title, str):
        return [(name, value) for name, value in (("title", title), ("description", description)) if isinstance(value, str)]
    if modality in {"title", "description"}:
        return [(modality, row["text"])]
    return [("comment" if "comment" in modality or row.get("kind") in {"comment", "reply"} else "text", row["text"])]


def metadata_index(observations, max_words, boilerplate_min_videos):
    """Extract within lines/fields; retain every suppressed fragment separately.

    Repeated description lines are only suspected boilerplate, never verified
    non-memes. Titles and hashtag tokens are preserved even on those lines.
    """
    lines, repeated = [], defaultdict(set)
    for row in observations:
        for field, text in _metadata_fields(row):
            for number, raw_line in enumerate(text.splitlines(), 1):
                raw_line = unicodedata.normalize("NFKC", raw_line)
                without_contacts = CONTACT.sub("\n", raw_line)
                without_tags = HASHTAG.sub("\n", without_contacts)
                # Masked fragments break ngrams; they are never joined together.
                segments = [normalized(part) for part in without_tags.splitlines() if normalized(part)]
                line = {"row": row, "field": field, "line_number": number, "raw_text": raw_line,
                        "segments": segments, "signature": tuple(segments)}
                lines.append(line)
                if field == "description" and row.get("channel_id") and segments:
                    repeated[(row["channel_id"], tuple(segments))].add(row["video_id"])
    index, origins, suppressed_index = defaultdict(set), defaultdict(lambda: defaultdict(set)), defaultdict(set)
    suppressed, protected = [], set()
    for line in lines:
        row, field, raw_line = line["row"], line["field"], line["raw_text"]
        oid = row["observation_id"]
        location = {"observation_id": oid, "video_id": row["video_id"], "channel_id": row.get("channel_id"),
                    "field": field, "line_number": line["line_number"]}
        # Hashtags are standalone observed tokens; adjacent tags never form a phrase.
        for match in HASHTAG.finditer(CONTACT.sub("\n", raw_line)):
            tag = normalized(match.group(1))
            if not tag or not re.search(r"[가-힣]", tag):
                continue
            index[tag].add(oid)
            origins[tag]["hashtag"].add(oid)
            protected.add(tag)
        for match in CONTACT.finditer(raw_line):
            suppressed.append({**location, "reason": "url_or_email_fragment", "raw_text": match.group(),
                               "suppression_status": "non_content_fragment", "candidate_phrases": []})
        reason = None
        if field == "description":
            if PRODUCTION_NOTICE.search(raw_line):
                reason = "production_or_contact_notice"
            elif UPLOAD_NOTICE.search(raw_line):
                reason = "upload_or_navigation_notice"
            elif len(repeated.get((row.get("channel_id"), line["signature"]), ())) >= boilerplate_min_videos:
                reason = "same_channel_repeated_description_line"
        extracted = set().union(*(phrases(segment, max_words) for segment in line["segments"])) if line["segments"] else set()
        if reason and extracted:
            suppressed.append({**location, "reason": reason, "raw_text": raw_line,
                               "suppression_status": "suspected_boilerplate_not_gold_non_meme",
                               "candidate_phrases": sorted(extracted), "hashtags_preserved": True})
            for phrase in extracted:
                suppressed_index[phrase].add(oid)
        else:
            for phrase in extracted:
                index[phrase].add(oid)
                origins[phrase][field].add(oid)
    return index, origins, suppressed_index, suppressed, protected


def _similarity(left, right):
    a, b = left.split(), right.split()
    if abs(len(a) - len(b)) > 1 or len(set(a) & set(b)) < 2:
        return None
    if (" " + left + " ") in (" " + right + " ") or (" " + right + " ") in (" " + left + " "):
        return None
    ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
    ca, cb = {left[i:i+2] for i in range(len(left)-1)}, {right[i:i+2] for i in range(len(right)-1)}
    jaccard = len(ca & cb) / max(1, len(ca | cb))
    if ratio < .72 or jaccard < .4:
        return None
    return {"sequence_similarity": round(ratio, 4), "character_bigram_jaccard": round(jaccard, 4),
            "shared_tokens": sorted(set(a) & set(b))}


def detect_candidates(rows, *, cutoff, method="D0", min_documents=2, min_videos=2,
                      max_words=6, max_candidates=100, max_phrase_nodes=3000,
                      max_variant_pairs=100000, baseline_end=None,
                      text_scope="metadata_content", boilerplate_min_videos=3):
    """Model-free prototype; positive output means 'review this', never 'a meme'."""
    if method not in {"D0", "D1"}:
        raise ValueError("method must be D0 or D1")
    if text_scope not in {"metadata_content", "full_text"}:
        raise ValueError("text_scope must be metadata_content or full_text")
    for label, value, minimum in (("min_documents", min_documents, 2), ("min_videos", min_videos, 2),
                                  ("max_words", max_words, 2), ("max_candidates", max_candidates, 1),
                                  ("max_phrase_nodes", max_phrase_nodes, 1), ("max_variant_pairs", max_variant_pairs, 1),
                                  ("boilerplate_min_videos", boilerplate_min_videos, 2)):
        if type(value) is not int or value < minimum:
            raise ValueError(f"{label} must be an integer >= {minimum}")
    end = timestamp(cutoff)
    baseline = timestamp(baseline_end) if baseline_end else None
    if baseline and baseline >= end:
        raise ValueError("baseline_end must precede cutoff")
    raw = list(rows)
    observations, exclusions = prepare_observations(raw, cutoff)
    if len({row["provenance"] for row in observations}) > 1:
        raise ValueError("Run each provenance separately; synthetic/authored text cannot provide collected-data support")
    generated_at = datetime.now(timezone.utc).isoformat()
    by_id = {row["observation_id"]: row for row in observations}
    origins, suppressed_index, suppressed, protected = {}, {}, [], set()
    if text_scope == "full_text":
        index = defaultdict(set)
        for row in observations:
            for phrase in phrases(row["_normalized"], max_words):
                index[phrase].add(row["observation_id"])
    else:
        index, origins, suppressed_index, suppressed, protected = metadata_index(observations, max_words, boilerplate_min_videos)
    index = _maximal_phrases(index, protected)
    nodes_before_limit = len(index)
    # Deterministic resource bound is disclosed, including any dropped nodes.
    ordered = sorted(index, key=lambda p: (-len({_source(by_id[i]) for i in index[p]}), -len(p), p))
    index = {phrase: index[phrase] for phrase in ordered[:max_phrase_nodes]}
    parent = {phrase: phrase for phrase in index}

    def find(phrase):
        while parent[phrase] != phrase:
            parent[phrase] = parent[parent[phrase]]
            phrase = parent[phrase]
        return phrase

    edges, comparisons, pair_limit_reached = [], 0, False
    if method == "D1":
        buckets = defaultdict(list)
        for phrase in sorted(index):
            tokens = phrase.split()
            if len(tokens) >= 3:
                buckets[("start", tokens[0])].append(phrase)
                buckets[("end", tokens[-1])].append(phrase)
        compared = set()
        for bucket in sorted(buckets):
            members = buckets[bucket]
            for offset, left in enumerate(members):
                for right in members[offset + 1:]:
                    pair = (left, right)
                    if pair in compared:
                        continue
                    if comparisons >= max_variant_pairs:
                        pair_limit_reached = True
                        break
                    compared.add(pair)
                    comparisons += 1
                    similarity = _similarity(left, right)
                    if similarity:
                        parent[find(right)] = find(left)
                        edges.append({"left": left, "right": right, **similarity,
                                      "relation_status": "surface_similarity_only_needs_review",
                                      "linked_at": generated_at, "evidence_cutoff": end.isoformat()})
                if pair_limit_reached:
                    break
            if pair_limit_reached:
                break
    groups = defaultdict(list)
    for phrase in index:
        groups[find(phrase)].append(phrase)
    all_sources = {_source(row) for row in observations}
    denominators = {}
    if baseline:
        for name, predicate in (("baseline", lambda r: timestamp(r["posted_at"]) <= baseline),
                                ("recent", lambda r: timestamp(r["posted_at"]) > baseline)):
            denominators[name] = len({_source(row) for row in observations if predicate(row)})
    candidates = []
    for members in groups.values():
        support = set().union(*(index[phrase] for phrase in members))
        evidence = [by_id[oid] for oid in sorted(support)]
        sources = {_source(row) for row in evidence}
        videos = {row["video_id"] for row in evidence}
        if len(sources) < min_documents or len(videos) < min_videos:
            continue
        content_groups = defaultdict(list)
        for row in evidence:
            content_groups[digest(row["_normalized"])].append(row["observation_id"])
        duplicates = [sorted(ids) for ids in content_groups.values() if len(ids) > 1]
        variations = sorted(members, key=lambda p: (-len(index[p]), -len(p), p))
        title_ids = set().union(*(origins.get(phrase, {}).get("title", set()) for phrase in members))
        hashtag_ids = set().union(*(origins.get(phrase, {}).get("hashtag", set()) for phrase in members))
        description_ids = set().union(*(origins.get(phrase, {}).get("description", set()) for phrase in members))
        boilerplate_ids = set().union(*(suppressed_index.get(phrase, set()) for phrase in members))
        title_sources = {_source(by_id[oid]) for oid in title_ids}
        hashtag_sources = {_source(by_id[oid]) for oid in hashtag_ids}
        candidate_channels = {row["channel_id"] for row in evidence if row.get("channel_id")}
        channel_label_ids = {row["observation_id"] for row in evidence if isinstance(row.get("context"), dict)
                             and isinstance(row["context"].get("channel_label"), str)
                             and normalized(row["context"]["channel_label"]) in members}
        hashtag_channel_fractions = {}
        for channel in sorted(candidate_channels):
            denominator = len({row["video_id"] for row in observations if row.get("channel_id") == channel})
            numerator = len({by_id[oid]["video_id"] for oid in hashtag_ids if by_id[oid].get("channel_id") == channel})
            hashtag_channel_fractions[channel] = numerator / denominator if denominator else 0
        ubiquitous_hashtag_only = bool(hashtag_ids and not title_ids and
                                      len(hashtag_sources) == len(sources) and hashtag_channel_fractions and
                                      all(value >= .8 for value in hashtag_channel_fractions.values()))
        # A coarse, disclosed retrieval priority protects title phrases from
        # channel-wide tag repetition. Nothing is labeled a meme by this tier.
        content_priority = 2 if title_ids else (0 if ubiquitous_hashtag_only else 1)
        observed_days = Counter(timestamp(row["posted_at"]).date().isoformat() for row in evidence)
        family_edges = [edge for edge in edges if edge["left"] in members and edge["right"] in members]
        growth = None
        if baseline:
            before = {_source(row) for row in evidence if timestamp(row["posted_at"]) <= baseline}
            after = {_source(row) for row in evidence if timestamp(row["posted_at"]) > baseline}
            fractions = {name: len(support_set) / denominators[name] if denominators[name] else None
                         for name, support_set in (("baseline", before), ("recent", after))}
            growth = {"baseline_document_count": len(before), "recent_document_count": len(after),
                      "sample_document_fractions": fractions,
                      "fraction_difference": fractions["recent"] - fractions["baseline"]
                      if all(v is not None for v in fractions.values()) else None,
                      "claim_scope": "observed_sample_only_not_platform_growth"}
        candidates.append({
            "candidate_id": "ytc_" + digest({"phrases": sorted(members), "evidence": sorted(support)})[:20],
            "platform": "youtube", "method": method, "text_scope": text_scope,
            "method_status": "candidate_retrieval_prototype",
            "label": "unverified_candidate", "verified_meme": False, "requires_human_review": True,
            "representative_phrase": variations[0], "surface_forms": variations,
            "candidate_extraction_mode": "no_phrase_names_supplied",
            "sampling_name_seed_status": "not_inferred_check_collection_manifest_before_claiming_name_free_discovery",
            "sampling_methods": sorted({str(row.get("sample_method", "not_recorded")) for row in evidence}),
            "provenance_counts": dict(sorted(Counter(row["provenance"] for row in evidence).items())),
            "evidence_observation_ids": sorted(support), "source_video_ids": sorted(videos),
            "score_components": {"source_document_frequency": len(sources), "source_video_count": len(videos),
                "distinct_observed_content_count": len(content_groups), "observation_count": len(evidence),
                "surface_form_count": len(members), "possible_variant_edge_count": len(family_edges),
                "sample_document_fraction": len(sources) / len(all_sources) if all_sources else 0,
                "channel_count": len(candidate_channels), "title_support": len(title_sources),
                "hashtag_support": len(hashtag_sources), "description_support": len({_source(by_id[oid]) for oid in description_ids}),
                "boilerplate_support_excluded": len({_source(by_id[oid]) for oid in boilerplate_ids}),
                "content_priority": content_priority,
                "channel_label_match_support": len({_source(by_id[oid]) for oid in channel_label_ids}),
                "independent_use_count": None},
            "content_support_evidence": {"title": sorted(title_ids), "hashtag": sorted(hashtag_ids),
                                         "description": sorted(description_ids), "suppressed_boilerplate": sorted(boilerplate_ids),
                                         "channel_label_match": sorted(channel_label_ids)},
            "hashtag_fraction_of_observed_channel_videos": hashtag_channel_fractions,
            "ubiquitous_hashtag_only_flag": ubiquitous_hashtag_only,
            "exact_normalized_text_groups": duplicates,
            "repost_status": "unknown_exact_text_matches_are_possible_copies_or_references",
            "independence_status": "not_established_from_distinct_accounts_or_videos",
            "possible_variant_links": family_edges, "family_status": "provisional_surface_cluster",
            "posted_observation_counts_by_utc_day": dict(sorted(observed_days.items())), "sample_growth": growth,
            "first_observed_available_at": min(timestamp(row["available_at"]) for row in evidence).isoformat(),
            "last_observed_available_at": max(timestamp(row["available_at"]) for row in evidence).isoformat(),
            "linked_at": generated_at, "evidence_cutoff": end.isoformat(),
            "historical_availability_claim": "links_created_at_linked_at_not_backdated_to_evidence_cutoff",
            "meaning": None, "origin": None, "human_review_minutes": 0,
        })
    # Ranking dimensions are explicit; this is not a calibrated meme probability.
    if method == "D0":
        rank_fields = ["source_document_frequency", "source_video_count"]
    else:
        rank_fields = ["distinct_observed_content_count", "possible_variant_edge_count", "source_video_count"]
    if text_scope == "metadata_content" and method == "D1":
        rank_fields = ["content_priority", "title_support", "channel_count"] + rank_fields
    candidates.sort(key=lambda row: tuple(-row["score_components"][name] for name in rank_fields) + (row["candidate_id"],))
    before_limit = len(candidates)
    candidates = candidates[:max_candidates]
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
    public_evidence = [{key: value for key, value in row.items() if not key.startswith("_")} for row in observations]
    summary = {
        "status": "retrieval_completed_not_meme_validation", "method": method,
        "text_scope": text_scope, "boilerplate_min_videos": boilerplate_min_videos,
        "suppressed_fragments": len(suppressed),
        "suppression_reasons": dict(sorted(Counter(item["reason"] for item in suppressed).items())),
        "suppression_claim": "heuristic_content_filter_not_gold_non_meme_labels",
        "ngram_boundary_policy": "legacy_flattened_full_text" if text_scope == "full_text" else "within_field_line_and_unmasked_span_only",
        "hashtag_policy": "legacy_tokenization" if text_scope == "full_text" else "standalone_tokens_preserved_from_ngram_absorption",
        "metadata_ranking_policy": "D1 experimental context priority: title support first; hashtag-only candidates seen in >=80% of each observed channel panel ranked lower" if text_scope == "metadata_content" and method == "D1" else None,
        "experimental_context_ranking": text_scope == "metadata_content" and method == "D1",
        "input_observations": len(raw), "eligible_observations": len(observations),
        "excluded_observations": len(exclusions), "exclusion_reasons": dict(sorted(Counter(item["reason"] for item in exclusions).items())),
        "eligible_source_documents": len(all_sources), "eligible_source_videos": len({row["video_id"] for row in observations}),
        "eligible_channels": len({row["channel_id"] for row in observations if row.get("channel_id")}),
        "eligible_by_modality": dict(sorted(Counter(row["modality"] for row in observations).items())),
        "eligible_by_provenance": dict(sorted(Counter(row["provenance"] for row in observations).items())),
        "candidate_count": len(candidates), "candidate_count_before_output_limit": before_limit,
        "phrase_nodes_before_limit": nodes_before_limit, "phrase_nodes_retained": len(index),
        "variant_pairs_compared": comparisons, "variant_pair_limit_reached": pair_limit_reached,
        "ranking_dimensions_descending": rank_fields, "cutoff": end.isoformat(),
        "generated_at": generated_at, "growth_used_for_ranking": False,
        "baseline_end": baseline.isoformat() if baseline else None, "sample_window_source_denominators": denominators,
        "verified_meme_count": None, "independent_gold_count": 0, "recall": None, "precision": None,
        "false_candidate_count": None, "missed_meme_count": None, "family_link_error_count": None,
        "independent_use_validation": "not_performed", "context_semantic_validation": "not_performed",
        "understanding_model_calls": 0, "paid_api_calls": 0, "human_review_minutes": 0,
        "limitations": ["Text only; no video, audio, visual or intonation understanding.",
                        "General phrases, series names, advertisements and copied text can become candidates.",
                        "Distinct sources and surface variants do not establish independent meme use.",
                        "False positives, missed memes and incorrect family links require independent gold annotation.",
                        "D1 surface links are a retrieval prototype; contextual/independent-use verification remains incomplete.",
                        "Sampling representativeness and known-name contamination depend on the supplied collection.",
                        "Finite ngrams and disclosed resource limits can miss expressions and variants."],
    }
    return {"candidates": candidates, "exclusions": exclusions, "evidence": public_evidence,
            "suppressed": suppressed, "summary": summary}


def run(input_path, output_dir, *, db="logs/research.sqlite3", retry_of=None, retry_reason=None, **settings):
    input_path, output_dir = Path(input_path), Path(output_dir)
    if output_dir.exists():
        raise ValueError("Output directory already exists; previous outputs are preserved")
    settings = {"method": "D0", "min_documents": 2, "min_videos": 2, "max_words": 6,
                "max_candidates": 100, "max_phrase_nodes": 3000, "max_variant_pairs": 100000,
                "baseline_end": None, "text_scope": "metadata_content", "boilerplate_min_videos": 3, **settings}
    settings["cutoff"] = timestamp(settings["cutoff"]).isoformat()
    if settings["baseline_end"]:
        settings["baseline_end"] = timestamp(settings["baseline_end"]).isoformat()
    input_hash = hash_file(input_path)
    # Parse before begin only to determine provenance; no candidate processing yet.
    rows = [json.loads(line) for line in input_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if hash_file(input_path) != input_hash:
        raise ValueError("Input changed during loading")
    provenance = {row.get("provenance") for row in rows if isinstance(row, dict)}
    provenance_label = next(iter(provenance)) if len(provenance) == 1 and provenance <= PROVENANCE else "mixed"
    spec = {"objective": "Retrieve unverified YouTube text candidates and provisional variants from immutable observations",
            "stage": "offline_validation", "parameters": {"algorithm": "youtube_candidates_v02", **settings},
            "data_snapshots": [{"id": "youtube_observations", "sha256": input_hash}],
            "code_hashes": {"youtube_candidates.py": hash_file(__file__), "research_log.py": hash_file(ROOT / "src/research_log.py")},
            "prompt_hashes": {}, "config_hashes": {}, "modality": ["text"],
            "provenance": provenance_label, "time_cutoff": settings["cutoff"]}
    log = ResearchLog(db)
    entry = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    try:
        result = detect_candidates(rows, **settings)
        result["summary"]["run_id"] = entry["run_id"]
        result["summary"]["input_sha256"] = input_hash
        output_dir.mkdir(parents=True, exist_ok=False)
        artifacts = []
        for name in ("candidates", "exclusions", "evidence", "suppressed"):
            path = output_dir / f"{name}.jsonl"
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                for row in result[name]:
                    handle.write(canonical(row) + "\n")
            artifacts.append(path)
        summary_path = output_dir / "summary.json"
        with summary_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(result["summary"], ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        artifacts.append(summary_path)
        log.finish(entry["run_id"], status="succeeded",
                   notes=f"Retrieved {len(result['candidates'])} unverified candidates. No meme accuracy, meaning or independent-use validation performed.",
                   artifacts=artifacts, actual_cost={"amount": 0, "currency": "USD", "api_units": 0, "human_minutes": 0})
        return result["summary"]
    except BaseException as exc:
        log.finish(entry["run_id"], status="failed", notes=f"Candidate retrieval failed: {type(exc).__name__}")
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cutoff", required=True, help="ISO timestamp with explicit timezone")
    parser.add_argument("--method", choices=("D0", "D1"), default="D0")
    parser.add_argument("--min-documents", type=int, default=2)
    parser.add_argument("--min-videos", type=int, default=2)
    parser.add_argument("--max-words", type=int, default=6)
    parser.add_argument("--max-candidates", type=int, default=100)
    parser.add_argument("--max-phrase-nodes", type=int, default=3000)
    parser.add_argument("--max-variant-pairs", type=int, default=100000)
    parser.add_argument("--baseline-end")
    parser.add_argument("--text-scope", choices=("metadata_content", "full_text"), default="metadata_content",
                        help="Default: field/line-aware filtering; full_text reproduces the unfiltered baseline")
    parser.add_argument("--boilerplate-min-videos", type=int, default=3)
    parser.add_argument("--db", default="logs/research.sqlite3")
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = vars(parser.parse_args(argv))
    input_path, output_dir = args.pop("input"), args.pop("output_dir")
    try:
        print(json.dumps(run(input_path, output_dir, **args), ensure_ascii=False, indent=2))
    except DuplicateRunError as exc:
        parser.exit(2, f"{exc}\n")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Candidate retrieval error: {exc}\n")


if __name__ == "__main__":
    main()
