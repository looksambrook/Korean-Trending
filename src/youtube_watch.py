"""Collect or reuse a YouTube snapshot, extract candidates, and build a local report.

No server, login, model call or paid API is needed for the feed path. The report
contains all observed documents so that unselected examples remain reviewable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_log import ResearchLog, hash_file, read_json
from youtube_feed_collect import collect
from youtube_candidates import run as find_candidates


HTML = r'''<!doctype html>
<html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>YouTube 밈 관찰실</title>
<style>
:root{color-scheme:light;--ink:#152329;--muted:#52666e;--line:#d7e1e5;--accent:#087c78}
*{box-sizing:border-box}body{margin:0;background:#f4f7f7;color:var(--ink);font:16px/1.65 system-ui,"Malgun Gothic",sans-serif}
header{background:#102e36;color:white;padding:40px max(24px,calc((100vw - 1180px)/2))}header p{color:#c6dadd;max-width:850px}
h1{font-size:32px;margin:8px 0}h2{font-size:22px;margin:0 0 16px}h3{font-size:18px;margin:0 0 8px}
main{max-width:1230px;padding:28px 24px;margin:auto}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:24px}
.stat,.panel,.card{background:white;border:1px solid var(--line);border-radius:12px;padding:20px}.stat strong{font-size:29px;display:block}
.stat small,.muted{color:var(--muted)}.controls{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}input,select,button{font:inherit;padding:10px 14px;border:1px solid var(--line);border-radius:8px;background:white}input{min-width:240px;flex:1}button{cursor:pointer}button.active{background:var(--accent);color:white}
.layout{display:grid;grid-template-columns:minmax(300px,1fr) minmax(320px,1.15fr);gap:24px}.list{display:grid;gap:12px;max-height:850px;overflow:auto}.card{cursor:pointer}.card:hover{border-color:var(--accent)}.tag{display:inline-block;background:#e8f4f2;padding:2px 8px;border-radius:5px;font-size:12px;margin-right:5px}.count{font-size:14px;color:var(--muted)}.warning{background:#fff4df;padding:14px 18px;border-left:4px solid #c28a2b;border-radius:6px}
.evidence{border-top:1px solid var(--line);padding:16px 0}a{color:#08736f}pre{font:14px/1.65 inherit;white-space:pre-wrap;overflow-wrap:anywhere}details summary{cursor:pointer}.empty{padding:30px;color:var(--muted)}footer{margin:24px 0;color:var(--muted);font-size:13px;overflow-wrap:anywhere}@media(max-width:850px){.layout{grid-template-columns:1fr}.list{max-height:540px}}
</style>
<header><div>실제 수집 자료 · 출처를 따라 확인</div><h1>YouTube 밈 관찰실</h1><p>영상 제목과 설명에서 반복 표현을 찾고, 어떤 문맥에서 쓰였는지 확인합니다. 후보는 자동 추출 결과이며 밈 판정·유행 증가·독립 사용은 추가 근거가 필요합니다.</p></header>
<main><div id="stats" class="stats"></div><div class="warning" id="scope"></div>
<div class="controls"><input id="query" placeholder="표현·영상 제목·채널 검색" aria-label="검색"><select id="channel" aria-label="채널"><option value="">모든 채널</option></select><button id="candidateTab" class="active">후보 표현</button><button id="allTab">전체 관측 문서</button><button id="expansionTab">사후 이름 검색</button></div>
<div class="layout"><section><h2 id="listTitle">관측된 반복 표현</h2><div id="list" class="list"></div></section><section class="panel"><h2 id="detailTitle">출처와 사용 문맥</h2><div id="detail" class="empty">왼쪽의 표현 또는 영상을 선택하세요.</div></section></div>
<details class="panel" style="margin-top:24px"><summary>수집 범위와 제외 기록</summary><div id="limits"></div></details>
<footer id="footer"></footer></main><script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const data=JSON.parse(document.getElementById('payload').textContent), byId=new Map(data.observations.map(r=>[r.observation_id,r])), metadata=new Map(data.metadata.map(r=>[r.video_id,r]));
const $=id=>document.getElementById(id);let mode='candidates';
const node=(tag,text,cls)=>{let n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
const title=r=>r.context?.title||r.text?.split('\n')[0]||r.video_id;
const channel=r=>r.context?.channel_label||(metadata.get(r.video_id)?.context?.channel_label ? metadata.get(r.video_id).context.channel_label+' · 후속 확인':r.channel_id)||'채널 미상';
const ids=c=>c.evidence_observation_ids.map(x=>byId.get(x)).filter(Boolean);
for(const [label,value,note] of [['관측 영상',data.observations.length,'제목·설명'],['채널',new Set(data.observations.map(r=>r.channel_id)).size,'편의 표본'],['반복 표현 후보',data.candidates.length,'자동 추출 · 미확정'],['공개 피드 요청',data.manifest.counts.requests,'유료 API·모델 호출 없음']]){let n=node('div',undefined,'stat');n.append(node('small',label),node('strong',String(value)),node('small',note));$('stats').append(n)}
$('scope').textContent='관측 시각: '+data.summary.cutoff+' · '+data.summary.method+' · 실제 수집 원문을 보존했습니다. 원본 영상·음성·댓글은 수집하지 않았습니다. 전체 관측 문서 탭에는 후보에서 빠진 문서도 표시합니다.';
for(const [id,name] of new Map([...data.observations,...data.expansion].filter(r=>r.channel_id).map(r=>[r.channel_id,channel(r)]))){let o=node('option',name);o.value=id;$('channel').append(o)}
function evidence(r){let e=node('div',undefined,'evidence'),a=node('a',title(r));a.href='https://www.youtube.com/watch?v='+encodeURIComponent(r.video_id);a.target='_blank';a.rel='noopener noreferrer';e.append(a,node('div',channel(r)+' · 게시 '+(r.posted_at||r.context?.publication_label||'정확한 시각 미확인'),'count'));let d=node('details');d.append(node('summary','원문·설명·관측 시각 보기'));d.append(node('pre',r.text),node('div','수집 '+r.collected_at+' / 이용 가능 '+r.available_at,'count'));e.append(d);let full=metadata.get(r.video_id);if(full){let more=node('details');more.open=true;more.append(node('summary','후속 원문 확인 · '+full.context.channel_label),node('div','게시 '+(full.posted_at||full.posted_date||'미확인')+' / 후속 수집 '+full.collected_at,'count'),node('pre',full.text));e.append(more)}return e}
function select(item){$('detail').replaceChildren();$('detail').className='';if(mode==='candidates'){let rows=ids(item);$('detailTitle').textContent=item.representative_phrase;$('detail').append(node('p','자동 후보입니다. 같은 채널의 재활용, 프로그램명, 광고 문구일 수 있습니다. 독립 사용이나 의미를 확정한 결과는 아닙니다.','muted'));$('detail').append(node('div',(item.surface_forms||[]).join(' · '),'tag'));for(let r of rows)$('detail').append(evidence(r))}else{$('detailTitle').textContent=title(item);$('detail').append(evidence(item))}}
function render(){const q=$('query').value.toLocaleLowerCase(),cid=$('channel').value;let rows=(mode==='candidates'?data.candidates:mode==='expansion'?data.expansion:data.observations).filter(r=>{const evidenceRows=mode==='candidates'?ids(r):[r];return (!cid||evidenceRows.some(e=>e.channel_id===cid))&&(!q||((mode==='candidates'?r.representative_phrase+' '+r.surface_forms.join(' '):r.text)+' '+evidenceRows.map(e=>channel(e)+' '+title(e)).join(' ')).toLocaleLowerCase().includes(q))});$('list').replaceChildren();$('listTitle').textContent=(mode==='candidates'?'관측된 반복 표현':mode==='expansion'?'사후 이름 검색 · 최초 발견과 별도':'전체 관측 문서')+' · '+rows.length;for(let r of rows){let c=node('button',undefined,'card');c.style.textAlign='left';c.append(node('h3',mode==='candidates'?r.representative_phrase:title(r)));if(mode==='candidates'){let es=ids(r);c.append(node('div','영상 '+new Set(es.map(e=>e.video_id)).size+' · 채널 '+new Set(es.map(e=>e.channel_id)).size+' · 자동 후보','count'))}else c.append(node('div',channel(r)+' · '+(r.posted_at?.slice(0,10)||r.context?.publication_label||'게시일 미확인'),'count'));c.onclick=()=>select(r);$('list').append(c)}if(!rows.length)$('list').append(node('div','조건에 맞는 관측 자료가 없습니다.','empty'))}
$('query').oninput=render;$('channel').onchange=render;for(const [id,value] of [['candidateTab','candidates'],['allTab','all'],['expansionTab','expansion']])$(id).onclick=()=>{mode=value;$('candidateTab').className=mode==='candidates'?'active':'';$('allTab').className=mode==='all'?'active':'';$('expansionTab').className=mode==='expansion'?'active':'';render()};
$('limits').append(node('p','게시일 범위는 채널마다 다릅니다. 현재 받은 피드로 과거의 발견 가능성이나 플랫폼 전체 유행을 복원할 수 없습니다.'));for(let r of data.manifest.requests)$('limits').append(node('div',r.channel_id+' · '+r.status+' · 영상 '+(r.observations||0)));$('limits').append(node('p','탐지 제외 '+data.summary.excluded_observations+'건 · 안내문 등 억제 '+data.suppressed.length+'개 기록. 원본 삭제 없이 별도 기록합니다.'));let ds=node('details');ds.append(node('summary','제외·억제 사유 보기'),node('pre',JSON.stringify({exclusions:data.exclusions,suppressed:data.suppressed},null,2)));$('limits').append(ds);
$('footer').textContent='수집 실행 '+data.manifest.run_id+' / 후보 추출 '+data.summary.run_id+' / 보고서 '+data.report_run_id+' · 사후 이름 검색 '+data.expansion.length+'건과 후속 원문 확인 '+data.metadata.length+'건은 원래 T의 발견·빈도 계산에 포함하지 않았습니다. 통제된 모델 이해 실험 및 사람 정답 평가는 아직 수행하지 않았습니다.';render();
</script></html>'''


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def build_report(manifest_path, candidate_dir, output_path, *, db="logs/research.sqlite3", expansion_manifest=None,
                 metadata_manifest=None):
    manifest_path, candidate_dir, output_path = map(Path, (manifest_path, candidate_dir, output_path))
    if output_path.exists():
        raise ValueError("Report already exists; preserve it and choose a new path")
    files = {"manifest": manifest_path, "observations": manifest_path.parent / "observations.jsonl",
             "summary": candidate_dir / "summary.json", "candidates": candidate_dir / "candidates.jsonl",
             "exclusions": candidate_dir / "exclusions.jsonl"}
    if (candidate_dir / "suppressed.jsonl").exists():
        files["suppressed"] = candidate_dir / "suppressed.jsonl"
    if expansion_manifest:
        files["expansion_manifest"] = Path(expansion_manifest)
        files["expansion"] = Path(expansion_manifest).parent / "observations.jsonl"
    if metadata_manifest:
        if not expansion_manifest:
            raise ValueError("Metadata enrichment requires its originating search manifest")
        files["metadata_manifest"] = Path(metadata_manifest)
        files["metadata"] = Path(metadata_manifest).parent / "observations.jsonl"
    hashes = {key: hash_file(path) for key, path in files.items()}
    payload = {key: read_json(path) if path.suffix == ".json" else rows(path) for key, path in files.items()}
    payload.setdefault("suppressed", [])
    payload.setdefault("expansion", [])
    payload.setdefault("metadata", [])
    if expansion_manifest and (payload["expansion_manifest"]["config"]["discovery_run_id"] != payload["manifest"]["run_id"]
                               or payload["expansion_manifest"]["provenance"] != payload["manifest"]["provenance"]):
        raise ValueError("Expansion must belong to this original collection and have matching provenance")
    if metadata_manifest and (payload["metadata_manifest"]["config"]["source_run_id"] != payload["expansion_manifest"]["run_id"]
                              or payload["metadata_manifest"]["provenance"] != payload["manifest"]["provenance"]):
        raise ValueError("Metadata must belong to this search expansion and have matching provenance")
    if payload["summary"]["input_sha256"] != hashes["observations"]:
        raise ValueError("Candidate results do not refer to this observation snapshot")
    if any(hash_file(files[key]) != value for key, value in hashes.items()):
        raise ValueError("Input changed during report loading")
    log = ResearchLog(db)
    spec = {"objective": "Create source-linked local YouTube observation report",
            "stage": "offline_validation", "parameters": {"format": "standalone_html"},
            "data_snapshots": [{"id": key, "sha256": value} for key, value in hashes.items()],
            "code_hashes": {"report": hash_file(__file__)}, "prompt_hashes": {}, "config_hashes": {},
            "modality": ["text", "metadata"], "provenance": payload["manifest"]["provenance"]}
    record = log.begin(spec)
    try:
        payload["report_run_id"] = record["run_id"]
        encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("x", encoding="utf-8") as stream:
            stream.write(HTML.replace("__PAYLOAD__", encoded))
        log.finish(record["run_id"], status="succeeded", notes="Built a local interactive report of all observed documents and unverified candidates; no model or human judgement added.", artifacts=[output_path], actual_cost={"amount": 0, "currency": "USD"})
        return {"report": str(output_path.resolve()), "run_id": record["run_id"]}
    except BaseException as exc:
        log.finish(record["run_id"], status="failed", notes="Report failed: " + type(exc).__name__)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report")
    report.add_argument("--manifest", required=True)
    report.add_argument("--candidates", required=True)
    report.add_argument("--output", required=True)
    report.add_argument("--expansion-manifest", help="Show later named search evidence separately")
    report.add_argument("--metadata-manifest", help="Attach later watch-page metadata without changing the original observations")
    watch = sub.add_parser("run")
    source = watch.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", help="Collect a new planned feed observation round")
    source.add_argument("--manifest", help="Reuse a completed local collection without network requests")
    watch.add_argument("--output-dir", required=True)
    watch.add_argument("--method", choices=["D0", "D1"], default="D0")
    watch.add_argument("--retry-of")
    watch.add_argument("--retry-reason")
    for p in [report, watch]:
        p.add_argument("--db", default="logs/research.sqlite3")
    args = parser.parse_args()
    if args.command == "report":
        result = build_report(args.manifest, args.candidates, args.output, db=args.db,
                              expansion_manifest=args.expansion_manifest, metadata_manifest=args.metadata_manifest)
    else:
        output = Path(args.output_dir)
        if output.exists():
            raise ValueError("Choose a new output directory")
        if args.config:
            collection = collect(read_json(args.config), output_dir=output / "collection", logdb=args.db,
                                 retry_of=args.retry_of, retry_reason=args.retry_reason)
            manifest_path = output / "collection" / "manifest.json"
        else:
            manifest_path = Path(args.manifest)
            collection = read_json(manifest_path)
        if collection["counts"]["observations"] == 0:
            raise ValueError("No observations were collected; inspect the collection manifest")
        find_candidates(manifest_path.parent / "observations.jsonl", output / "candidates", db=args.db,
                        cutoff=collection["finished_at"], method=args.method)
        result = build_report(manifest_path, output / "candidates", output / "report.html", db=args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
