"""Deterministic discovery of reusable expressions from observed metadata.

No known meme names, gold labels, or researcher-written interpretations enter
this module. It proposes exact observed search spans, prioritises distinct
contextual uses, and turns collected titles into inspectable evidence families.
Textual reuse is evidence, not proof of independent authorship or popularity.
The caller is responsible for applying its observation-time cutoff before use.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import re
import unicodedata


VIDEO = re.compile(r"[A-Za-z0-9_-]{11}\Z")
HASHTAGS = re.compile(r"(?<!\w)#[\w가-힣]+")
DECORATION = re.compile(r"\[[^\]]{0,80}\]|【[^】]{0,80}】")
QUOTED = re.compile(r'["“「‘\']([^"”」’\'\n]{4,70})["”」’\']')
REUSE_CUE = re.compile(r"패러디|밈|따라\s*(?:해|하|했)|인용|모사|parody|\bmeme\b|\bremix\b", re.I)
PARODY = re.compile(r"패러디|parody", re.I)
NEWS = re.compile(r"\[?(?:속보|단독|뉴스|긴급속보)\]?|금리\s*(?:인상|인하)|증시\s*(?:마감|개장)|(?:정부|대통령|장관).{0,15}(?:발표|회의|브리핑)|사망자\s*\d", re.I)
BOILERPLATE = re.compile(r"^(?:예고|선공개|미방분|스페셜\s*클립|공식|뉴스|속보|shorts?|ep\.?\s*\d+|\d+화)$", re.I)
GENERIC_REFERENCE = re.compile(r"^(?:그|이|저|이런|그런|저런|어떤)\s*(?:효과음|음악|소리|노래|장면|영상|사진|짤|캐릭터|춤|밈)$")
COMPARISON = re.compile(
    r"^(?P<subject>.+)(?:은|는|이|가)\s+(?P<object>.+?)(?:과|와)\s+"
    r"(?P<verb>구분|구별)(?:할|될|하기)?\s*수(?:가)?\s*없(?:다|음|어|습니다|네|죠)?[.!?…]*$"
)
MODIFIER_END = re.compile(r"(?:한|하는|된|되는|진|지는|운|우는|었던|였던|같은|없는|있는)$")


def _normal(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(value or ""))).strip().casefold()


def _id(prefix, value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return prefix + hashlib.sha256(data).hexdigest()[:20]


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def _positive_parody(text):
    for line in text.splitlines():
        if PARODY.search(line) and not re.search(r"패러디(?:가|는|를)?\s*(?:아니|아닙|아님)|not\s+(?:a\s+)?parody", line, re.I):
            return True
    return False


def _fields(row):
    context = row.get("context") or {}
    title = context.get("title", context.get("video_title", row.get("title", "")))
    description = context.get("description", context.get("video_description", context.get("description_snippet",
                  row.get("description", row.get("description_snippet", "")))))
    if not title:
        title = str(row.get("text", "")).split("\n", 1)[0]
    return str(title or ""), str(description or "")


def _core(title):
    title = unicodedata.normalize("NFC", title)
    title = re.split(r"\s[|｜]\s", title, maxsplit=1)[0]
    return re.sub(r"\s+", " ", HASHTAGS.sub(" ", DECORATION.sub(" ", title))).strip()


def _comparison(title):
    core = _core(title)
    match = COMPARISON.match(core)
    if not match:
        return None
    subject, target = match["subject"].strip(), match["object"].strip()
    words = subject.split()
    # A modifier must precede the observed subject, so an ordinary A/B factual
    # comparison alone does not become a reusable rhetorical frame.
    modifiers = [i for i, word in enumerate(words[:-1]) if MODIFIER_END.search(word)]
    if not modifiers:
        return None
    split = modifiers[-1] + 1
    prefix, left = " ".join(words[:split]), " ".join(words[split:])
    if not left or not target or len(prefix) < 4:
        return None
    return {"prefix": prefix, "left": left, "right": target,
            "frame_key": _normal(prefix) + "|indistinguishable",
            "observed_predicate": match["verb"],
            "frame": prefix + " {A}는 {B}와 구분/구별할 수 없다"}


def _repeated_frame(title, query):
    """An observed token repeats twice, then surrounds variable material."""
    words = re.findall(r"[가-힣A-Za-z]+", query)
    if len(words) != 2 or words[0] != words[1] or len(words[0]) < 3:
        return None
    token = words[0]
    pattern = re.compile(re.escape(token) + r"[\s,]+" + re.escape(token)
                         + r"[\s,]+(?P<middle>.{1,60}?)\s+" + re.escape(token) + r"(?=\W|_|$)")
    match = pattern.search(_core(title))
    if not match:
        return None
    middle = match["middle"].strip(" ,.!?_")
    if not middle or len(middle.split()) > 10:
        return None
    return {"repeated_token": token, "observed_middle": middle,
            "frame": token + " " + token + " {observed_middle} " + token}


def _origin(row):
    sample = row.get("sample_method") or {}
    result = {"mode": sample} if isinstance(sample, str) else dict(sample)
    if row.get("discovery_origin"):
        result["pipeline_origin"] = row["discovery_origin"]
    return result


def _unique_rows(rows):
    """Merge collection snapshots of one video without counting them twice."""
    best, origins, snapshots = {}, defaultdict(list), defaultdict(list)
    for row in rows:
        vid = row.get("video_id")
        if not vid:
            continue
        origin = row.get("discovery_origin")
        if origin and origin not in origins[vid]:
            origins[vid].append(origin)
        snapshots[vid].append({"observation_id": row.get("observation_id"),
                               "available_at": row.get("available_at"), "discovery_origin": origin,
                               "sampling": row.get("sample_method")})
        old = best.get(vid)
        def score_of(item):
            return (bool(_time(item.get("posted_at"))),
                    _time(item.get("available_at")) or datetime.min.replace(tzinfo=timezone.utc),
                    bool(item.get("channel_id")), len(_fields(item)[1]))
        score = score_of(row)
        old_score = score_of(old) if old else None
        if old is None or score > old_score:
            best[vid] = row
    return [{**row, "_pipeline_discovery_origins": origins[vid], "_collection_snapshots": snapshots[vid]}
            for vid, row in best.items()]


def _exact_source_span(candidate, title, description):
    """Queries must literally occur in a collected origin text after NFC/casefold."""
    query = unicodedata.normalize("NFC", candidate).strip()
    if query and any(query.casefold() in unicodedata.normalize("NFC", text).casefold()
                     for text in (title, description)):
        return query
    return None


def plan_seeds(rows, max_seeds=4):
    """Select observed expressions automatically, including singleton forms.

    Scores are heuristics for follow-up allocation, never meme probabilities.
    No recency or engagement popularity claims are made from these scores.
    """
    if type(max_seeds) is not int or max_seeds < 0:
        raise ValueError("max_seeds must be a nonnegative integer")
    proposals = []
    for row in _unique_rows(rows):
        title, description = _fields(row)
        core = _core(title)
        if NEWS.search(title):
            continue
        form = _comparison(title)
        candidates = []
        if form:
            candidates.append((form["prefix"], "sentence_comparison", 12,
                               "A modifier plus an indistinguishability comparison offers a testable reusable sentence form.", form))
        words = list(re.finditer(r"[가-힣A-Za-z0-9]+", core))
        # Orthographic echo discovers potential parody names without knowing
        # either the base name or its altered version beforehand.
        for left, right in zip(words, words[1:]):
            a, b = left.group(), right.group()
            similarity = SequenceMatcher(None, a.casefold(), b.casefold()).ratio()
            if 3 <= len(a) <= 16 and 3 <= len(b) <= 16 and a != b and .70 <= similarity < 1:
                candidates.append((b, "orthographic_wordplay", 8,
                    "Adjacent similar but unequal spellings may mark a parody name; collected uses must establish the relation.",
                    {"observed_pair": [a, b]}))
        # Repeated speech-like tokens are search leads, not automatic memes.
        for left, right in zip(words, words[1:]):
            if left.group() == right.group() and len(left.group()) >= 3:
                span = core[left.start():right.end()]
                candidates.append((span, "repeated_catchphrase", 7,
                    "An exact repeated phrase in a title can be followed into distinct contexts.", {}))
        for quote in QUOTED.finditer(title):
            span = quote.group(1).strip()
            if len(span.split()) >= 2 and not BOILERPLATE.match(span):
                candidates.append((span, "quoted_expression", 6 + bool(REUSE_CUE.search(title)),
                                   "An explicitly quoted expression is a candidate, pending contextual reuse evidence.", {}))
        if core.count(",") >= 2 and 8 <= len(core) <= 65 and re.search(r"(?:하다|이다|야|해|다)[.!?…☆]*$", core):
            candidates.append((core.rstrip(".☆ "), "rhetorical_catchphrase", 6,
                               "A compact enumerated sentence may be a reusable quotation; frequency alone will not establish that.", {}))
        if REUSE_CUE.search(core) and 3 <= len(core) <= 55 and not BOILERPLATE.match(core):
            candidates.append((core, "source_labelled_reuse", 5,
                               "The observed title itself mentions a reuse convention; this is a source claim to investigate.", {}))
        for candidate, kind, score, reason, grammar in candidates:
            query = _exact_source_span(candidate, title, description)
            if not query or GENERIC_REFERENCE.fullmatch(_normal(query)):
                continue
            proposals.append({"seed_id": _id("mseed_", [_normal(query), kind]), "query": query,
                "source_observation_ids": [row.get("observation_id")], "source_video_ids": [row["video_id"]],
                "source_channel_ids": [row.get("channel_id")] if row.get("channel_id") else [],
                "source_titles": [title], "reason": reason, "kind": kind, "score": score,
                "grammar": grammar, "selection_method": "automatic_observed_surface_heuristics",
                "discovery_origin": _origin(row), "query_is_exact_observed_span": True})
    combined = {}
    for seed in sorted(proposals, key=lambda s: (-s["score"], s["seed_id"])):
        key = _normal(seed["query"])
        if key not in combined:
            combined[key] = seed
        else:
            for field in ("source_observation_ids", "source_video_ids", "source_channel_ids", "source_titles"):
                combined[key][field] = list(dict.fromkeys(combined[key][field] + seed[field]))
    return list(combined.values())[:max_seeds]


def select_followups(seed, search_rows, limit=3):
    """Choose relevant, varied videos for exact watch metadata collection."""
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a nonnegative integer")
    sources = set(seed.get("source_video_ids", []))
    channels = set(seed.get("source_channel_ids", []))
    frame_key = seed.get("grammar", {}).get("frame_key")
    pool = []
    for row in _unique_rows(search_rows):
        vid = row.get("video_id", "")
        if not VIDEO.fullmatch(vid) or vid in sources:
            continue
        title, description = _fields(row)
        parsed = _comparison(title)
        same_frame = bool(frame_key and parsed and parsed["frame_key"] == frame_key)
        literal = _normal(seed["query"]) in _normal(title + "\n" + description)
        if not same_frame and not literal:
            continue
        if NEWS.search(title) and not REUSE_CUE.search(title):
            continue
        score = (20 * same_frame + 4 * literal + 5 * bool(REUSE_CUE.search(title + "\n" + description))
                 + min(len(description) / 50, 2) + 2 * bool(row.get("channel_id")))
        pool.append({"row": row, "score": score, "title_key": _normal(_core(title)),
                     "slots": (parsed["left"], parsed["right"]) if parsed else None})
    selected, seen_titles, seen_slots = [], set(), set()
    while pool and len(selected) < limit:
        def priority(item):
            row = item["row"]
            return (item["score"] + 5 * bool(row.get("channel_id") and row["channel_id"] not in channels)
                    + 3 * (item["title_key"] not in seen_titles)
                    + 2 * bool(item["slots"] and item["slots"] not in seen_slots),
                    -int(row.get("search_rank") or 100000), row["video_id"])
        item = max(pool, key=priority)
        pool.remove(item)
        row = item["row"]
        if item["title_key"] in seen_titles:
            continue
        selected.append(row["video_id"])
        channels.add(row.get("channel_id"))
        seen_titles.add(item["title_key"])
        if item["slots"]:
            seen_slots.add(item["slots"])
    return selected


def _associations(value):
    if isinstance(value, dict):
        return {str(k): set(v) for k, v in value.items()}
    result = defaultdict(set)
    for entry in value or []:
        if isinstance(entry, dict):
            result[entry.get("seed_id", "")].update(entry.get("video_ids", []))
            if entry.get("video_id"):
                result[entry["seed_id"]].add(entry["video_id"])
    return result


def _example(row, seeds, associations):
    title, description = _fields(row)
    selected_by = [s["seed_id"] for s in seeds if row["video_id"] in associations.get(s["seed_id"], set())]
    initial = [s["seed_id"] for s in seeds if row["video_id"] in s.get("source_video_ids", [])]
    return {"observation_id": row.get("observation_id"), "video_id": row["video_id"], "title": title,
            "description": description, "channel_id": row.get("channel_id"),
            "channel_label": (row.get("context") or {}).get("channel_label"),
            "source_url": row.get("source_url"), "posted_at": row.get("posted_at"),
            "available_at": row.get("available_at"), "provenance": row.get("provenance", "not_recorded"),
            "discovery_origin": {"sampling": _origin(row), "initial_seed_ids": initial,
                                 "pipeline_origin": row.get("discovery_origin"),
                                 "observed_pipeline_origins": row.get("_pipeline_discovery_origins", []),
                                 "posthoc_expansion_seed_ids": selected_by},
            "collection_snapshots": row.get("_collection_snapshots", []),
            "original_or_repost": row.get("original_or_repost", "unknown")}


def _family(label, kind, examples, relationships, meaning, usage, *, supported, basis, timestamp):
    status = "supported_textual_reuse" if supported else "needs_evidence"
    return {"family_id": _id("mfamily_", [kind, _normal(label)]), "label": label, "kind": kind,
            "status": status, "reason": basis, "examples": examples,
            "surface_forms": list(dict.fromkeys(ex["title"] for ex in examples)), "relationships": relationships,
            "knowledge": {"meaning": meaning, "usage": usage,
                "confidence": "source_grounded_rule_interpretation" if supported else "insufficient",
                "evidence_ids": [ex["observation_id"] for ex in examples],
                "unknowns": ["원출처", "독립 제작 여부", "영상·음성의 실제 내용", "현재 확산 규모", "모든 맥락에서의 의미"],
                "method": "deterministic_surface_grammar_and_source_cues", "model_weights_trained": False,
                "available_at": timestamp},
            "latest_published_at": max((ex["posted_at"] for ex in examples if _time(ex["posted_at"])), key=_time, default=None),
            "distinct_video_count": len({ex["video_id"] for ex in examples}),
            "distinct_channel_count": len({ex["channel_id"] for ex in examples if ex["channel_id"]}),
            "independent_use_count": None, "independence_status": "unknown",
            "review_mode": "automatic", "human_review_minutes": 0,
            "created_at": timestamp, "popularity_rank": None}


def build_families(rows, seeds, search_associations=None):
    """Build typed evidence families without forcing all memes into slots.

    Missing exact publication/availability timestamps cannot support a family.
    Search cards may guide follow-up selection; watch/feed records provide the
    timestamped evidence. The caller supplies its selected time slice.
    """
    seeds = list(seeds)
    associations = _associations(search_associations)
    timestamp = datetime.now(timezone.utc).isoformat()
    rejected, eligible = [], []
    for row in _unique_rows(rows):
        if not _time(row.get("posted_at")) or not _time(row.get("available_at")):
            rejected.append({"video_id": row["video_id"], "observation_id": row.get("observation_id"),
                             "reason": "exact_publication_or_observation_time_missing"})
        elif NEWS.search(_fields(row)[0]) and not REUSE_CUE.search(_fields(row)[0]):
            rejected.append({"video_id": row["video_id"], "observation_id": row.get("observation_id"),
                             "reason": "news_or_topic_title_without_reuse_evidence"})
        else:
            eligible.append(row)
    groups = defaultdict(list)
    for row in eligible:
        parsed = _comparison(_fields(row)[0])
        if parsed:
            groups[parsed["frame_key"]].append((row, parsed))
    families, assigned = [], set()
    for group in groups.values():
        exemplar = group[0][1]
        examples = [_example(row, seeds, associations) for row, _ in group]
        relationships, varied = [], set()
        for i, (left, a) in enumerate(group):
            for right, b in group[i + 1:]:
                if (a["left"], a["right"]) == (b["left"], b["right"]):
                    relation = "duplicate_text_not_independent_variation"
                else:
                    relation = "shared_comparison_frame_with_changed_subjects"
                    varied.update([left["video_id"], right["video_id"]])
                relationships.append({"left": left.get("observation_id"), "right": right.get("observation_id"),
                    "relation": relation, "basis": {"shared_modifier": exemplar["prefix"],
                    "left_arguments": [a["left"], a["right"]], "right_arguments": [b["left"], b["right"]],
                    "observed_predicates": [a["observed_predicate"], b["observed_predicate"]],
                    "comparison_verb_normalization": "구분/구별 are generic lexical equivalents here"}})
        channels = {ex["channel_id"] for ex in examples if ex["channel_id"]}
        supported = len(varied) >= 2 and len(channels) >= 2
        meaning = (f"제목에서 ‘{exemplar['prefix']}’이라는 수식 뒤의 대상 A를 B와 구분하기 어렵다고 비교한다. "
                   "대상만 바꾼 재사용이 관찰된다." if supported else
                   "구분하기 어렵다는 비교 문장은 관찰되지만, 서로 다른 맥락의 변형 근거가 부족하다.")
        usage = [{"source_observation_id": row.get("observation_id"), "A": parsed["left"], "B": parsed["right"],
                  "observed_title": _fields(row)[0]} for row, parsed in group]
        families.append(_family(exemplar["frame"], "sentence_comparison", examples, relationships, meaning, usage,
            supported=supported, timestamp=timestamp,
            basis="Different argument pairs in the same comparison frame across source channels." if supported else
                  "One surface form or one source channel is insufficient for cross-context textual reuse."))
        assigned.update(row["video_id"] for row, _ in group)
    for seed in seeds:
        if seed["kind"] == "sentence_comparison":
            continue
        query = _normal(seed["query"])
        matches = [row for row in eligible if query in _normal("\n".join(_fields(row)))]
        if not matches:
            rejected.append({"seed_id": seed["seed_id"], "query": seed["query"],
                             "reason": "no_timestamped_literal_evidence_for_seed"})
            continue
        examples = [_example(row, seeds, associations) for row in matches]
        repeated = [(row, _repeated_frame(_fields(row)[0], seed["query"])) for row in matches]
        repeated = [(row, parsed) for row, parsed in repeated if parsed]
        middles = {_normal(parsed["observed_middle"]) for _, parsed in repeated}
        repeated_channels = {row["channel_id"] for row, _ in repeated if row.get("channel_id")}
        if seed["kind"] == "repeated_catchphrase" and len(middles) >= 2 and len(repeated_channels) >= 2:
            relationships = []
            for i, (left, a) in enumerate(repeated):
                for right, b in repeated[i + 1:]:
                    changed = _normal(a["observed_middle"]) != _normal(b["observed_middle"])
                    relationships.append({"left": left.get("observation_id"), "right": right.get("observation_id"),
                        "relation": "repeated_catchphrase_with_changed_context" if changed else "duplicate_text_not_independent_variation",
                        "basis": {"observed_repeated_token": a["repeated_token"],
                                  "left_middle": a["observed_middle"], "right_middle": b["observed_middle"]}})
            token = repeated[0][1]["repeated_token"]
            families.append(_family(seed["query"], "repeated_catchphrase",
                [_example(row, seeds, associations) for row, _ in repeated], relationships,
                f"‘{token}’를 앞에서 두 번, 뒤에서 한 번 반복하고 그 사이 내용을 바꾸는 제목 표현이다. 서로 다른 맥락의 변형이 관찰된다.",
                [{"source_observation_id": row.get("observation_id"), "observed_repeated_token": token,
                  "observed_middle": parsed["observed_middle"], "observed_title": _fields(row)[0]} for row, parsed in repeated],
                supported=True, timestamp=timestamp,
                basis="The same observed token recurs in the same positions around different text on distinct source channels."))
            assigned.update(row["video_id"] for row, _ in repeated)
            continue
        channels = {ex["channel_id"] for ex in examples if ex["channel_id"]}
        distinct_titles = {_normal(_core(ex["title"])) for ex in examples}
        parody_rows = [row for row in matches if _positive_parody("\n".join(_fields(row)))]
        cue_rows = [row for row in matches if REUSE_CUE.search("\n".join(_fields(row)))]
        generic_reference = bool(GENERIC_REFERENCE.fullmatch(query))
        supported = len(distinct_titles) >= 2 and len(channels) >= 2 and bool(cue_rows) and not generic_reference
        kind = "source_declared_parody" if parody_rows else "expression_reuse"
        relationships = []
        for i, left in enumerate(matches):
            for right in matches[i + 1:]:
                same = _normal(_core(_fields(left)[0])) == _normal(_core(_fields(right)[0]))
                relationships.append({"left": left.get("observation_id"), "right": right.get("observation_id"),
                    "relation": "rejected_generic_reference_link" if generic_reference else
                                "duplicate_text_not_independent_variation" if same else "literal_expression_in_different_titles",
                    "basis": {"observed_expression": seed["query"], "semantic_relation": "source_cues_only"}})
        usage = []
        for row in cue_rows:
            for field, text in zip(("title", "description"), _fields(row)):
                for line in text.splitlines():
                    if REUSE_CUE.search(line):
                        usage.append({"source_observation_id": row.get("observation_id"), "field": field,
                                      "source_excerpt": line, "relation_to_expression": "same_video_source_claim"})
        if generic_reference:
            meaning = f"‘{seed['query']}’는 가리키는 대상이 생략된 일반 표현이다. 같은 음원·장면·밈을 가리키는지 확인되지 않아 하나의 계열로 연결하지 않았다."
            rejected.append({"seed_id": seed["seed_id"], "query": seed["query"],
                             "reason": "generic_deictic_media_reference_shared_referent_unknown",
                             "rejected_relation_count": len(relationships)})
        elif parody_rows:
            meaning = f"‘{seed['query']}’가 등장하는 자료의 제작자 설명에 패러디가 명시되어 있다. 대상과 의도는 원문 용례를 함께 확인해야 한다."
        elif supported:
            meaning = f"‘{seed['query']}’라는 표현이 서로 다른 제목에서 재사용되고, 해당 자료에 밈·모방·인용 관련 표지가 있다. 세부 의미는 자동 확정하지 않았다."
        else:
            meaning = f"‘{seed['query']}’라는 표현은 관찰되지만 반복만으로 밈의 의미나 계열을 확정할 수 없다."
        families.append(_family(seed["query"], kind, examples, relationships, meaning, usage,
            supported=supported, timestamp=timestamp,
            basis="Generic demonstrative media reference; identical words do not identify the same audio, image or referent." if generic_reference else
                  "Distinct source titles/channels plus explicit reuse wording in the matching source." if supported else
                  "Literal recurrence alone does not establish a meme family or meaning."))
        assigned.update(row["video_id"] for row in matches)
    for row in eligible:
        if row["video_id"] not in assigned:
            rejected.append({"video_id": row["video_id"], "observation_id": row.get("observation_id"),
                             "title": _fields(row)[0], "reason": "no_supported_reusable_form_detected",
                             "is_non_meme": None})
    families.sort(key=lambda family: (family["status"] == "supported_textual_reuse",
                                      family["distinct_channel_count"], family["distinct_video_count"]), reverse=True)
    return {"families": families, "rejected": rejected,
            "summary": {"algorithm": "observed_surface_discovery_v2", "seed_count": len(seeds),
                        "supported_textual_reuse_count": sum(f["status"] == "supported_textual_reuse" for f in families),
                        "needs_evidence_count": sum(f["status"] == "needs_evidence" for f in families),
                        "independent_use_count": None, "human_review_minutes": 0, "created_at": timestamp,
                        "limitation": "Text-only deterministic discovery; not prevalence, gold accuracy, visual/audio understanding or model training."}}
