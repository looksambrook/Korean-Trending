"""Register an actual incremental C0/CR/CF adapter update with optimizer reset.

Input JSON: model_path, revision, cases, train_examples, settings, provenance.
Additional required fields: prior_run_id, initial_adapter_path,
initial_adapter_sha256. Prior manifest/config paths default to the adapter dir.
There are no built-in synthetic or gold examples and no automatic retries.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from creation_incremental_backend import checkpoint_identity, model_identity, run_experiment, sha_file, validate_settings, write_json
from research_log import DuplicateRunError, ResearchLog


def worker(request_path):
    request=json.loads(Path(request_path).read_text(encoding="utf8"))
    result=run_experiment(**request)
    return 0 if result["status"]=="completed" else 2


def run(spec_path,output_dir,db,retry_of=None,retry_reason=None):
    source=Path(spec_path).resolve()
    if source.stat().st_size>4*1024**2:
        raise ValueError("spec_byte_limit")
    data=json.loads(source.read_text(encoding="utf-8-sig"))
    options=validate_settings(data.get("settings"))
    identity=model_identity(data["model_path"],data["revision"])
    checkpoint=checkpoint_identity(data,identity,options)
    versions={}
    import importlib.metadata
    for name in ("torch","transformers","accelerate","safetensors","tokenizers"):
        versions[name]=importlib.metadata.version(name)
    spec={"objective":"Actual bounded incremental text adapter update from verified prior checkpoint with optimizer reset",
        "stage":"model_experiment","parameters":{"settings":options,"versions":versions,"scope":"developer_local_text_incremental_update_diagnostic",
            "prior_run_id":data["prior_run_id"],"optimizer_reset_at_update":True,"continuous_optimizer_resume":False},
        "data_snapshots":[{"id":"experiment_input","sha256":sha_file(source)},{"id":"actual_model_snapshot","sha256":identity["snapshot_sha256"]},
            {"id":"initial_adapter","sha256":checkpoint["adapter_file_sha256"]},
            {"id":"prior_manifest","sha256":checkpoint["prior_manifest_sha256"]},
            {"id":"prior_adapter_config","sha256":checkpoint["adapter_config_sha256"]}],
        "code_hashes":{"backend":sha_file(ROOT/"src/creation_incremental_backend.py"),"runner":sha_file(Path(__file__))},
        "prompt_hashes":{},"config_hashes":{},"model_version":data["revision"],"modality":["text"],
        "provenance":data["provenance"]}
    log=ResearchLog(db)
    prior_run=log.status(data["prior_run_id"])
    if prior_run["status"]!="succeeded":
        raise ValueError("prior_registry_run_must_be_succeeded")
    recorded={str(Path(entry["path"]).resolve()).casefold():entry["sha256"]
        for entry in prior_run["finish"]["artifacts"]}
    for path_key,hash_key in (("adapter_path","adapter_file_sha256"),("prior_manifest_path","prior_manifest_sha256"),
                               ("adapter_config_path","adapter_config_sha256")):
        if recorded.get(checkpoint[path_key].casefold())!=checkpoint[hash_key]:
            raise ValueError("prior_checkpoint_differs_from_registry_artifact")
    registered=log.begin(spec,retry_of=retry_of,retry_reason=retry_reason)
    destination=Path(output_dir).resolve()
    result={"run_id":registered["run_id"],"status":"failed","failures":[]}
    artifacts=[]
    tick=time.perf_counter()
    try:
        destination.mkdir(parents=True,exist_ok=False)
        write_json(destination/"spec.json",data)  # Preserve provenance and any caller-owned evidence metadata.
        write_json(destination/"initial_checkpoint.json",checkpoint)
        request={"model_path":data["model_path"],"revision":data["revision"],"cases":data["cases"],
            "train_examples":data["train_examples"],"settings":options,"output_dir":str(destination/"worker"),
            "registered_run_id":registered["run_id"],"identity":identity,"initial_checkpoint":checkpoint}
        write_json(destination/"request.json",request)
        environment=dict(os.environ)
        environment.update(HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",HF_HUB_DISABLE_TELEMETRY="1",TOKENIZERS_PARALLELISM="false",PYTHONDONTWRITEBYTECODE="1")
        import psutil
        stop_reason=None
        peak_rss=0
        with (destination/"stdout.txt").open("x",encoding="utf8") as stdout,(destination/"stderr.txt").open("x",encoding="utf8") as stderr:
            proc=subprocess.Popen([sys.executable,"-X","utf8","-B",str(Path(__file__).resolve()),"--worker",str(destination/"request.json")],
                stdout=stdout,stderr=stderr,env=environment,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            monitor=psutil.Process(proc.pid)
            while proc.poll() is None:
                try:
                    rss=monitor.memory_info().rss
                    peak_rss=max(peak_rss,rss)
                    if rss>options["max_rss_mb"]*1024**2:
                        stop_reason="parent_rss_limit"
                except psutil.NoSuchProcess:
                    break
                if time.perf_counter()-tick>options["max_wall_seconds"]:
                    stop_reason="parent_wall_timeout"
                if stop_reason:
                    proc.kill()
                    break
                time.sleep(.5)
            proc.wait(timeout=10)
        manifest_path=destination/"worker/manifest.json"
        if manifest_path.exists():
            result=json.loads(manifest_path.read_text(encoding="utf8"))
        else:
            result["failures"].append({"type":"WorkerTermination","message":stop_reason or "worker_ended_without_manifest"})
            event_path=destination/"worker/events.jsonl"
            recovered=[]
            if event_path.exists():
                for line in event_path.read_text(encoding="utf8").splitlines():
                    try:
                        recovered.append(json.loads(line))
                    except ValueError:
                        pass  # An interrupted final write is not a completed event.
            result["optimizer_steps"]=sum(e.get("event")=="optimizer_step_completed" for e in recovered)
            result["generation_calls"]=sum(e.get("event")=="generation_started" for e in recovered)
            result["training_steps"]=[e for e in recovered if e.get("event")=="optimizer_step_completed"]
            result["responses"]=[e["record"] for e in recovered if e.get("event")=="generation_completed"]
            result["recovered_counts_are_observed_lower_bounds"]=True
        if stop_reason or proc.returncode!=0:
            result["status"]="failed"
        result["supervisor"]={"returncode":proc.returncode,"stop_reason":stop_reason,"sampled_peak_rss_bytes":peak_rss,
            "wall_seconds":round(time.perf_counter()-tick,6)}
        write_json(destination/"run_summary.json",result)
        artifacts=[p for p in destination.rglob("*") if p.is_file()]
    except Exception as exc:
        result["status"]="failed"
        result["failures"].append({"type":type(exc).__name__,"message":str(exc)[:1600]})
    finally:
        log.finish(registered["run_id"],status="succeeded" if result["status"]=="completed" else "failed",
            notes=f"Incremental text diagnostic {result['status']}; prior_run={data['prior_run_id']}; actual additional optimizer_steps={result.get('optimizer_steps',0)}; generation_calls={result.get('generation_calls',0)}; optimizer reset at update. This is not continuous optimizer resume, native multimodal evidence, or measured real-world trend change.",
            artifacts=artifacts,actual_cost={"amount":None,"currency":None,"api_units":0,"human_minutes":0})
    print(json.dumps({"run_id":registered["run_id"],"status":result["status"],"optimizer_steps":result.get("optimizer_steps",0),
        "generation_calls":result.get("generation_calls",0),"failures":result.get("failures",[])},ensure_ascii=False))
    return 0 if result["status"]=="completed" else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec")
    parser.add_argument("--output-dir")
    parser.add_argument("--db",default=str(ROOT/"logs/research.sqlite3"))
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    parser.add_argument("--worker")
    args=parser.parse_args()
    if args.worker:
        return worker(args.worker)
    if not args.spec or not args.output_dir:
        parser.error("--spec and --output-dir required")
    try:
        return run(args.spec,args.output_dir,args.db,args.retry_of,args.retry_reason)
    except DuplicateRunError as exc:
        print(json.dumps({"error":"duplicate_run","existing_run_id":exc.existing["run_id"]}))
        return 3


if __name__=="__main__":
    raise SystemExit(main())
