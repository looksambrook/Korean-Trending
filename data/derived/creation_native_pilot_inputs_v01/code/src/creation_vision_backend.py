"""Isolated SmolVLM native-image development diagnostic; text output only.

Run via scripts/run_creation_vision_pilot.py. Inputs have images as a list of
{local_path, sha256, kind}, where kind is image or selected_video_frame.
Selected video frames are still images, not a native audiovisual stream.
No downloads occur during model execution. All source image files stay intact.
"""
from __future__ import annotations
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from creation_model_backend import (DEFAULTS as TEXT_DEFAULTS, ResourceLimitError,
    _install_lora, _parameter_hash, model_identity, sha_file, validate_settings, write_json)
from research_log import ResearchLog

ROOT=Path(__file__).resolve().parents[1]
REVISION="7e3e67edbbed1bf9888184d9df282b700a323964"
DEFAULTS={**TEXT_DEFAULTS,"device":"cpu","dtype":"float32","steps":1,
    "max_input_tokens":384,"max_sequence_tokens":512,"max_new_tokens":32,
    "max_images":1,"max_image_bytes":16*1024**2,"max_image_pixels":12_000_000,
    "image_longest_edge":512}
IMAGE_KEYS={"max_images","max_image_bytes","max_image_pixels","image_longest_edge"}


def _image_items(row):
    if "images" in row:
        return row["images"]
    paths=row.get("image_paths")
    if not isinstance(paths,list) or any(not isinstance(path,str) for path in paths):
        raise ValueError("images_or_image_paths_required")
    return [{"local_path":path,"kind":"image"} for path in paths]


def options_for(settings):
    opts={**DEFAULTS,**(settings or {})}
    if set(opts)!=set(DEFAULTS):
        raise ValueError("unknown_vision_setting")
    validate_settings({k:v for k,v in opts.items() if k not in IMAGE_KEYS})
    if type(opts["max_images"]) is not int or not 1<=opts["max_images"]<=2:
        raise ValueError("one_or_two_still_images_supported")
    for key,maximum in (("max_image_bytes",32*1024**2),("max_image_pixels",12_000_000)):
        if type(opts[key]) is not int or not 1<=opts[key]<=maximum:
            raise ValueError("invalid_image_guard_"+key)
    if opts["image_longest_edge"]!=512:
        raise ValueError("this_minimal_diagnostic_uses_native_512_pixel_patch")
    return opts


