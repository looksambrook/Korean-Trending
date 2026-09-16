"""Static actual-output gallery and condition-masked local human rating pack.

No ratings, quality scores, independent gold, or model results are invented.
The pack copies only supplied local content; its condition mapping stays outside
human_pack. The caller owns experiment registration and evaluator recruitment.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from html import escape
import hashlib
import json
from pathlib import Path
import secrets
import shutil

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = ("C0", "CR", "CF")
CONDITION_NAMES = {"C0": "기억 추가 없음", "CR": "RAG / 문맥", "CF": "미세조정 조건"}
METRICS = (("request_fit", "요청·맥락 적합성"), ("meme_fidelity", "밈 특징 보존"), ("content_quality", "표현·완성도"))


def _h(value):
    return escape("" if value is None else str(value), quote=True)


def _read(value):
    if isinstance(value, (str, Path)):
        path = Path(value).resolve()
        return json.loads(path.read_text(encoding="utf-8-sig")), path.parent
    if not isinstance(value, dict):
        raise ValueError("report inputs must be dictionaries or JSON paths")
    return value, ROOT


def _sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _local(value, base):
    if not isinstance(value, (str, Path)) or not str(value):
        return None
    path = Path(value)
    path = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        return None
    return path


def _copy_asset(source, destination, stem):
    suffix = source.suffix.lower()
    target = destination / (stem + suffix)
    digest = _sha(source)
    shutil.copyfile(source, target)
    if _sha(target) != digest or _sha(source) != digest:
        raise ValueError("source asset changed while copying")
    return target.name, digest


def _case_rows(spec, renders):
    rows = spec.get("cases") or spec.get("evaluation_requests") or []
    result, seen = [], set()
    for row in rows:
        if isinstance(row, dict) and row.get("case_id") and row["case_id"] not in seen:
            result.append(row)
            seen.add(row["case_id"])
    for row in renders:
        if row.get("case_id") and row["case_id"] not in seen:
            result.append({"case_id": row["case_id"], "brief": row.get("brief", "요청 원문 미제공"), "unplanned_reported_case": True})
            seen.add(row["case_id"])
    return result


def _content(body, media, *, prefix):
    content = f'<pre class="body-text">{_h(body)}</pre>' if body else '<p class="missing">본문 출력 없음</p>'
    if media.get("image"):
        content += f'<img class="output-image" loading="lazy" src="{_h(prefix + media["image"])}" alt="제공된 제작 이미지">'
    else:
        content += '<p class="missing">이미지 파일 없음</p>'
    if media.get("video"):
        content += f'<video controls preload="metadata" src="{_h(prefix + media["video"])}" aria-label="제공된 제작 영상"></video>'
    else:
        content += '<p class="missing">영상 파일 없음</p>'
    return content


STYLE = '''
:root{color-scheme:light;font-family:system-ui,"Malgun Gothic",sans-serif;color:#172536;background:#f3f6fa}*{box-sizing:border-box}body{margin:0;line-height:1.65}main{max-width:1440px;margin:auto;padding:28px 22px 70px}h1{font-size:1.9rem;line-height:1.3;margin:0 0 12px}h2{font-size:1.3rem;margin:0 0 10px}h3{font-size:1.05rem;margin:0 0 10px}p{margin:8px 0}.muted{color:#58677b;font-size:.88rem}.notice{background:#eaf1fa;border-left:4px solid #427bbd;padding:12px 16px;margin:18px 0}.stats{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0}.stat{border:1px solid #dce3ee;border-radius:10px;background:white;padding:12px 16px;min-width:150px}.stat strong{display:block;font-size:1.3rem}.case{margin:28px 0}.case-head{margin-bottom:12px}.comparison{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.card,.rating-card{background:white;border:1px solid #dce3ee;border-radius:12px;padding:18px;overflow:hidden}.card.failed{border-color:#d8b9b9}.body-text{font:inherit;white-space:pre-wrap;word-break:break-word;background:#f8fafc;border-radius:7px;padding:12px}.output-image,video{width:100%;display:block;border-radius:7px;margin:12px 0}video{max-height:450px;background:#121925}.missing{padding:10px;border:1px dashed #bec9d6;border-radius:7px;color:#657286;font-size:.88rem}.badge{display:inline-block;border-radius:14px;background:#eef3f8;padding:2px 9px;font-size:.78rem}.sources{background:white;border:1px solid #dce3ee;border-radius:10px;padding:16px;margin:18px 0}.sources pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;font-size:.88rem}.source-image{max-width:440px;max-height:300px;display:block}.rating-card{max-width:950px;margin:22px auto}.ratings{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0}label{display:block;font-size:.92rem}select,input,textarea,button{font:inherit}select,input,textarea{border:1px solid #acb9ca;border-radius:5px;padding:8px;background:white;color:#172536;max-width:100%}select{width:100%;margin-top:5px}textarea{width:100%;min-height:75px;margin-top:5px;resize:vertical}button,.button{display:inline-block;border:0;border-radius:7px;padding:11px 16px;background:#1c599e;color:white;cursor:pointer;text-decoration:none}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #70a7f1;outline-offset:2px}.toolbar{position:sticky;top:0;z-index:2;background:#f3f6faf5;border-bottom:1px solid #dce3ee;padding:12px 0;display:flex;align-items:center;flex-wrap:wrap;gap:16px}.toolbar p{margin:0}.rater{display:flex;flex-wrap:wrap;gap:18px;margin:16px 0}.rater input{display:block;margin-top:5px}.status-line{font-size:.85rem;color:#53677e}.download-note{max-width:850px}.tiny{font-size:.78rem;color:#617187;overflow-wrap:anywhere}details{margin:12px 0}summary{cursor:pointer;font-weight:600}a{color:#1c599e}@media(max-width:850px){.comparison{grid-template-columns:1fr}.ratings{grid-template-columns:1fr}main{padding:22px 12px}.toolbar{position:static}}@media print{.toolbar{position:static}button{display:none}.comparison{grid-template-columns:repeat(3,minmax(0,1fr))}.rating-card{break-inside:avoid}}
'''


def _page(title, content, script=""):
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' file: data:; media-src 'self' file: blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>{_h(title)}</title><style>{STYLE}</style></head><body><main>{content}</main>{'<script>'+script+'</script>' if script else ''}</body></html>'''


def _number(value):
    return str(value) if isinstance(value, (int, float)) else "미기록"


def _source_cards(sources, asset_dir, *, prefix):
    cards = []
    for source in sources:
        if source["kind"] == "text":
            content = f'<pre>{_h(source["text"])}</pre>'
        elif source["kind"] == "image":
            name, _ = _copy_asset(source["path"], asset_dir, "source_" + secrets.token_hex(8))
            content = f'<img class="source-image" src="{_h(prefix + name)}" alt="제공된 공통 참고 이미지">'
        else:
            continue
        origin = {"actual_collected": "실제 수집 자료", "ai_synthetic": "AI 합성 자료", "researcher_authored": "연구자 작성 자료"}.get(source.get("provenance"), "출처 성격 미기록")
        cards.append(f'<details><summary>{_h(source.get("label") or "공통 참고 자료")} · {_h(origin)}</summary>{content}</details>')
    return '<section class="sources"><h2>공통 참고 자료</h2>' + "".join(cards) + '</section>' if cards else ''


def build_report(spec, model_summary, render_summary, output_dir):
    """Build gallery and a separately copyable condition-masked human pack."""
    spec, spec_base = _read(spec)
    model, _ = _read(model_summary)
    rendered, render_base = _read(render_summary)
    output = Path(output_dir).resolve()
    if not output.is_relative_to(ROOT.resolve()) or output.exists():
        raise ValueError("report requires a new output directory inside the project")
    supplied = rendered.get("outputs", [])
    if not isinstance(supplied, list):
        raise ValueError("render_summary.outputs must be an array")
    supplied = [r for r in supplied if isinstance(r, dict)]
    cases = _case_rows(spec, supplied)
    conditions = list(CONDITIONS)
    for row in supplied:
        if row.get("condition") and row["condition"] not in conditions:
            conditions.append(row["condition"])
    output.mkdir(parents=True)
    gallery_assets = output / "gallery_assets"
    human_dir, human_assets = output / "human_pack", output / "human_pack/assets"
    gallery_assets.mkdir()
    human_assets.mkdir(parents=True)
    by_case, raw_by_case = defaultdict(list), defaultdict(list)
    for row in supplied:
        by_case[(row.get("case_id"), row.get("condition"))].append(row)
    for row in model.get("responses", []):
        raw_by_case[(row.get("case_id"), row.get("condition"))].append(row)
    sources = []
    for source in spec.get("sources", []):
        if not isinstance(source, dict):
            continue
        path = _local(source.get("path") or source.get("local_path"), spec_base)
        if not path:
            continue
        common = {"path": path, "provenance": source.get("provenance"), "label": source.get("label") or source.get("family_id") or "제공된 출처"}
        if path.suffix.lower() in {".txt", ".md"}:
            content = path.read_text(encoding="utf-8-sig")
            sources.append({**common, "kind": "text", "text": content})
        elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            sources.append({**common, "kind": "image"})
    entries, mapping, gallery_cases, file_records = [], [], [], []
    for case in cases:
        columns = []
        case_id = case["case_id"]
        brief = case.get("brief") or case.get("request") or "요청 원문 미제공"
        for condition in conditions:
            rows = by_case.get((case_id, condition)) or [{"case_id": case_id, "condition": condition, "status": "missing", "body": "", "planned_missing": True}]
            cards = []
            for attempt, row in enumerate(rows, 1):
                raw = raw_by_case.get((case_id, condition), [])
                body = row.get("body")
                if body is None and attempt <= len(raw):
                    body = raw[attempt - 1].get("output", "")
                body = body if isinstance(body, str) else ""
                blind_id = "R-" + secrets.token_hex(6).upper()
                media, blinded_media, missing_media = {}, {}, []
                for kind, allowed in (("image", {".png", ".jpg", ".jpeg", ".webp"}), ("video", {".mp4", ".webm"})):
                    source = _local(row.get(kind + "_path"), render_base)
                    if source and source.suffix.lower() in allowed:
                        gallery_name, digest = _copy_asset(source, gallery_assets, kind + "_" + secrets.token_hex(8))
                        blind_name, _ = _copy_asset(source, human_assets, blind_id + "_" + kind)
                        media[kind], blinded_media[kind] = gallery_name, blind_name
                        file_records.append({"response_id": row.get("response_id"), "kind": kind, "source_path": str(source), "sha256": digest})
                    elif row.get(kind + "_path"):
                        missing_media.append(kind)
                status = str(row.get("status") or "unrecorded")
                status_label = {"succeeded": "제작기 완료 보고", "completed": "완료 보고", "missing": "계획된 출력 미제공", "empty": "빈 출력", "failed": "실패"}.get(status, status)
                cards.append(f'<article class="card{" failed" if status not in {"succeeded", "completed"} else ""}"><h3>{_h(condition)} · {_h(CONDITION_NAMES.get(condition, condition))}</h3><p class="status-line">{_h(status_label)}{f" · 시도 {attempt}" if len(rows)>1 else ""}</p>{_content(body, media, prefix="gallery_assets/")}<p class="tiny">응답 {_h(row.get("response_id") or "미기록")} · 모델 실행 {_h(row.get("model_run_id") or model.get("run_id") or "미기록")}</p></article>')
                entries.append({"blind_id": blind_id, "brief": brief, "body": body, "media": blinded_media,
                                "content_present": bool(body or blinded_media), "missing_media": missing_media})
                mapping.append({"blind_id": blind_id, "case_id": case_id, "condition": condition, "attempt": attempt,
                                "response_id": row.get("response_id"), "model_run_id": row.get("model_run_id") or model.get("run_id"),
                                "status": status, "planned_missing": bool(row.get("planned_missing")), "original_output": row})
            columns.append('<div>' + "".join(cards) + '</div>')
        gallery_cases.append(f'<section class="case"><div class="case-head"><h2>{_h(case_id)}</h2><p>{_h(brief)}</p></div><div class="comparison">{"".join(columns)}</div></section>')
    planned_cases = sum(not c.get("unplanned_reported_case") for c in cases)
    denominator = planned_cases * len(CONDITIONS)
    cost = model.get("local_compute_cost")
    cost_text = "측정 안 됨" if cost is None else _h(json.dumps(cost, ensure_ascii=False) if isinstance(cost, dict) else cost)
    gallery = f'''<h1>콘텐츠 제작 파일럿 결과</h1><p>동일 요청의 세 조건 출력을 나란히 보여 줍니다. 빈 출력·실패·미제공 항목도 계획 분모에 남겼습니다.</p>
<div class="notice">실제 전달된 출력과 제작 파일을 표시합니다. 품질 점수·독립 사람 평가·시간에 따른 의미 변화 효과는 이 보고서가 계산하거나 검증하지 않습니다. 고정 카드와 정지 카드 영상은 제한된 제작 방식입니다.</div>
<div class="stats"><div class="stat">계획 요청 × 조건<strong>{denominator}</strong></div><div class="stat">보고된 출력 항목<strong>{len(supplied)}</strong></div><div class="stat">실제 최적화 단계<strong>{_h(_number(model.get("optimizer_steps")))}</strong></div><div class="stat">실제 생성 호출<strong>{_h(_number(model.get("generation_calls")))}</strong></div><div class="stat">로컬 계산 비용<strong>{cost_text}</strong></div></div>
<p class="muted">어댑터 가중치 변경 보고: {_h({True:"변경됨",False:"변경 없음"}.get(model.get("adapter_weights_changed"),"미확인"))} · 사람 품질 평가: 아직 수집하지 않음 · 모델 실행 상태: {_h(model.get("status") or "미기록")}</p>
<p><a class="button" href="human_pack/index.html">조건을 숨긴 평가 자료 열기</a></p>
{_source_cards(sources, gallery_assets, prefix="gallery_assets/")}{"".join(gallery_cases) if gallery_cases else '<p class="missing">제공된 계획 요청이 없습니다. 결과를 만들어 채우지 않았습니다.</p>'}
<p class="tiny">모델 실행: {_h(model.get("run_id") or "미기록")} · 출처 설명과 AI 작성 요청·학습 예시, 모델 생성 출력은 서로 다른 자료입니다. 이 화면은 학습 예시를 독립 정답으로 제시하지 않습니다.</p>'''
    (output / "gallery.html").write_text(_page("콘텐츠 제작 파일럿 결과", gallery), encoding="utf-8")
    secrets.SystemRandom().shuffle(entries)
    order = {entry["blind_id"]: index for index, entry in enumerate(entries, 1)}
    mapping.sort(key=lambda row: order[row["blind_id"]])
    cards = []
    for entry in entries:
        controls = []
        for key, label in METRICS:
            choices = '<option value="">선택 안 함</option>' + ''.join(f'<option value="{score}">{score}</option>' for score in range(1, 6)) + '<option value="NA">해당 없음 / 판단 불가</option>'
            controls.append(f'<label>{_h(label)}<select data-metric="{key}" aria-label="{_h(entry["blind_id"]+" "+label)}">{choices}</select></label>')
        cards.append(f'''<article class="rating-card" data-rating-id="{entry["blind_id"]}"><h2>항목 {order[entry["blind_id"]]} · {entry["blind_id"]}</h2><p><strong>제작 요청</strong><br>{_h(entry["brief"])}</p>{_content(entry["body"],entry["media"],prefix="assets/")}
<div class="ratings">{"".join(controls)}</div><label>판단 이유 또는 확인하지 못한 내용<textarea data-reason placeholder="직접 판단한 근거를 적어 주세요"></textarea></label></article>''')
    pack_id = "PACK-" + secrets.token_hex(10).upper()
    human = f'''<h1>콘텐츠 평가 자료</h1><p>각 요청과 제공된 본문·이미지·영상을 보고 직접 평가해 주세요. 조건·모델 이름은 표시하지 않습니다.</p>
<div class="notice">1은 낮음, 5는 높음입니다. 밈이 필요 없는 요청이나 확인할 수 없는 매체·기준은 ‘해당 없음 / 판단 불가’를 선택하고 이유를 적어 주세요. 빈 출력도 평가 항목에 포함했습니다. 자동 점수와 기본 선택값은 없습니다.</div>
<div class="rater"><label>평가자 코드 (이름·연락처 불필요)<input id="rater-code" autocomplete="off" maxlength="100"></label><label>실제 작업 시간, 직접 입력 (분)<input id="human-minutes" type="number" min="0" step="0.1" placeholder="미기입 가능"></label></div>
<div class="toolbar"><button type="button" id="download">평가 JSON 내려받기</button><p id="progress">선택된 점수 없음</p><p id="elapsed" class="muted">페이지 체류 0초</p></div>
<p class="muted download-note">입력은 외부로 전송되지 않습니다. 내려받은 JSON을 별도로 전달해야 수집됩니다. 페이지 체류시간은 실제 노동시간과 다르므로 직접 입력한 시간과 분리해서 기록합니다. 새로고침하면 입력이 사라집니다.</p>
<noscript><p class="missing">JavaScript가 꺼져 있어 JSON 다운로드를 사용할 수 없습니다. 자료는 그대로 읽을 수 있습니다.</p></noscript>
{_source_cards(sources,human_assets,prefix="assets/")}{"".join(cards) if cards else '<p class="missing">평가할 계획 항목이 제공되지 않았습니다.</p>'}
<p class="tiny">평가 묶음 {_h(pack_id)} · 콘텐츠 자체의 표현으로 조건을 추측할 가능성까지 제거했다고 보장하지 않습니다.</p>'''
    script = '''(()=>{"use strict";
const packId=PACK_ID, startedAt=new Date().toISOString(), start=performance.now(), edits=new Map();
const cards=[...document.querySelectorAll('[data-rating-id]')];
function update(){let answered=0;cards.forEach(c=>{if([...c.querySelectorAll('select')].some(s=>s.value!==''))answered++;});document.getElementById('progress').textContent=answered+' / '+cards.length+'개 항목에 점수 선택';}
cards.forEach(card=>card.addEventListener('input',()=>{const now=Date.now(), id=card.dataset.ratingId, old=edits.get(id);edits.set(id,{first_changed_at:old?old.first_changed_at:new Date(now).toISOString(),last_changed_at:new Date(now).toISOString(),first_ms:old?old.first_ms:now,last_ms:now});update();}));
setInterval(()=>{document.getElementById('elapsed').textContent='페이지 체류 '+Math.floor((performance.now()-start)/1000)+'초';},1000);
document.getElementById('download').addEventListener('click',()=>{const minuteText=document.getElementById('human-minutes').value, minutes=minuteText===''?null:Number(minuteText);if(minutes!==null&&(!Number.isFinite(minutes)||minutes<0)){alert('작업 시간은 0 이상의 숫자로 입력해 주세요.');return;}
const ratings=cards.map(card=>{const values={};card.querySelectorAll('select').forEach(s=>{values[s.dataset.metric]=s.value===''?null:s.value==='NA'?'NA':Number(s.value);});const e=edits.get(card.dataset.ratingId);return {rating_id:card.dataset.ratingId,scores:values,reason:card.querySelector('textarea').value,first_changed_at:e?e.first_changed_at:null,last_changed_at:e?e.last_changed_at:null,edit_time_span_seconds:e?(e.last_ms-e.first_ms)/1000:null,all_metrics_answered:Object.values(values).every(v=>v!==null)};});
const data={schema_version:'creation-human-ratings-v1',pack_id:packId,rater_code:document.getElementById('rater-code').value,page_opened_at:startedAt,exported_at:new Date().toISOString(),page_elapsed_seconds:(performance.now()-start)/1000,human_minutes_self_reported:minutes,measured_elapsed_is_human_labor:false,rating_origin:'user_entered_identity_unverified',independent_evaluator_status:'not_established_by_this_page',automatic_scores:false,ratings};
const blob=new Blob([JSON.stringify(data,null,2)+'\\n'],{type:'application/json;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=packId+'-ratings.json';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});update();
})();'''.replace("PACK_ID", json.dumps(pack_id))
    (human_dir / "index.html").write_text(_page("콘텐츠 평가 자료", human, script), encoding="utf-8")
    private = {"pack_id": pack_id, "created_at": datetime.now(timezone.utc).isoformat(), "randomization_method": "secrets.SystemRandom.shuffle; realized order preserved",
               "never_distribute_with_human_pack": True, "mapping": mapping, "model_run_id": model.get("run_id")}
    (output / "private_mapping.json").write_text(json.dumps(private, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema_version": "creation-pilot-report-v1", "created_at": datetime.now(timezone.utc).isoformat(), "pack_id": pack_id,
                "gallery": "gallery.html", "human_pack": "human_pack/index.html", "private_mapping": "private_mapping.json",
                "planned_case_count": planned_cases, "planned_condition_count": len(CONDITIONS), "planned_unit_count": denominator,
                "reported_output_count": len(supplied), "human_rating_item_count_including_missing_and_retries": len(entries),
                "condition_order_in_gallery": conditions, "sources": [{"path": str(s["path"]), "sha256": _sha(s["path"]), "provenance": s.get("provenance")} for s in sources],
                "copied_output_assets": file_records, "independent_human_ratings_collected": False, "quality_scores_generated": False,
                "external_data_transmission": False, "human_work_time": "self-report only; page/edit elapsed durations stored separately",
                "model_run_id": model.get("run_id"), "files_sha256": {str(p.relative_to(output)): _sha(p) for p in sorted(output.rglob('*')) if p.is_file()}}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
