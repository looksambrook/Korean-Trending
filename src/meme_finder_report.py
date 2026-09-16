"""Readable, standalone reports of source-backed meme discovery runs.

Rendering adds no examples, semantic claims, popularity scores or confirmed labels.
Titles and other collected text are untrusted. Only HTTPS YouTube source links
become anchors, and the report works without JavaScript or an external server.
"""
from __future__ import annotations

from html import escape
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


KINDS = {
    "sentence_comparison": "비교 문장의 변형", "source_declared_parody": "제작자가 명시한 패러디",
    "expression_reuse": "표현 재사용", "orthographic_wordplay": "표기를 바꾼 말장난",
    "quoted_expression": "인용 표현", "repeated_catchphrase": "반복 구호",
    "rhetorical_catchphrase": "말투·문장 표현", "source_labelled_reuse": "재사용을 명시한 표현",
    "sentence_variant": "문장 변형", "sentence_family": "문장 변형",
    "phrase_variation": "표현 변형", "shared_frame": "문장 변형",
    "character": "캐릭터", "character_family": "캐릭터",
    "parody": "패러디", "parody_family": "패러디",
    "named_reuse": "이름·표현 재사용", "recurring_phrase": "표현 재사용",
    "catchphrase": "유행 표현", "challenge": "챌린지",
    "explicit_reuse": "명시된 재사용", "unknown": "유형 미정",
}
STATUSES = {
    "supported_textual_reuse": "변형·재사용 근거 있음", "needs_evidence": "근거 추가 필요",
    "possible_meme": "밈 후보", "candidate": "밈 후보",
    "supported_candidate": "변형·재사용 근거 있음",
    "supported_meme_family": "변형·재사용 근거 있음",
    "observed_variants": "여러 변형 관찰", "requires_evidence": "근거 추가 필요",
    "unresolved": "판단 보류", "likely_topic": "주제 가능성",
    "rejected": "제외", "insufficient_evidence": "근거 부족",
}
ORIGINS = {
    "name_free_channel_observation": "이름 미지정 채널 관찰",
    "generic_meme_topic_search": "밈 이름 미지정 주제 검색",
    "generic_topic_search": "밈 이름 미지정 주제 검색",
    "observed_phrase_expansion": "발견한 표현의 후속 검색",
    "public_watch_html": "후속 검색 영상의 상세 확인",
    "feed": "이름 미지정 채널 관찰", "public_feed": "이름 미지정 채널 관찰",
    "channel_feed": "이름 미지정 채널 관찰", "youtube_public_atom": "이름 미지정 채널 관찰",
    "channel_feed_no_meme_name": "이름 미지정 채널 관찰",
    "name_free": "이름 미지정 관찰", "topic_query": "밈 이름 미지정 주제 검색",
    "discovered_phrase_expansion": "발견한 표현의 후속 검색",
    "posthoc_search_expansion": "후속 검색 결과의 상세 확인",
    "known_name_lookup": "밈 이름 지정 검색",
    "not_recorded": "발견 경로 미기록", "unknown": "발견 경로 미기록",
}
CONFIDENCE = {"source_grounded_rule_interpretation": "수집 원문과 문장 규칙에 근거한 해석",
              "insufficient": "근거 부족", "low": "낮음", "medium": "중간", "high": "높음",
              "unknown": "미정", "rule_based": "규칙 기반"}
