"""Loopback-only evaluation preview/server. Never exposes study files or condition mappings."""
from __future__ import annotations
import argparse
import json
import sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import meme_pipeline as mp
import evaluation_store as es

ASSETS = Path(__file__).with_name("evaluation_ui")


def preview_bundle():
    families = [
        ("ui_quote", "회의는 끝났고, 커피는 남았다.", [
            ("긴 스터디를 마친 뒤 친구들에게 소감을 남기고 싶다.", "스터디는 끝났고, 간식은 남았다."),
            ("여행 마지막 날, 남은 간식을 보며 친구에게 짧은 말을 하고 싶다.", "여행은 끝났고, 과자는 남았다.")]),
        ("ui_dialogue", "오늘의 주인공은? 쉬는 사람!", [
            ("휴일에 친구와 주고받을 가벼운 인사가 필요하다.", "오늘의 주인공은? 푹 쉬는 우리!"),
            ("휴가를 시작한 동료를 단체 대화방에서 반갑게 맞이하고 싶다.", "오늘의 주인공은? 충전하는 사람!")]),
        ("ui_rhythm", "한 걸음, 한 번 더.", [
            ("어려운 퍼즐을 풀다 지친 친구에게 짧은 응원을 보내고 싶다.", "한 조각, 한 번 더."),
            ("산책을 조금 더 해 보려는 친구를 가볍게 격려하고 싶다.", "한 걸음, 한 번 더.")]),
    ]
    patterns = [(0,1,2),(1,2,0),(2,0,1),(0,2,1),(1,0,2),(2,1,0)]
    slots = [f"PREVIEW_SLOT_{i+1:02d}" for i in range(6)]
    items, assignments = [], []
    for fidx, (fid, canonical, scenarios) in enumerate(families):
        for qidx, (situation, output) in enumerate(scenarios):
            for cidx, condition in enumerate(("B1","B2","B3")):
                item_id = "ui_item_"+mp.stable_hash([fid,qidx,condition])[:20]
                items.append({"item_id":item_id,"family_id":fid,"condition":condition,
                    "scenario_id":fid+"_"+str(qidx),"repeat":0,"status":"ok",
                    "situation":situation,"output_text":output,
                    "reference":{"family_id":fid,"canonical_text":canonical,
                        "supports":[{"support_id":fid+"_support","text":canonical,
                                     "context":"화면 점검을 위해 AI가 만든 가상 표현입니다. 실제 수집 용례가 아닙니다."}]}})
                assignments.extend({"slot_id":slot,"item_id":item_id} for slot, pattern in zip(slots,patterns) if pattern[fidx]==cidx)
    return {"study_id":"ui_preview_v01","mode":"preview","seed":20260911,
        "authorization":{"approved":False,"approval_reference":None,"institutional_procedure_confirmed":False},
        "slots":slots,"items":items,"assignments":assignments,
        "provenance":"ui_fixture","note":"All situations, expressions and outputs are AI-authored UI fixtures, not model experiment outputs or actual observed memes."}


