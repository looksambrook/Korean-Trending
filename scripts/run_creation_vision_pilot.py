"""Isolated SmolVLM acquisition and separately registered native-image pilot."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from research_log import ResearchLog,hash_file

REPO="HuggingFaceTB/SmolVLM-256M-Instruct"
REVISION="7e3e67edbbed1bf9888184d9df282b700a323964"
DEPENDENCIES={"transformers":"4.49.0","tokenizers":"0.21.0"}
WHEEL_HASHES={"transformers":"6b4fded1c5fee04d384b1014495b4235a2b53c87503d7d592423c06128cbbe03",
    "tokenizers":"87841da5a25a3a5f70c102de371db120f41873b854ba65e52bccd57df5a3780c"}


def write(path,value):
    with Path(path).open("x",encoding="utf8") as h:
        json.dump(value,h,ensure_ascii=False,indent=2,allow_nan=False)
        h.write("\n")


def acquire(args):
    deps=Path(args.deps_path).resolve()
    model=Path(args.model_path).resolve()
    for path in (deps,model):
        if not path.is_relative_to(ROOT):
            raise ValueError("acquisition_targets_must_be_in_workspace")
    spec={"objective":"Acquire pinned public SmolVLM and two isolated wheels without modifying global packages",
        "stage":"access_probe","parameters":{"repo":REPO,"revision":REVISION,"dependencies":DEPENDENCIES,
            "wheel_sha256":WHEEL_HASHES,"deps_path":str(deps),"model_path":str(model),"model_byte_limit":530_000_000},
        "data_snapshots":[],"code_hashes":{"acquisition_runner":hash_file(Path(__file__))},
        "prompt_hashes":{},"config_hashes":{},"modality":["text","image"],"provenance":"not_applicable"}
    log=ResearchLog(args.db)
    run=log.begin(spec,retry_of=args.retry_of,retry_reason=args.retry_reason)
    folder=ROOT/"logs/research_artifacts"/run["run_id"]
    folder.mkdir(parents=True,exist_ok=False)
    result={"run_id":run["run_id"],"status":"started","repo":REPO,"revision":REVISION,
        "global_packages_modified":False,"weights_loaded":False,"paid_api_calls":0,"files":[],"failures":[]}
    tick=time.perf_counter()
    try:
        from huggingface_hub import HfApi,snapshot_download
        info=HfApi().model_info(REPO,revision=REVISION,files_metadata=True)
        if info.sha != REVISION:
            raise ValueError("resolved_revision_mismatch")
        selected=[s for s in info.siblings if "/" not in s.rfilename and
            (s.rfilename.endswith((".json",".txt")) or s.rfilename in {"model.safetensors","README.md"})]
        if not any(s.rfilename=="model.safetensors" for s in selected) or any(s.size is None for s in selected):
            raise ValueError("model_inventory_incomplete")
        total=sum(s.size for s in selected)
        if total>530_000_000:
            raise ValueError("model_inventory_exceeds_authorized_small_download")
        result["resolved_revision"]=info.sha
        result["planned_model_bytes"]=total
        inventory=[]
        for s in selected:
            inventory.append({"name":s.rfilename,"bytes":s.size,"expected_lfs_sha256":getattr(s.lfs,"sha256",None) if s.lfs else None})
        write(folder/"remote_inventory.json",inventory)
        if deps.exists() and any(deps.iterdir()):
            raise ValueError("isolated_dependency_target_already_populated_inspect_before_retry")
        requirements=folder/"isolated_requirements.txt"
        with requirements.open("x",encoding="ascii") as handle:
            for name,version in DEPENDENCIES.items():
                handle.write(f"{name}=={version} --hash=sha256:{WHEEL_HASHES[name]}\n")
        command=[sys.executable,"-m","pip","install","--target",str(deps),"--no-deps","--only-binary=:all:",
            "--require-hashes","--disable-pip-version-check","-r",str(requirements)]
        with (folder/"pip_stdout.txt").open("x",encoding="utf8") as stdout,(folder/"pip_stderr.txt").open("x",encoding="utf8") as stderr:
            installation=subprocess.run(command,stdout=stdout,stderr=stderr,timeout=180,check=False)
        result["pip_returncode"]=installation.returncode
        if installation.returncode:
            raise RuntimeError("isolated_pip_install_failed")
        resolved=snapshot_download(repo_id=REPO,revision=REVISION,local_dir=str(model),
            allow_patterns=[s.rfilename for s in selected],max_workers=1)
        result["resolved_model_path"]=str(Path(resolved).resolve())
        for remote in inventory:
            path=model/remote["name"]
            digest=hash_file(path)
            record={**remote,"path":str(path),"sha256":digest,"actual_bytes":path.stat().st_size}
            result["files"].append(record)
            if record["actual_bytes"]!=remote["bytes"] or (remote["expected_lfs_sha256"] and remote["expected_lfs_sha256"]!=digest):
                raise ValueError("downloaded_file_identity_mismatch")
        metadata=[]
        for path in sorted(deps.glob("*.dist-info/METADATA")):
            metadata.append({"path":str(path),"sha256":hash_file(path)})
        result["isolated_metadata"]=metadata
        result["status"]="completed"
    except Exception as exc:
        result["status"]="failed"
        result["failures"].append({"type":type(exc).__name__,"message":str(exc)[:1600]})
    finally:
        result["wall_seconds"]=round(time.perf_counter()-tick,6)
        write(folder/"acquisition.json",result)
        log.finish(run["run_id"],status="succeeded" if result["status"]=="completed" else "failed",
            notes=f"Pinned public image model acquisition {result['status']}; no model load; no global package update; SDK request count not instrumented.",
            artifacts=[p for p in folder.iterdir() if p.is_file()],actual_cost={"amount":None,"currency":None,"api_units":0,"human_minutes":0})
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result["status"]=="completed" else 2


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--acquire",action="store_true")
    p.add_argument("--deps-path",default=str(ROOT/".local_dependencies/vision_tf449"))
    p.add_argument("--model-path",default=str(ROOT/"data/models/smolvlm_256m_7e3e67e"))
    p.add_argument("--db",default=str(ROOT/"logs/research.sqlite3"))
    p.add_argument("--retry-of")
    p.add_argument("--retry-reason")
    p.add_argument("--spec")
    p.add_argument("--output-dir")
    p.add_argument("--worker")
    args=p.parse_args()
    if args.acquire:
        return acquire(args)
    from creation_vision_backend import run_registered,run_worker
    if args.worker:
        return run_worker(args.worker)
    if not args.spec or not args.output_dir:
        p.error("--spec and --output-dir required unless --acquire")
    return run_registered(args.spec,args.output_dir,args.db,args.deps_path,args.retry_of,args.retry_reason)


if __name__=="__main__":
    raise SystemExit(main())