REASONS = {
    "The same observed token recurs in the same positions around different text on distinct source channels.": "서로 다른 채널에서 같은 말을 반복하는 위치는 유지하면서 그 사이의 표현을 바꾼 용례가 관찰되었습니다.",
    "Generic demonstrative media reference; identical words do not identify the same audio, image or referent.": "대상을 막연하게 가리키는 표현입니다. 문구가 같아도 같은 음성·이미지·대상을 뜻하는지는 확인되지 않았습니다.",
    "Actual title and description reuse is examined; video, audio and gestures are not interpreted.": "제목과 설명의 재사용을 살펴봤습니다. 영상·음성·몸짓은 해석하지 않았습니다.",
    "Distinct channels or changed titles do not prove independent footage or authorship.": "채널이나 제목이 다르더라도 독립 촬영·제작으로 확정하지 않습니다.",
    "Recent source use is not a popularity ranking; source counts are not added across platforms.": "최근 사용 사례는 인기 순위가 아닙니다. 출처 수를 플랫폼 간 합산하지 않습니다.",
    "Generic meme-topic search and observed-phrase expansion remain separate from name-free channel observations.": "밈 주제 검색, 발견한 표현의 후속 검색, 이름 미지정 채널 관찰을 구분합니다.",
    "Independent gold evaluation and missed-family recall are not yet available.": "독립 정답 자료에 따른 평가와 놓친 밈 계열의 비율은 아직 확인하지 못했습니다.",
    "Rule-derived explanations are separated from local language-model responses; no model weights are trained.": "규칙에서 얻은 설명과 언어 모델의 응답을 구분합니다. 모델 가중치를 학습한 결과가 아닙니다.",
    "Different argument pairs in the same comparison frame across source channels.": "서로 다른 채널에서 같은 비교 문장의 대상을 바꾼 용례가 관찰되었습니다.",
    "One surface form or one source channel is insufficient for cross-context textual reuse.": "표현이나 채널이 하나뿐이어서 서로 다른 맥락의 재사용 근거가 부족합니다.",
    "Distinct source titles/channels plus explicit reuse wording in the matching source.": "서로 다른 제목과 채널의 자료에 재사용을 가리키는 표현이 명시되어 있습니다.",
    "Literal recurrence alone does not establish a meme family or meaning.": "문구가 반복된 사실만으로 밈 계열이나 의미를 확정할 수 없습니다.",
    "exact_publication_or_observation_time_missing": "정확한 게시 시각 또는 관찰 시각이 없어 근거에서 제외했습니다.",
    "news_or_topic_title_without_reuse_evidence": "뉴스·주제 제목이며 재사용 근거가 없습니다.",
    "no_timestamped_literal_evidence_for_seed": "시각이 확인된 해당 표현의 용례를 확보하지 못했습니다.",
    "no_supported_reusable_form_detected": "현재 탐지 규칙에서 재사용 형식을 찾지 못했습니다. 비밈으로 확정한 것은 아닙니다.",
}


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _h(value) -> str:
    return escape(_text(value), quote=True)


def _md(value) -> str:
    # HTML entities also keep collected <tags> from becoming markdown raw HTML.
    text = escape(_text(value), quote=False).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)