def bundle_from_export(export_dir, plan, authorization):
    """Bind a previously exported automatic study to its complete planned rater assignments."""
    directory = Path(export_dir)
    notes = mp.read_json(directory/"export_notes.json")
    mp.require(notes["mode"] == "pilot" and notes["expected_output_origin"]=="automatic", "This interface accepts automatic development pilot exports only")
    key = mp.read_jsonl(directory/"PRIVATE_key.jsonl")
    a = mp.index_unique(mp.read_jsonl(directory/"stage_a.jsonl"),"item_id")
    b = mp.index_unique(mp.read_jsonl(directory/"stage_b.jsonl"),"item_id")
    mp.index_unique(key,"item_id")
    good = {k["item_id"] for k in key if k["status"]=="ok"}
    mp.require(set(a)==set(b)==good,"A/B content must exactly cover successful planned outputs")
    mp.require(notes.get("imported_ok_fixture_responses")==0 and
               notes.get("imported_ok_model_responses")==len(good),
               "Experiment export must report only actual automatic responses")
    cells = {(k["scenario_id"],k["condition"],k["repeat"]):k for k in key}
    mp.require(len(cells)==len(key),"Duplicate output cell")
    expected = {(v["scenario_id"],v["condition"],v["repeat"]) for v in plan["assignments"]}
    mp.require(set(cells)==expected,"Rater plan and experiment grid differ")
    items=[]
    for k in key:
        item = {name:k[name] for name in ("item_id","family_id","condition","scenario_id","repeat","status")}
        item.update(situation="",output_text="",reference=None)
        if k["status"]=="ok":
            aa,bb=a[k["item_id"]],b[k["item_id"]]
            mp.require(aa["output_text"]==bb["output_text"],"Output changes between evaluation blocks")
            mp.require(bb["reference"]["family_id"]==k["family_id"],"Reference family mismatch")
            item.update(situation=aa["situation"],output_text=aa["output_text"],reference=bb["reference"])
        items.append(item)
    assignments=[]
    for row in plan["assignments"]:
        cell=cells[(row["scenario_id"],row["condition"],row["repeat"]) ]
        mp.require(cell["family_id"]==row["family_id"],"Assigned family differs from experiment")
        assignments.append({"slot_id":row["planning_slot_id"],"item_id":cell["item_id"]})
    return {"study_id":authorization["study_id"],"mode":"pilot","seed":authorization.get("seed",20260911),
            "authorization":authorization,"slots":[s["planning_slot_id"] for s in plan["rater_slots"]],
            "items":items,"assignments":assignments,
            "source_condition_key":key,
            "experiment_mode":notes["mode"],"source_export_hashes":{p.name:mp.file_hash(p) for p in directory.glob("*.jsonl")}}


def initialize(out, bundle):
    out=Path(out)
    mp.require(not out.exists(),"Choose a new study directory")
    out.mkdir(parents=True)
    # The store validates the full bundle before issuing tokens. Failed init is preserved for inspection.
    credentials=es.create_study(out/"study.sqlite3",bundle)
    mp.write_json(out/"bundle.PRIVATE.json",bundle)
    mp.write_json(out/"access_codes.PRIVATE.json",credentials)
    mp.write_json(out/"initialization.json",{"study_id":bundle["study_id"],"mode":bundle["mode"],
        "bundle_sha256":mp.file_hash(out/"bundle.PRIVATE.json"),"rater_slots":len(bundle["slots"]),
        "planned_items":len(bundle["items"]),"planned_assignments":len(bundle["assignments"]),
        "interface_hashes":{str(p.relative_to(ASSETS.parent)):mp.file_hash(p) for p in
            [Path(__file__),ASSETS.parent/"evaluation_store.py",*sorted(ASSETS.glob("*"))] if p.is_file()},
        "recruitment_performed":False,"actual_model_calls":0,
        "note":"Keep DB, bundle, access codes, exports and condition mappings private; preview scores are UI fixtures."})
    return out/"study.sqlite3"