def image_identity(cases,train_examples,opts,asset_root):
    """Bounded identity preparation, no Pillow decoding before registry begin."""
    if not 1<=len(cases)<=8 or not 1<=len(train_examples)<=32:
        raise ValueError("vision_diagnostic_requires_bounded_nonempty_examples")
    base=Path(asset_root).resolve()
    identities={}
    for row in cases+train_examples:
        images=_image_items(row)
        if not isinstance(images,list) or not 1<=len(images)<=opts["max_images"]:
            raise ValueError("native_image_input_required")
        for item in images:
            if item.get("kind","image") not in {"image","selected_video_frame"}:
                raise ValueError("audio_and_native_video_not_supported")
            if item.get("kind")=="selected_video_frame" and not isinstance(item.get("frame_at_seconds"),(int,float)):
                raise ValueError("selected_video_frame_requires_timestamp")
            path=Path(item["local_path"])
            path=(base/path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_relative_to(base) or not path.is_file() or path.stat().st_size>opts["max_image_bytes"]:
                raise ValueError("image_path_or_byte_limit")
            if str(path) not in identities:
                identities[str(path)]={"path":str(path),"sha256":sha_file(path),"bytes":path.stat().st_size}
            if item.get("sha256") and item["sha256"]!=identities[str(path)]["sha256"]:
                raise ValueError("declared_image_hash_mismatch")
    return identities


def run_worker(request_path):
    request=json.loads(Path(request_path).read_text(encoding="utf8"))
    result=execute(**request)
    return 0 if result["status"]=="completed" else 2


def execute(*,data,settings,deps_path,output_dir,registered_run_id,identity,images):
    opts=options_for(settings)
    destination=Path(output_dir)
    destination.mkdir(parents=True,exist_ok=False)
    start=time.perf_counter()
    manifest={"run_id":registered_run_id,"schema_version":"creation-vision-diagnostic-v1",
        "scope":"developer_native_image_diagnostic_not_main_study","status":"started",
        "settings":opts,"model_identity":identity,"registered_images":images,"responses":[],"training_steps":[],
        "optimizer_steps":0,"generation_calls":0,"vision_forward_calls":0,"failures":[],
        "native_inputs":["image","text"],"output_modality":"text","audio_supported":False,
        "video_input_scope":"explicit_selected_still_frames_only","image_generation_supported":False,
        "quality_verified":False,"paid_api_calls":0,"network_requests":0,"human_review_minutes":0,
        "local_compute_cost":None,"peak_rss_bytes":0,"peak_cuda_allocated_bytes":0}
    torch=None
    process=None
    def event(kind,**fields):
        with (destination/"events.jsonl").open("a",encoding="utf8") as handle:
            handle.write(json.dumps({"event":kind,"elapsed_seconds":round(time.perf_counter()-start,6),**fields},ensure_ascii=False,allow_nan=False)+"\n")
    def guard():
        if time.perf_counter()-start>opts["max_wall_seconds"]:
            raise ResourceLimitError("vision_worker_wall_limit")
        if process:
            rss=process.memory_info().rss
            manifest["peak_rss_bytes"]=max(manifest["peak_rss_bytes"],rss)
            if rss>opts["max_rss_mb"]*1024**2:
                raise ResourceLimitError("vision_worker_rss_limit")
        if torch is not None and opts["device"]=="cuda" and torch.cuda.is_initialized():
            manifest["peak_cuda_allocated_bytes"]=max(manifest["peak_cuda_allocated_bytes"],torch.cuda.max_memory_allocated())
            if torch.cuda.memory_allocated()>opts["max_cuda_allocated_mb"]*1024**2:
                raise ResourceLimitError("vision_cuda_limit")
    try:
        deps=Path(deps_path).resolve()
        if not (deps/"transformers").is_dir() or not (deps/"tokenizers").is_dir():
            raise ValueError("isolated_transformers_and_tokenizers_missing")
        if any(name in sys.modules for name in ("transformers","tokenizers")):
            raise ValueError("fresh_worker_required_before_isolated_import")
        sys.path.insert(0,str(deps))
        event("before_isolated_import")
        import psutil
        process=psutil.Process()
        import torch as loaded_torch
        torch=loaded_torch
        import transformers
        import tokenizers
        for module,version in ((transformers,"4.49.0"),(tokenizers,"0.21.0")):
            if module.__version__!=version or not Path(module.__file__).resolve().is_relative_to(deps):
                raise ValueError("isolated_dependency_version_or_path_mismatch")
        from transformers import AutoProcessor,AutoModelForVision2Seq,StoppingCriteria,StoppingCriteriaList
        from PIL import Image
        from safetensors.torch import save_file
        import warnings
        Image.MAX_IMAGE_PIXELS=opts["max_image_pixels"]
        warnings.simplefilter("error",Image.DecompressionBombWarning)
        torch.set_num_threads(opts["cpu_threads"])
        torch.manual_seed(opts["seed"])
        if opts["device"]=="cuda":
            if not torch.cuda.is_available():
                raise ResourceLimitError("cuda_unavailable_no_automatic_cpu_retry")
            torch.cuda.manual_seed_all(opts["seed"])
            torch.cuda.reset_peak_memory_stats()
        manifest["versions"]={name:importlib.metadata.version(name) for name in ("torch","torchvision","transformers","tokenizers","Pillow","safetensors")}
        manifest["dependency_paths"]={"transformers":transformers.__file__,"tokenizers":tokenizers.__file__}
        guard()
        processor=AutoProcessor.from_pretrained(data["model_path"],local_files_only=True,trust_remote_code=False)
        tokenizer=processor.tokenizer
        if not tokenizer.eos_token_id:
            raise ValueError("image_tokenizer_eos_required")
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token=tokenizer.eos_token
        base=Path(data.get("asset_root",ROOT)).resolve()

        def encode(row,prompt):
            if not isinstance(prompt,str) or not prompt or len(prompt)>20_000:
                raise ValueError("invalid_vision_prompt")
            pictures=[]
            asset_records=[]
            for item in _image_items(row):
                path=Path(item["local_path"])
                path=(base/path).resolve() if not path.is_absolute() else path.resolve()
                if not path.is_relative_to(base) or str(path) not in images or sha_file(path)!=images[str(path)]["sha256"]:
                    raise ValueError("image_bytes_changed_after_registration")
                with Image.open(path) as original:
                    if original.width*original.height>opts["max_image_pixels"]:
                        raise ValueError("image_pixel_limit")
                    original.seek(0)
                    original.load()
                    pictures.append(original.convert("RGB"))
                    asset_records.append({**images[str(path)],"source_kind":item.get("kind","image"),
                        "source_frame_count":getattr(original,"n_frames",1),"selected_image_frame":0,
                        "source_width":original.width,"source_height":original.height})
            messages=[{"role":"user","content":[{"type":"image"} for _ in pictures]+[{"type":"text","text":prompt}]}]
            rendered=processor.apply_chat_template(messages,add_generation_prompt=True,tokenize=False)
            batch=processor(text=rendered,images=pictures,return_tensors="pt",do_image_splitting=False,size={"longest_edge":512})
            if "pixel_values" not in batch or "input_ids" not in batch:
                raise ValueError("processor_did_not_create_native_pixel_input")
            token_count=int(batch["input_ids"].shape[-1])
            if token_count>opts["max_input_tokens"]:
                raise ValueError(f"image_prompt_exceeds_budget_without_truncation:{token_count}")
            import hashlib
            pixels=batch["pixel_values"].contiguous()
            image_token_count=int((batch["input_ids"]==model.config.image_token_id).sum())
            if image_token_count!=processor.image_seq_len*len(pictures):
                raise ValueError("image_token_count_does_not_match_unsplit_images")
            record={"rendered_prompt":rendered,"input_tokens":token_count,"input_token_ids":batch["input_ids"][0].tolist(),
                "input_truncated":False,"truncation_policy":"reject_over_budget_preserve_image_tokens",
                "pixel_values_shape":list(pixels.shape),"pixel_values_sha256":hashlib.sha256(pixels.numpy().tobytes()).hexdigest(),
                "image_token_count":image_token_count,
                "images":asset_records,"preprocessing":{"do_image_splitting":False,"longest_edge":512,
                    "final_processor_shape":"512x512 square; aspect ratio may change","color":"RGB","source_files_saved_or_modified":False}}
            for key,tensor in batch.items():
                batch[key]=tensor.to(device=opts["device"],dtype=getattr(torch,opts["dtype"]) if tensor.is_floating_point() else tensor.dtype)
            return batch,record

        event("before_vision_model_load",revision=data["revision"])
        model=AutoModelForVision2Seq.from_pretrained(data["model_path"],local_files_only=True,trust_remote_code=False,
            use_safetensors=True,torch_dtype=getattr(torch,opts["dtype"]),low_cpu_mem_usage=True,
            device_map={"":opts["device"]},attn_implementation="eager")
        if model.config.model_type!="idefics3":
            raise ValueError("expected_idefics3_model")
        model.requires_grad_(False)
        language=model.get_submodule("model.text_model")
        modules=_install_lora(language,torch,opts["rank"],opts["alpha"])
        manifest["lora"]={"target_scope":"language_model_qv_only","rank":opts["rank"],"alpha":opts["alpha"],
            "modules":[name for name,_ in modules],"vision_encoder_frozen":True,"vision_projector_frozen":True,
            "conditioning":"actual_pixel_values_through_frozen_vision_encoder_and_projector"}
        def vision_hook(module,inputs,output):
            manifest["vision_forward_calls"]+=1
            if hasattr(output,"last_hidden_state"):
                manifest["last_vision_hidden_state_shape"]=list(output.last_hidden_state.shape)
        handle=model.get_submodule("model.vision_model").register_forward_hook(vision_hook)
        manifest["base_hash_before"]=_parameter_hash(model,False,torch)
        manifest["adapter_hash_before"]=_parameter_hash(model,True,torch)
        event("vision_model_loaded",base_hash=manifest["base_hash_before"],adapter_hash=manifest["adapter_hash_before"])
        guard()
        class Watchdog(StoppingCriteria):
            def __call__(self,input_ids,scores,**kwargs):
                guard()
                return False
        def generate(row,condition):
            model.eval()
            for _,module in modules:
                module.enabled=condition=="CF"
            prompt=row[condition.lower()+"_prompt"]
            batch,record=encode(row,prompt)
            record.update(case_id=row["case_id"],condition=condition,status="started",prompt=prompt,
                adapter_enabled=condition=="CF",vision_calls_before=manifest["vision_forward_calls"])
            manifest["responses"].append(record)
            event("generation_started",case_id=row["case_id"],condition=condition,input_tokens=record["input_tokens"],pixel_values_shape=record["pixel_values_shape"])
            tick=time.perf_counter()
            manifest["generation_calls"]+=1
            with torch.inference_mode():
                outputs=model.generate(**batch,max_new_tokens=opts["max_new_tokens"],do_sample=False,num_beams=1,use_cache=True,
                    pad_token_id=tokenizer.pad_token_id,eos_token_id=tokenizer.eos_token_id,
                    stopping_criteria=StoppingCriteriaList([Watchdog()]))
            ids=outputs[0,record["input_tokens"]:].tolist()
            record.update(status="completed",output=tokenizer.decode(ids,skip_special_tokens=True),output_tokens=len(ids),output_token_ids=ids,
                output_reached_token_limit=len(ids)>=opts["max_new_tokens"],wall_seconds=round(time.perf_counter()-tick,6),vision_calls_after=manifest["vision_forward_calls"])
            if record["vision_calls_after"]<=record["vision_calls_before"]:
                raise RuntimeError("native_vision_forward_not_observed")
            event("generation_completed",record=record)
            guard()
        for row in data["cases"]:
            generate(row,"C0")
            generate(row,"CR")
        model.train()
        model.config.use_cache=False
        for _,module in modules:
            module.enabled=True
        if opts["gradient_checkpointing"]:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant":False})
        trainable=[p for p in model.parameters() if p.requires_grad]
        optimizer=torch.optim.AdamW(trainable,lr=opts["learning_rate"],weight_decay=0)
        manifest["optimizer"]={"class":"torch.optim.AdamW","learning_rate":opts["learning_rate"],"weight_decay":0,
            "trainable_parameters":sum(p.numel() for p in trainable)}
        for step in range(opts["steps"]):
            guard()
            row=data["train_examples"][step%len(data["train_examples"])]
            batch,record=encode(row,row["prompt"])
            raw_target=tokenizer.encode(row["target"],add_special_tokens=False)+[tokenizer.eos_token_id]
            target=raw_target[:opts["max_target_tokens"]]
            if not target or record["input_tokens"]+len(target)>opts["max_sequence_tokens"]:
                raise ValueError("native_training_sequence_exceeds_budget_without_image_truncation")
            target_tensor=torch.tensor([target],device=opts["device"])
            prompt_ids=batch["input_ids"]
            batch["input_ids"]=torch.cat([prompt_ids,target_tensor],dim=1)
            batch["attention_mask"]=torch.ones_like(batch["input_ids"])
            labels=torch.cat([torch.full_like(prompt_ids,-100),target_tensor],dim=1)
            record.update(example_id=row["example_id"],step=step+1,target_tokens=len(target),target_token_ids=target,
                untruncated_target_tokens=len(raw_target),target_truncated=len(target)<len(raw_target),total_training_tokens=int(labels.shape[-1]))
            write_json(destination/f"training_input_{step+1:03d}.json",record)
            optimizer.zero_grad(set_to_none=True)
            vision_before=manifest["vision_forward_calls"]
            tick=time.perf_counter()
            output=model(**batch,labels=labels,use_cache=False)
            loss=output.loss
            if not torch.isfinite(loss):
                raise FloatingPointError("nonfinite_native_image_loss")
            loss.backward()
            norm=torch.nn.utils.clip_grad_norm_(trainable,1.0,error_if_nonfinite=True)
            optimizer.step()
            manifest["optimizer_steps"]+=1
            step_log={"step":step+1,"example_id":row["example_id"],"loss":float(loss.detach().float().cpu()),
                "gradient_norm_before_clip":float(norm.detach().float().cpu()),"wall_seconds":round(time.perf_counter()-tick,6),
                "vision_forward_calls":manifest["vision_forward_calls"]-vision_before,"total_training_tokens":record["total_training_tokens"]}
            manifest["training_steps"].append(step_log)
            event("optimizer_step_completed",**step_log)
            if step_log["vision_forward_calls"]<1:
                raise RuntimeError("training_image_forward_not_observed")
            del output,loss,batch,labels
            guard()
        optimizer.zero_grad(set_to_none=True)
        del optimizer
        model.gradient_checkpointing_disable()
        model.config.use_cache=True
        manifest["adapter_hash_after"]=_parameter_hash(model,True,torch)
        manifest["base_hash_after"]=_parameter_hash(model,False,torch)
        manifest["base_weights_unchanged"]=manifest["base_hash_before"]==manifest["base_hash_after"]
        manifest["adapter_weights_changed"]=manifest["adapter_hash_before"]!=manifest["adapter_hash_after"]
        if not manifest["base_weights_unchanged"] or not manifest["adapter_weights_changed"]:
            raise RuntimeError("native_optimizer_hash_invariant_failed")
        adapters={n:p.detach().cpu().contiguous() for n,p in model.named_parameters() if n.endswith((".lora_A",".lora_B"))}
        save_file(adapters,str(destination/"adapter.safetensors"),metadata={"base_revision":data["revision"],"scope":"language_qv_native_image_conditioned"})
        manifest["adapter_file_sha256"]=sha_file(destination/"adapter.safetensors")
        write_json(destination/"adapter_config.json",{"base_revision":data["revision"],**manifest["lora"]})
        event("training_completed",adapter_sha256=manifest["adapter_file_sha256"],optimizer_steps=manifest["optimizer_steps"])
        for row in data["cases"]:
            generate(row,"CF")
        handle.remove()
        manifest["status"]="completed"
    except Exception as exc:
        manifest["status"]="failed"
        manifest["failures"].append({"type":type(exc).__name__,"message":str(exc)[:2000]})
        event("failed",failure=manifest["failures"][-1])
    finally:
        manifest["wall_seconds"]=round(time.perf_counter()-start,6)
        unchanged={}
        for path,record in images.items():
            try:
                unchanged[path]=sha_file(path)==record["sha256"]
            except OSError:
                unchanged[path]=False
        manifest["source_image_bytes_unchanged"]=unchanged
        if not all(unchanged.values()):
            manifest["status"]="failed"
            manifest["failures"].append({"type":"SourceChanged","message":"source image changed during diagnostic"})
        if process:
            manifest["peak_rss_bytes"]=max(manifest["peak_rss_bytes"],process.memory_info().rss)
        if torch is not None and opts["device"]=="cuda" and torch.cuda.is_initialized():
            manifest["peak_cuda_allocated_bytes"]=max(manifest["peak_cuda_allocated_bytes"],torch.cuda.max_memory_allocated())
        write_json(destination/"manifest.json",manifest)
    return manifest


def run_registered(spec_path,output_dir,db,deps_path,retry_of=None,retry_reason=None):
    source=Path(spec_path).resolve()
    if source.stat().st_size>4*1024**2:
        raise ValueError("native_spec_byte_limit")
    data=json.loads(source.read_text(encoding="utf-8-sig"))
    if data["revision"]!=REVISION:
        raise ValueError("this_diagnostic_requires_reviewed_smolvlm_revision")
    opts=options_for(data.get("settings"))
    identity=model_identity(data["model_path"],data["revision"])
    images=image_identity(data["cases"],data["train_examples"],opts,data.get("asset_root",ROOT))
    deps=Path(deps_path).resolve()
    dependency_hashes={str(p.relative_to(deps)):sha_file(p) for p in sorted(deps.glob("*.dist-info/METADATA"))}
    spec={"objective":"Actual isolated SmolVLM native-image C0 CR CF diagnostic and low-rank optimizer update",
        "stage":"model_experiment","parameters":{"settings":opts,"scope":"developer_native_image_diagnostic_not_main_study",
            "isolated_dependencies":dependency_hashes,"deps_path":str(deps)},
        "data_snapshots":[{"id":"input_spec","sha256":sha_file(source)},{"id":"model_snapshot","sha256":identity["snapshot_sha256"]}]+
            [{"id":f"native_image_{i}","sha256":r["sha256"]} for i,r in enumerate(images.values())],
        "code_hashes":{"vision_backend":sha_file(Path(__file__)),"shared_lora":sha_file(ROOT/"src/creation_model_backend.py"),
            "runner":sha_file(ROOT/"scripts/run_creation_vision_pilot.py")},"prompt_hashes":{},"config_hashes":{},
        "model_version":data["revision"],"modality":["text","image"],"provenance":data["provenance"]}
    log=ResearchLog(db)
    registered=log.begin(spec,retry_of=retry_of,retry_reason=retry_reason)
    destination=Path(output_dir).resolve()
    result={"run_id":registered["run_id"],"status":"failed","failures":[]}
    artifacts=[]
    started=time.perf_counter()
    try:
        destination.mkdir(parents=True,exist_ok=False)
        write_json(destination/"spec.json",data)
        request={"data":data,"settings":opts,"deps_path":str(deps),"output_dir":str(destination/"worker"),
            "registered_run_id":registered["run_id"],"identity":identity,"images":images}
        write_json(destination/"request.json",request)
        environment=dict(os.environ)
        environment.update(HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",HF_HUB_DISABLE_TELEMETRY="1",TOKENIZERS_PARALLELISM="false",PYTHONDONTWRITEBYTECODE="1")
        import psutil
        stop_reason=None
        peak_rss=0
        with (destination/"stdout.txt").open("x",encoding="utf8") as stdout,(destination/"stderr.txt").open("x",encoding="utf8") as stderr:
            proc=subprocess.Popen([sys.executable,"-X","utf8","-B",str(ROOT/"scripts/run_creation_vision_pilot.py"),"--worker",str(destination/"request.json")],
                stdout=stdout,stderr=stderr,env=environment,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            monitor=psutil.Process(proc.pid)
            while proc.poll() is None:
                try:
                    rss=monitor.memory_info().rss
                    peak_rss=max(peak_rss,rss)
                    if rss>opts["max_rss_mb"]*1024**2:
                        stop_reason="native_parent_rss_limit"
                except psutil.NoSuchProcess:
                    break
                if time.perf_counter()-started>opts["max_wall_seconds"]:
                    stop_reason="native_parent_wall_timeout"
                if stop_reason:
                    proc.kill()
                    break
                time.sleep(.5)
            proc.wait(timeout=10)
        path=destination/"worker/manifest.json"
        if path.exists():
            result=json.loads(path.read_text(encoding="utf8"))
        else:
            result["failures"].append({"type":"WorkerTermination","message":stop_reason or "no_native_manifest"})
            events=[]
            path=destination/"worker/events.jsonl"
            if path.exists():
                for line in path.read_text(encoding="utf8").splitlines():
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
            result["optimizer_steps"]=sum(e.get("event")=="optimizer_step_completed" for e in events)
            result["generation_calls"]=sum(e.get("event")=="generation_started" for e in events)
            result["training_steps"]=[e for e in events if e.get("event")=="optimizer_step_completed"]
            result["responses"]=[e["record"] for e in events if e.get("event")=="generation_completed"]
            result["recovered_counts_are_observed_lower_bounds"]=True
        if stop_reason or proc.returncode:
            result["status"]="failed"
        result["supervisor"]={"returncode":proc.returncode,"stop_reason":stop_reason,"sampled_peak_rss_bytes":peak_rss,"wall_seconds":round(time.perf_counter()-started,6)}
        write_json(destination/"run_summary.json",result)
        artifacts=[p for p in destination.rglob("*") if p.is_file()]
    except Exception as exc:
        result["status"]="failed"
        result["failures"].append({"type":type(exc).__name__,"message":str(exc)[:1600]})
    finally:
        log.finish(registered["run_id"],status="succeeded" if result["status"]=="completed" else "failed",
            notes=f"Native image diagnostic {result['status']}; observed optimizer_steps={result.get('optimizer_steps',0)}; generation_calls={result.get('generation_calls',0)}. Text output, no audio or native video stream, no quality validation.",
            artifacts=artifacts,actual_cost={"amount":None,"currency":None,"api_units":0,"human_minutes":0})
    print(json.dumps({"run_id":registered["run_id"],"status":result["status"],"optimizer_steps":result.get("optimizer_steps",0),
        "generation_calls":result.get("generation_calls",0),"failures":result.get("failures",[])},ensure_ascii=False))
    return 0 if result["status"]=="completed" else 2