def _items(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _safe_youtube_url(value) -> str | None:
    if not isinstance(value, str) or any(ord(c) <= 32 for c in value) or "\\" in value:
        return None
    try:
        parts = urlsplit(value)
        valid = (parts.scheme == "https" and
                 parts.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"} and
                 parts.username is None and parts.password is None and parts.port in {None, 443})
    except (ValueError, TypeError):
        return None
    return value if valid else None


def _origin(example: dict) -> str:
    value = example.get("discovery_origin", example.get("sampling_origin", example.get("sample_method")))
    if isinstance(value, dict):
        if isinstance(value.get("sampling"), dict):
            origin = value.get("pipeline_origin") or value["sampling"].get("pipeline_origin")
            base = ORIGINS.get(origin, origin) if origin else _origin({"discovery_origin": value["sampling"]})
            if value.get("initial_seed_ids"):
                base += " · 후속 탐색의 최초 근거"
            if value.get("posthoc_expansion_seed_ids"):
                base += " · 발견한 표현으로 추가 수집"
            return base
        mode = value.get("pipeline_origin") or value.get("mode") or value.get("method") or value.get("source_mode")
        label = ORIGINS.get(_text(mode), _text(mode)) or "발견 경로 미기록"
        query = value.get("query")
        return label + (f" · 검색어: {_text(query)}" if query else "")
    return ORIGINS.get(_text(value), _text(value)) or "발견 경로 미기록"


def _source_html(example: dict) -> str:
    title = example.get("title") or example.get("source_title") or "제목 미기록"
    url = _safe_youtube_url(example.get("source_url"))
    if url:
        return f'<a href="{_h(url)}" target="_blank" rel="noopener noreferrer">{_h(title)}</a>'
    source = example.get("source_url")
    return _h(title) + (f'<span class="unlinked">{_h(source)}</span>' if source else "")


def _source_md(example: dict) -> str:
    title = _md(example.get("title") or example.get("source_title") or "제목 미기록")
    url = _safe_youtube_url(example.get("source_url"))
    if url:
        # A percent-encoded target cannot terminate Markdown link syntax.
        url = url.replace("<", "%3C").replace(">", "%3E").replace("(", "%28").replace(")", "%29")
        return f"[{title}]({url})"
    source = example.get("source_url")
    return title + (f" — {_md(source)}" if source else "")


def _knowledge(family: dict) -> dict:
    value = family.get("knowledge")
    return value if isinstance(value, dict) else {}


def _evidence_line(example: dict) -> str:
    channel = example.get("channel_label") or example.get("channel_title") or example.get("channel_id") or "채널 미기록"
    posted = example.get("posted_at") or "미기록"
    available = example.get("available_at") or "미기록"
    return f"{_text(channel)} · 게시 {_text(posted)} · 관찰 {_text(available)}"


def _list_html(values, empty="미기록") -> str:
    values = _items(values)
    return "<ul>" + "".join(f"<li>{_h(v)}</li>" for v in values) + "</ul>" if values else f"<p>{_h(empty)}</p>"


def _usage_text(value) -> str:
    if not isinstance(value, dict):
        return _text(value)
    if value.get("A") is not None and value.get("B") is not None:
        return f"관찰된 비교 대상: ‘{_text(value['A'])}’ ↔ ‘{_text(value['B'])}’"
    if value.get("observed_repeated_token") is not None:
        return f"반복한 말: ‘{_text(value['observed_repeated_token'])}’ · 사이에 넣은 표현: ‘{_text(value.get('observed_middle'))}’"
    if value.get("source_excerpt") is not None:
        field = {"title": "제목", "description": "설명"}.get(value.get("field"), "원문")
        return f"{field}에서 인용: {_text(value['source_excerpt'])}"
    return _text(value)


def _relation_text(relation) -> str:
    if not isinstance(relation, dict):
        return _text(relation)
    basis = relation.get("basis")
    if isinstance(basis, dict):
        if basis.get("observed_repeated_token"):
            changed = relation.get("relation") == "repeated_catchphrase_with_changed_context"
            return (f"‘{_text(basis['observed_repeated_token'])}’라는 말을 반복하는 위치를 공유하며 "
                    f"사이 표현은 ‘{_text(basis.get('left_middle'))}’ / ‘{_text(basis.get('right_middle'))}’입니다. "
                    + ("서로 다른 맥락으로 변형됐습니다." if changed else "같은 표현이며 독립 변형으로 세지 않습니다."))
        if basis.get("shared_modifier"):
            left, right = _items(basis.get("left_arguments")), _items(basis.get("right_arguments"))
            detail = f" · {' ↔ '.join(map(_text, left))} / {' ↔ '.join(map(_text, right))}" if left and right else ""
            return f"공통 수식 ‘{_text(basis['shared_modifier'])}’와 비교 구조를 공유합니다{detail}."
        if basis.get("observed_expression"):
            if relation.get("relation") == "rejected_generic_reference_link":
                return f"‘{_text(basis['observed_expression'])}’라는 문구는 같지만 가리키는 대상의 동일성이 확인되지 않아 계열 연결을 보류합니다."
            duplicate = relation.get("relation") == "duplicate_text_not_independent_variation"
            return f"‘{_text(basis['observed_expression'])}’라는 표현을 공유합니다. " + ("동일한 문구이며 독립 변형으로 세지 않습니다." if duplicate else "표현의 일치는 관찰됐지만 독립 제작 여부는 미확인입니다.")
    return _text(basis or relation.get("relation") or "연결 근거 미기록")


def _family_html(family: dict, number: int) -> str:
    kind = _text(family.get("kind") or "unknown")
    status = _text(family.get("status") or "requires_evidence")
    examples = [x for x in _items(family.get("examples")) if isinstance(x, dict)]
    knowledge = _knowledge(family)
    evidence_ids = set(map(_text, _items(knowledge.get("evidence_ids"))))
    example_numbers = {_text(e.get("observation_id")): i for i, e in enumerate(examples, 1)}
    examples_html = []
    for i, example in enumerate(examples, 1):
        provenance = {"actual_collected": "실제 수집", "researcher_authored": "연구자 작성", "ai_synthetic": "AI 합성"}.get(example.get("provenance"))
        desc = _text(example.get("description"))
        description = f'<details><summary>수집한 설명 발췌</summary><p class="source-text">{_h(desc[:900])}{"…" if len(desc) > 900 else ""}</p></details>' if desc else ""
        used = " · 의미 설명의 근거" if _text(example.get("observation_id")) in evidence_ids else ""
        examples_html.append(f'''<li><div class="source-title">{_source_html(example)}</div>
<p class="meta">{_h(_evidence_line(example))}</p><p class="origin">{_h(_origin(example))}{_h(" · " + provenance if provenance else "")}{_h(used)}</p>{description}</li>''')
    relations = []
    for relation in _items(family.get("relationships")):
        if not isinstance(relation, dict):
            relations.append(_text(relation))
            continue
        left = example_numbers.get(_text(relation.get("left")))
        right = example_numbers.get(_text(relation.get("right")))
        pair = f"용례 {left} ↔ {right}: " if left and right else ""
        relations.append(pair + _relation_text(relation))
    independent = family.get("independent_use_count")
    independent_text = "독립적인 창작·변형 수는 확인하지 못했습니다." if independent is None else f"보고된 독립 사용 수: {_text(independent)}. 판단 근거는 연결 설명을 확인하세요."
    unknowns = _items(knowledge.get("unknowns"))
    search_text = " ".join([_text(family.get("label")), _text(knowledge.get("meaning")), *[_text(e.get("title") or e.get("source_title")) for e in examples]])
    return f'''<article class="family" data-kind="{_h(kind)}" data-status="{_h(status)}" data-search="{_h(search_text.casefold())}">
<div class="badges"><span>{_h(KINDS.get(kind, kind))}</span><span>{_h(STATUSES.get(status, status))}</span></div>
<h2>{number}. {_h(family.get("label") or "이름 미정 후보")}</h2>
<p>{_h(REASONS.get(family.get("reason"), family.get("reason")) or "밈 판단 근거가 아직 기록되지 않았습니다.")}</p>
<p class="latest">관찰된 최신 게시: {_h(family.get("latest_published_at") or "미기록")}</p>
<div class="meaning"><h3>의미와 사용 맥락</h3><p>{_h(knowledge.get("meaning") or "수집 근거만으로 의미를 설명하지 못했습니다.")}</p>
{_list_html([_usage_text(v) for v in _items(knowledge.get("usage"))], "사용 맥락 미기록")}
<p class="meta">자동 구조화·해석 · 사람 검수 여부는 별도 기록 · 해석 확신도: {_h(CONFIDENCE.get(_text(knowledge.get("confidence")), _text(knowledge.get("confidence"))) or "미정")}</p></div>
<h3>실제 용례와 출처</h3><ol class="examples">{"".join(examples_html) if examples_html else '<li>연결된 용례가 없습니다.</li>'}</ol>
<details><summary>연결 근거와 확인 범위</summary>{_list_html(relations, "연결 근거 미기록")}<p>{_h(independent_text)}</p>
<h3>아직 모르는 점</h3>{_list_html(unknowns, "개별 미확인 사항이 기록되지 않았습니다. 검증 완료를 뜻하지 않습니다.")}</details></article>'''


def _row_summary(row) -> str:
    if not isinstance(row, dict):
        return _text(REASONS.get(_text(row), row))
    label = row.get("label") or row.get("phrase") or row.get("query") or row.get("title") or row.get("candidate_id") or row.get("observation_id") or row.get("stage") or "항목"
    if row.get("stage") == label:
        stage = _text(row["stage"])
        label = ("채널 피드 수집" if stage == "feed" else "이름 미지정 주제 검색" if stage.startswith("topic_search") else
                 "발견 표현의 추가 검색" if "search" in stage else "영상 상세 확인" if "metadata" in stage or "watch" in stage else stage)
    reason = row.get("reason") or row.get("selection_reason") or row.get("error") or row.get("status") or row.get("decision")
    reason = {"complete": "완료", "completed": "완료", "succeeded": "완료", "failed": "실패", "skipped": "건너뜀"}.get(_text(reason), reason)
    return _text(label) + (" — " + _text(REASONS.get(_text(reason), reason)) if reason else "")


def _scope(result: dict) -> str:
    scope = result.get("scope")
    return {"YouTube public title and description observations; Korean-first, convenience sample":
            "YouTube 공개 제목·설명 · 한국어 중심 · 접근 가능한 채널과 검색 결과 표본"}.get(_text(scope), _text(scope)) or "미기록"


def markdown_report(result: dict) -> str:
    """Return a source-linked Korean report without adding observations."""
    families = [f for f in _items(result.get("families")) if isinstance(f, dict)]
    lines = ["# 관찰에서 찾은 밈", "", f"작성 시각: {_md(result.get('created_at') or '미기록')}", "",
             f"범위: {_md(_scope(result))}", "",
             "최근 사용된 사례와 표현·캐릭터의 재사용 근거를 보여 줍니다. 검색 순위나 조회 수를 인기 순위로 해석하지 않습니다. 자동 구조화·해석이며, 사람이 검수한 정답과 구분해야 합니다.", ""]
    if not families:
        lines += ["현재 수집 범위에서 보고할 밈 계열을 찾지 못했습니다. 아래 제외·보류 항목과 수집 실패를 확인하세요.", ""]
    for i, family in enumerate(families, 1):
        kind = _text(family.get("kind") or "unknown")
        status = _text(family.get("status") or "requires_evidence")
        knowledge = _knowledge(family)
        lines += [f"## {i}. {_md(family.get('label') or '이름 미정 후보')}", "",
                  f"{_md(KINDS.get(kind, kind))} · {_md(STATUSES.get(status, status))}", "",
                  _md(REASONS.get(family.get("reason"), family.get("reason")) or "밈 판단 근거 미기록"), "",
                  f"관찰된 최신 게시: {_md(family.get('latest_published_at') or '미기록')}", "",
                  "**자동 해석**", "", _md(knowledge.get("meaning") or "수집 근거만으로 의미를 설명하지 못했습니다."), ""]
        if knowledge.get("usage"):
            lines.extend("- " + _md(_usage_text(v)) for v in _items(knowledge["usage"]))
            lines.append("")
        lines += ["**실제 용례**", ""]
        examples = [x for x in _items(family.get("examples")) if isinstance(x, dict)]
        for example in examples:
            lines += [f"- {_source_md(example)} — {_md(_evidence_line(example))}; {_md(_origin(example))}"]
        if not examples:
            lines.append("연결된 용례가 없습니다.")
        lines += ["", "**확인 범위와 미확인 사항**", ""]
        for relation in _items(family.get("relationships")):
            lines.append("- " + _md(_relation_text(relation)))
        lines.extend("- " + _md(v) for v in _items(knowledge.get("unknowns")))
        if family.get("independent_use_count") is None:
            lines.append("- 독립적인 창작·변형 수는 확인하지 못했습니다.")
        lines.append("")
    for key, label in [("rejected", "제외·보류한 항목"), ("seeds", "후속 탐색 대상"), ("failures", "수집·처리 실패"), ("limitations", "전체 확인 범위")]:
        values = _items(result.get(key))
        lines += [f"## {label}", ""]
        lines += ["- " + _md(_row_summary(row)) for row in values] if values else ["기록된 항목 없음"]
        lines.append("")
    lines += ["실행 기록: " + _md(result.get("run_id") or "미기록"), ""]
    return "\n".join(lines)


def render_report(result: dict, output_path: str | Path) -> Path:
    """Write a self-contained HTML report; all cards remain visible without JS."""
    families = [f for f in _items(result.get("families")) if isinstance(f, dict)]
    kinds = sorted({_text(f.get("kind") or "unknown") for f in families})
    statuses = sorted({_text(f.get("status") or "requires_evidence") for f in families})
    options = lambda values, names: "".join(f'<option value="{_h(v)}">{_h(names.get(v, v))}</option>' for v in values)
    cards = "\n".join(_family_html(f, i) for i, f in enumerate(families, 1))
    if not cards:
        cards = '<div class="empty"><h2>아직 보고할 밈 계열이 없습니다</h2><p>현재 수집 범위에서 근거를 갖춘 계열을 찾지 못했습니다. 아래 제외·보류 항목과 수집 실패를 확인하세요.</p></div>'
    supplemental = []
    for key, label in [("rejected", "제외·보류한 항목"), ("seeds", "후속 탐색 대상"), ("failures", "수집·처리 실패"), ("attempts", "탐색 시도"), ("limitations", "전체 확인 범위")]:
        values = _items(result.get(key))
        supplemental.append(f'<details class="supplement"{" open" if key == "failures" and values else ""}><summary>{label} · {len(values)}</summary>{_list_html([_row_summary(x) for x in values], "기록된 항목 없음")}</details>')
    budget = result.get("budget")
    budget_html = f'<details class="supplement"><summary>수집 범위와 비용 기록</summary><pre>{_h(json.dumps(budget, ensure_ascii=False, indent=2, default=str))}</pre></details>' if budget else ""
    document = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>관찰에서 찾은 밈</title><style>
:root{color-scheme:light;--ink:#18283a;--muted:#53657b;--line:#dce4ed;--blue:#175cc3;--paper:#fff;--bg:#f3f6fa}*{box-sizing:border-box}body{font-family:system-ui,"Malgun Gothic",sans-serif;color:var(--ink);background:var(--bg);margin:0;line-height:1.7}main{max-width:1120px;margin:auto;padding:36px 22px 70px}header{margin-bottom:26px}h1{font-size:2.1rem;line-height:1.25;margin:0 0 14px}h2{font-size:1.4rem;line-height:1.45;margin:12px 0}h3{font-size:1rem;margin:15px 0 6px}p{margin:8px 0}.lead{max-width:850px}.meta,.origin,.latest{color:var(--muted);font-size:.86rem}.filters{display:flex;flex-wrap:wrap;gap:12px;align-items:end;background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:16px;margin:22px 0}.filters label{display:flex;flex-direction:column;gap:4px;font-size:.85rem}.filters input,.filters select{font:inherit;border:1px solid #bdcada;border-radius:6px;padding:8px;min-width:145px}.filters input{min-width:230px}#visible-count{margin:0 0 7px auto;font-size:.9rem;color:var(--muted)}.family,.empty{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:24px;margin-bottom:20px}.badges{display:flex;flex-wrap:wrap;gap:8px}.badges span{font-size:.78rem;color:#174879;background:#eaf3fe;padding:3px 9px;border-radius:20px}.meaning{background:#f5f8fc;border-left:3px solid #4f82c4;padding:4px 16px 12px;margin:16px 0}.examples{padding-left:25px}.examples>li{padding:10px 0;border-bottom:1px solid var(--line)}.source-title{font-weight:650;overflow-wrap:anywhere}a{color:var(--blue);text-underline-offset:3px}a:hover{text-decoration-thickness:2px}.source-text{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.9rem}.unlinked{display:block;font-size:.8rem;font-weight:400;overflow-wrap:anywhere}details{margin-top:14px}summary{cursor:pointer;font-weight:600}li{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8rem}.supplement{padding:14px 18px;border:1px solid var(--line);border-radius:10px;background:white}.empty{border-style:dashed}.fine{font-size:.8rem;color:var(--muted);margin-top:24px;overflow-wrap:anywhere}[hidden]{display:none!important}@media(max-width:600px){main{padding:24px 14px}h1{font-size:1.65rem}.family{padding:18px}.filters label,.filters input,.filters select{width:100%}#visible-count{margin:0}}@media print{body{background:white}.filters{display:none}.family{break-inside:avoid}details> :not(summary){display:block}.family[hidden]{display:block!important}}
</style></head><body><main>'''
    document += f'''<header><h1>관찰에서 찾은 밈</h1><p class="lead">표현의 변형, 캐릭터의 재사용, 패러디 근거를 출처와 함께 살펴봅니다. 최근 게시 사례가 있다는 사실만으로 현재의 인기 순위를 뜻하지는 않습니다.</p>
<p class="meta">작성 {_h(result.get("created_at") or "미기록")} · 범위 {_h(_scope(result))}</p>
<p class="meta">자동 구조화·해석 결과입니다. 실제 수집 자료, 합성 자료, 사람의 검수 결과를 구분해서 읽어 주세요.</p></header>
<div class="filters"><label>표현·용례 찾기<input id="query" type="search" placeholder="제목이나 표현 입력"></label><label>유형<select id="kind"><option value="">모든 유형</option>{options(kinds, KINDS)}</select></label><label>근거 상태<select id="status"><option value="">모든 상태</option>{options(statuses, STATUSES)}</select></label><p id="visible-count" role="status" aria-live="polite">{len(families)}개 계열</p></div>
<noscript><p>자바스크립트가 꺼져 있어 전체 결과를 표시합니다. 출처와 확인 범위는 모두 읽을 수 있습니다.</p></noscript>
<section id="families" aria-label="발견된 계열">{cards}</section><p id="filter-empty" class="empty" hidden>필터에 맞는 계열이 없습니다.</p>
{"".join(supplemental)}{budget_html}
<p class="fine">실행 기록: {_h(result.get("run_id") or "미기록")}<br>조회·반응 수를 플랫폼 간 합산하지 않습니다. 같은 채널이나 서로 다른 채널의 게시만으로 재게시와 독립 창작을 구분하지 않습니다.</p>'''
    document += '''</main><script>
(()=>{"use strict";const cards=[...document.querySelectorAll(".family")],q=document.getElementById("query"),kind=document.getElementById("kind"),status=document.getElementById("status");function filter(){const needle=q.value.toLocaleLowerCase().trim();let n=0;cards.forEach(card=>{const show=(!needle||card.dataset.search.includes(needle))&&(!kind.value||card.dataset.kind===kind.value)&&(!status.value||card.dataset.status===status.value);card.hidden=!show;if(show)n++;});document.getElementById("visible-count").textContent=n+" / "+cards.length+"개 계열";document.getElementById("filter-empty").hidden=n>0||cards.length===0;}[q,kind,status].forEach(el=>el.addEventListener("input",filter));})();
</script></body></html>'''
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return output