class EvaluationHandler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"

    def log_message(self, *_):
        pass  # No access codes, cookies, IPs or participant text in console logs.

    def respond(self,status,body,mime="application/json; charset=utf-8",cookie=None):
        if not isinstance(body,bytes): body=json.dumps(body,ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie: self.send_header("Set-Cookie",cookie)
        self.end_headers()
        self.wfile.write(body)

    def check_origin(self):
        hosts={f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}"}
        mp.require(self.headers.get("Host") in hosts,"Unexpected host")
        origin=self.headers.get("Origin")
        mp.require(origin is None or origin in {"http://"+h for h in hosts},"Unexpected origin")

    def token(self):
        cookie=SimpleCookie()
        cookie.load(self.headers.get("Cookie",""))
        mp.require("evaluation_session" in cookie,"참여 코드를 입력해 주세요.")
        return cookie["evaluation_session"].value

    def do_GET(self):
        try:
            self.check_origin()
            path=urlsplit(self.path).path
            if path=="/api/next":
                return self.respond(200,es.next_item(self.server.db_path,self.token()))
            assets={"/":("index.html","text/html; charset=utf-8"),
                    "/app.js":("app.js","text/javascript; charset=utf-8"),
                    "/style.css":("style.css","text/css; charset=utf-8")}
            if path in assets:
                file,mime=assets[path]
                return self.respond(200,(ASSETS/file).read_bytes(),mime)
            return self.respond(404,{"error":"페이지를 찾을 수 없습니다."})
        except (ValueError,KeyError,TypeError):
            return self.respond(403,{"error":"접근 정보를 확인해 주세요. 참여 코드로 다시 시작할 수 있습니다."})

    def do_POST(self):
        try:
            self.check_origin()
            mp.require(self.headers.get("Content-Type","").split(";")[0]=="application/json","JSON required")
            length=int(self.headers.get("Content-Length","0"))
            mp.require(0<length<=16384,"Invalid body size")
            data=json.loads(self.rfile.read(length))
            mp.require(isinstance(data,dict),"Invalid request")
            path=urlsplit(self.path).path
            if path=="/api/session":
                token=data.get("token")
                mp.require(isinstance(token,str) and len(token)<=256,"Invalid access code")
                state=es.next_item(self.server.db_path,token)
                return self.respond(200,state,cookie=f"evaluation_session={token}; Path=/; HttpOnly; SameSite=Strict")
            if path=="/api/rating":
                state=es.submit_rating(self.server.db_path,self.token(),data["item_id"],data["phase"],data["values"])
                return self.respond(200,state)
            if path=="/api/logout":
                return self.respond(200,{"ok":True},cookie="evaluation_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            return self.respond(404,{"error":"요청을 찾을 수 없습니다."})
        except (ValueError,KeyError,TypeError,UnicodeDecodeError,json.JSONDecodeError):
            return self.respond(400,{"error":"응답을 저장하지 못했습니다. 모든 항목을 선택했는지 확인하고, 새로고침 후 현재 문항을 확인해 주세요."})
        except Exception:
            return self.respond(500,{"error":"저장 중 문제가 생겼습니다. 현재 응답이 저장됐는지 확인하기 위해 새로고침해 주세요."})


def make_server(db_path,port=8769):
    mp.require(Path(db_path).is_file(),"Initialize a study first")
    server=ThreadingHTTPServer(("127.0.0.1",port),EvaluationHandler)
    server.db_path=Path(db_path)
    return server


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    preview=sub.add_parser("preview"); preview.add_argument("--out",required=True)
    init=sub.add_parser("init"); init.add_argument("--export",required=True); init.add_argument("--plan",required=True); init.add_argument("--scope",required=True); init.add_argument("--out",required=True)
    serve=sub.add_parser("serve"); serve.add_argument("--db",required=True); serve.add_argument("--port",type=int,default=8769)
    exp=sub.add_parser("export"); exp.add_argument("--db",required=True); exp.add_argument("--out",required=True)
    args=p.parse_args()
    if args.action in {"preview","init"}:
        bundle=preview_bundle() if args.action=="preview" else bundle_from_export(args.export,mp.read_json(args.plan),mp.read_json(args.scope))
        print(json.dumps({"db":str(initialize(args.out,bundle)),"mode":bundle["mode"],"network_started":False}))
    elif args.action=="serve":
        server=make_server(args.db,args.port)
        print(f"Local evaluation interface: http://127.0.0.1:{server.server_port}",flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()
    else:
        rows=es.export_rows(args.db)
        mp.write_json(args.out,{"kind":"evaluation_export_with_provenance","rows":rows,
            "warning":"Preview/ui_fixture rows are software checks and must not enter research effect estimates. Failure and unrated rows retain the planned denominator."})
        print(json.dumps({"export":args.out,"rows":len(rows)}))


if __name__=="__main__":
    try: main()
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(f"STOPPED: {exc}",file=sys.stderr); sys.exit(2)
