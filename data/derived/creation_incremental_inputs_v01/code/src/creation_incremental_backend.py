"""Incremental text proxy: verified prior LoRA plus actual additional updates.

No implicit downloads, model selection, retrieval, example creation, or CPU retry.
The caller must register the run before invoking run_experiment. Native image,
audio and video input are not supported by this deliberately bounded backend.
Optimizer state is deliberately reset: this is adapter continuation, not an
uninterrupted AdamW resume. The original creation_model_backend stays frozen.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import time

DEFAULTS = {
    "device": "cpu", "dtype": "float32", "steps": 2, "seed": 42,
    "rank": 4, "alpha": 8, "learning_rate": 0.001,
    "max_input_tokens": 256, "max_target_tokens": 64,
    "max_sequence_tokens": 256, "max_new_tokens": 64,
    "max_wall_seconds": 900, "max_rss_mb": 4096, "max_cuda_allocated_mb": 1800,
    "cpu_threads": 2, "gradient_checkpointing": True,
}


class ResourceLimitError(RuntimeError):
    pass


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def model_identity(model_path, revision):
    """Hash the actual small safetensors checkpoint and local tokenizer/config."""
    path = Path(model_path).resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("model_revision_must_be_full_commit_hash")
    if not path.is_dir():
        raise ValueError("local_model_directory_missing")
    weights = sorted(path.glob("*.safetensors"))
    if not weights or sum(p.stat().st_size for p in weights) > 1_300_000_000:
        raise ValueError("small_safetensors_checkpoint_required_max_1_3GB")
    files = weights + sorted(p for p in path.iterdir() if p.suffix in {".json", ".txt"} and p.is_file())
    if any(p.stat().st_size > 16 * 1024**2 for p in files if p not in weights):
        raise ValueError("model_metadata_file_too_large")
    records = [{"name": p.name, "bytes": p.stat().st_size, "sha256": sha_file(p)} for p in files]
    return {"path": str(path), "declared_revision": revision, "files": records,
            "snapshot_sha256": hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()}


def validate_settings(settings):
    options = {**DEFAULTS, **(settings or {})}
    if set(options) != set(DEFAULTS):
        raise ValueError("unknown_backend_setting")
    if options["device"] not in {"cuda", "cpu"} or options["dtype"] not in {"float16", "float32"}:
        raise ValueError("unsupported_device_or_dtype")
    if options["device"] == "cpu" and options["dtype"] != "float32":
        raise ValueError("cpu_requires_explicit_float32_run")
    bounds = {"steps":100,"rank":16,"alpha":64,"max_input_tokens":512,
        "max_target_tokens":128,"max_sequence_tokens":640,"max_new_tokens":128,
        "max_wall_seconds":900,"max_rss_mb":8192,"max_cuda_allocated_mb":8192,"cpu_threads":8}
    for key, maximum in bounds.items():
        if type(options[key]) is not int or not 1 <= options[key] <= maximum:
            raise ValueError("invalid_bounded_setting_" + key)
    if options["max_target_tokens"] >= options["max_sequence_tokens"]:
        raise ValueError("target_budget_exceeds_training_sequence")
    if type(options["seed"]) is not int or not 0 <= options["seed"] < 2**32:
        raise ValueError("invalid_seed")
    if not isinstance(options["gradient_checkpointing"], bool):
        raise ValueError("invalid_gradient_checkpointing")
    if not isinstance(options["learning_rate"], (int, float)) or not 0 < options["learning_rate"] <= .01:
        raise ValueError("invalid_learning_rate")
    return options


def checkpoint_identity(data, identity, options):
    """Read/hash bounded checkpoint metadata, without importing torch/models."""
    adapter=Path(data["initial_adapter_path"]).resolve()
    if adapter.suffix!=".safetensors" or not adapter.is_file() or adapter.stat().st_size>32*1024**2:
        raise ValueError("bounded_initial_safetensors_adapter_required")
    expected=data["initial_adapter_sha256"]
    if not isinstance(expected,str) or not re.fullmatch(r"[0-9a-f]{64}",expected):
        raise ValueError("initial_adapter_sha256_required")
    manifest=Path(data.get("prior_manifest_path",adapter.parent/"manifest.json")).resolve()
    config=Path(data.get("initial_adapter_config_path",adapter.parent/"adapter_config.json")).resolve()
    for path in (manifest,config):
        if not path.is_file() or path.stat().st_size>4*1024**2:
            raise ValueError("bounded_prior_manifest_and_adapter_config_required")
    prior=json.loads(manifest.read_text(encoding="utf8"))
    adapter_config=json.loads(config.read_text(encoding="utf8"))
    measured=sha_file(adapter)
    if measured!=expected or measured!=prior.get("adapter_file_sha256"):
        raise ValueError("initial_adapter_file_hash_mismatch")
    if prior.get("run_id")!=data["prior_run_id"] or prior.get("status")!="completed" or prior.get("optimizer_steps",0)<1:
        raise ValueError("prior_completed_training_run_required")
    if prior.get("declared_revision")!=data["revision"] or adapter_config.get("base_revision")!=data["revision"]:
        raise ValueError("prior_base_revision_mismatch")
    if prior.get("model_identity",{}).get("snapshot_sha256")!=identity["snapshot_sha256"] or adapter_config.get("base_snapshot_sha256")!=identity["snapshot_sha256"]:
        raise ValueError("prior_base_checkpoint_file_identity_mismatch")
    if prior.get("base_hash_before")!=prior.get("base_hash_after") or not prior.get("base_hash_after",{}).get("sha256"):
        raise ValueError("prior_base_frozen_hash_evidence_required")
    if not prior.get("adapter_hash_after",{}).get("sha256"):
        raise ValueError("prior_final_adapter_tensor_hash_required")
    if options["dtype"]!="float32" or prior.get("settings",{}).get("dtype")!="float32":
        raise ValueError("this_incremental_diagnostic_requires_same_fp32_base")
    for key in ("rank","alpha"):
        if adapter_config.get(key)!=options[key]:
            raise ValueError("prior_lora_configuration_mismatch_"+key)
    if adapter_config.get("implementation")!="local_qv_low_rank_delta_v1" or adapter_config.get("adapter_dtype")!="float32":
        raise ValueError("unsupported_prior_adapter_implementation")
    return {"prior_run_id":data["prior_run_id"],"adapter_path":str(adapter),"adapter_file_sha256":measured,
        "prior_manifest_path":str(manifest),"prior_manifest_sha256":sha_file(manifest),
        "adapter_config_path":str(config),"adapter_config_sha256":sha_file(config),
        "expected_initial_adapter_hash":prior["adapter_hash_after"],"expected_base_hash":prior["base_hash_after"],
        "expected_target_modules":adapter_config["target_modules"],
        "previous_optimizer_steps":prior["optimizer_steps"],"optimizer_reset_at_update":True,
        "optimizer_state_loaded":False,"continuous_optimizer_resume":False}


def _load_initial_adapter(model, checkpoint, torch):
    """Strict safetensors keys/shapes/dtypes; no pickle or partial state load."""
    from safetensors.torch import load_file
    path=Path(checkpoint["adapter_path"])
    if sha_file(path)!=checkpoint["adapter_file_sha256"]:
        raise ValueError("initial_adapter_changed_after_registration")
    saved=load_file(str(path),device="cpu")
    expected={name:p for name,p in model.named_parameters() if name.endswith((".lora_A",".lora_B"))}
    if set(saved)!=set(expected):
        raise ValueError("initial_adapter_full_key_set_mismatch")
    for name,tensor in saved.items():
        if tensor.shape!=expected[name].shape or tensor.dtype!=torch.float32:
            raise ValueError("initial_adapter_shape_or_dtype_mismatch")
        if not torch.isfinite(tensor).all():
            raise ValueError("initial_adapter_nonfinite_values")
    with torch.no_grad():
        for name,tensor in saved.items():
            expected[name].copy_(tensor.to(expected[name].device))
    return {"strict_key_count":len(expected),"all_shapes_match":True,"all_dtypes_float32":True,
            "all_values_finite":True,"serialization":"safetensors_only"}


def _install_lora(model, torch, rank, alpha):
    """Conventional delta W=(alpha/r)*B*A; FP32 adapters, frozen base."""
    class LowRankLinear(torch.nn.Module):
        def __init__(self, base):
            super().__init__()
            self.base = base
            self.enabled = False
            self.scale = alpha / rank
            self.lora_A = torch.nn.Parameter(torch.empty(rank, base.in_features, device=base.weight.device, dtype=torch.float32))
            self.lora_B = torch.nn.Parameter(torch.zeros(base.out_features, rank, device=base.weight.device, dtype=torch.float32))
            torch.nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))

        def forward(self, inputs):
            output = self.base(inputs)
            if self.enabled:
                update = torch.nn.functional.linear(torch.nn.functional.linear(inputs.float(), self.lora_A), self.lora_B)
                output = output + (update * self.scale).to(output.dtype)
            return output

    model.requires_grad_(False)
    replaced = []
    for name, module in list(model.named_modules()):
        if name.rsplit(".",1)[-1] in {"q_proj","v_proj"} and isinstance(module, torch.nn.Linear):
            parent_name, attr = name.rsplit(".", 1)
            replacement = LowRankLinear(module)
            setattr(model.get_submodule(parent_name), attr, replacement)
            replaced.append((name, replacement))
    if not replaced:
        raise ValueError("no_supported_qv_projection_modules")
    return replaced


def _parameter_hash(model, adapter, torch):
    """Full value hash, in bounded CPU copies; not a few sampled parameters."""
    h, count = hashlib.sha256(), 0
    for name, parameter in model.named_parameters():
        is_adapter = name.endswith((".lora_A", ".lora_B"))
        if is_adapter != adapter:
            continue
        h.update(json.dumps([name, list(parameter.shape), str(parameter.dtype)]).encode())
        flat = parameter.detach().reshape(-1)
        for start in range(0, flat.numel(), 1_000_000):
            block = flat[start:start+1_000_000].to(device="cpu").contiguous()
            h.update(block.view(torch.uint8).numpy().tobytes())
        count += parameter.numel()
    return {"sha256":h.hexdigest(), "parameters":count}


def run_experiment(*, model_path, revision, cases, train_examples, output_dir,
                   settings=None, registered_run_id, identity=None, initial_checkpoint):
    """Actual execution. The caller owns evidence, split validity and registry.

    cases: [{case_id,c0_prompt,cr_prompt,cf_prompt}]
    train_examples: [{example_id,prompt,target}]
    Outputs are raw model text. No generated response is a validated meme score.
    """
    options = validate_settings(settings)
    if not registered_run_id:
        raise ValueError("execution_requires_registered_run_id")
    if not 1 <= len(cases) <= 32 or not 1 <= len(train_examples) <= 64:
        raise ValueError("bounded_nonempty_cases_and_training_examples_required")
    for rows, fields in ((cases, ("case_id","c0_prompt","cr_prompt","cf_prompt")),
                         (train_examples,("example_id","prompt","target"))):
        for row in rows:
            if any(not isinstance(row.get(key),str) or not row[key] or len(row[key]) > 100_000 for key in fields):
                raise ValueError("invalid_case_or_training_example")
    path = Path(model_path).resolve()
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    manifest = {"schema_version":"creation-incremental-backend-v1", "run_id":registered_run_id,
        "scope":"developer_local_text_incremental_update_diagnostic", "status":"started", "settings":options,
        "initial_checkpoint":initial_checkpoint,"prior_run_id":initial_checkpoint["prior_run_id"],
        "optimizer_reset_at_update":True,"optimizer_state_loaded":False,"continuous_optimizer_resume":False,
        "model_identity":identity or model_identity(path,revision), "declared_revision":revision,
        "native_inputs":["text"], "native_output":"text", "quality_verified":False,
        "network_requests":0,"paid_api_calls":0,"human_review_minutes":0,
        "optimizer_steps":0,"generation_calls":0,"responses":[],"training_steps":[],
        "failures":[],"local_compute_cost":None,"peak_rss_bytes":0,
        "peak_cuda_allocated_bytes":0, "base_weights_updated":False}
    write_json(destination / "input.json", {"cases":cases,"train_examples":train_examples})
    event_path = destination / "events.jsonl"

    def event(kind, **fields):
        with event_path.open("a",encoding="utf8") as handle:
            handle.write(json.dumps({"event":kind,"elapsed_seconds":round(time.perf_counter()-started,6),**fields},ensure_ascii=False,allow_nan=False)+"\n")

    torch = None
    process = None
    def guard():
        if process is not None:
            rss = process.memory_info().rss
            manifest["peak_rss_bytes"] = max(manifest["peak_rss_bytes"],rss)
            if rss > options["max_rss_mb"] * 1024**2:
                raise ResourceLimitError("worker_rss_limit")
        if torch is not None and options["device"] == "cuda" and torch.cuda.is_initialized():
            allocated = torch.cuda.memory_allocated()
            manifest["peak_cuda_allocated_bytes"] = max(manifest["peak_cuda_allocated_bytes"],torch.cuda.max_memory_allocated())
            if allocated > options["max_cuda_allocated_mb"] * 1024**2:
                raise ResourceLimitError("cuda_allocated_limit")
        if time.perf_counter()-started > options["max_wall_seconds"]:
            raise ResourceLimitError("worker_wall_limit")

    model = None
    modules = None
    try:
        event("before_model_import", revision=revision)
        import psutil
        process = psutil.Process()
        import torch as loaded_torch
        torch = loaded_torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, StoppingCriteria, StoppingCriteriaList
        from safetensors.torch import save_file
        manifest["versions"] = {name:importlib.metadata.version(name) for name in ("torch","transformers","safetensors","accelerate","tokenizers")}
        torch.set_num_threads(options["cpu_threads"])
        torch.manual_seed(options["seed"])
        if options["device"] == "cuda":
            if not torch.cuda.is_available():
                raise ResourceLimitError("cuda_unavailable_no_automatic_cpu_retry")
            torch.cuda.manual_seed_all(options["seed"])
            torch.cuda.reset_peak_memory_stats()
            manifest["gpu"] = {"name":torch.cuda.get_device_name(0),"total_bytes":torch.cuda.get_device_properties(0).total_memory}
        guard()
        config = json.loads((path / "config.json").read_text(encoding="utf8"))
        if config.get("model_type") != "qwen2":
            raise ValueError("this_proxy_backend_requires_qwen2")
        if int(config.get("max_position_embeddings",0)) < max(options["max_sequence_tokens"],options["max_input_tokens"]+options["max_new_tokens"]):
            raise ValueError("configured_token_bounds_exceed_model_context")
        tokenizer = AutoTokenizer.from_pretrained(str(path),local_files_only=True,trust_remote_code=False)
        if tokenizer.eos_token_id is None:
            raise ValueError("tokenizer_eos_required")
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        event("before_model_load")
        model = AutoModelForCausalLM.from_pretrained(str(path),local_files_only=True,trust_remote_code=False,
            use_safetensors=True,torch_dtype=getattr(torch,options["dtype"]),low_cpu_mem_usage=True,
            device_map={"":options["device"]},attn_implementation="eager")
        guard()
        modules = _install_lora(model,torch,options["rank"],options["alpha"])
        if [name for name,_ in modules]!=initial_checkpoint["expected_target_modules"]:
            raise ValueError("initial_adapter_target_modules_mismatch")
        manifest["lora"] = {"implementation":"local_qv_low_rank_delta_v1","rank":options["rank"],"alpha":options["alpha"],
            "target_modules":[name for name,_ in modules],"adapter_dtype":"float32","dropout":0,
            "base_frozen":all(not p.requires_grad for n,p in model.named_parameters() if not n.endswith((".lora_A",".lora_B")))}
        manifest["base_hash_before"] = _parameter_hash(model,False,torch)
        if manifest["base_hash_before"]!=initial_checkpoint["expected_base_hash"]:
            raise ValueError("loaded_fp32_base_does_not_match_prior_final_base_hash")
        manifest["initial_adapter_load"]=_load_initial_adapter(model,initial_checkpoint,torch)
        manifest["adapter_hash_before"] = _parameter_hash(model,True,torch)
        manifest["prior_final_adapter_equals_initial_adapter"]=(manifest["adapter_hash_before"]==initial_checkpoint["expected_initial_adapter_hash"])
        if not manifest["prior_final_adapter_equals_initial_adapter"]:
            raise ValueError("loaded_initial_adapter_does_not_match_prior_final_tensor_hash")
        event("prior_adapter_loaded",prior_run_id=initial_checkpoint["prior_run_id"],
            initial_adapter_hash=manifest["adapter_hash_before"],base_hash=manifest["base_hash_before"],optimizer_reset_at_update=True)
        event("model_loaded",base_hash=manifest["base_hash_before"],adapter_hash=manifest["adapter_hash_before"])
        guard()

        def source_tokens(prompt, budget):
            rendered = tokenizer.apply_chat_template([{"role":"user","content":prompt}],tokenize=False,add_generation_prompt=True)
            ids = tokenizer.encode(rendered,add_special_tokens=False)
            used = ids[-budget:]
            return used,{"rendered_prompt":rendered,"untruncated_input_tokens":len(ids),"input_tokens":len(used),
                "input_truncated":len(ids)>len(used),"truncation_side":"left","input_token_ids":used}

        class Watchdog(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                guard()
                return False

        def generate(case, condition):
            guard()
            model.eval()
            for _, module in modules:
                module.enabled = condition == "CF"
            prompt = case[condition.lower()+"_prompt"]
            ids, token_record = source_tokens(prompt,options["max_input_tokens"])
            record = {"case_id":case["case_id"],"condition":condition,"status":"started","prompt":prompt,
                "adapter_enabled":condition=="CF",**token_record}
            manifest["responses"].append(record)
            event("generation_started",case_id=case["case_id"],condition=condition,input_tokens=len(ids))
            tensor = torch.tensor([ids],device=options["device"])
            tick = time.perf_counter()
            manifest["generation_calls"] += 1
            with torch.inference_mode():
                generated = model.generate(input_ids=tensor,attention_mask=torch.ones_like(tensor),
                    max_new_tokens=options["max_new_tokens"],do_sample=False,num_beams=1,use_cache=True,
                    pad_token_id=tokenizer.pad_token_id,eos_token_id=tokenizer.eos_token_id,
                    stopping_criteria=StoppingCriteriaList([Watchdog()]))
            output_ids = generated[0,len(ids):].tolist()
            record.update(status="completed",output=tokenizer.decode(output_ids,skip_special_tokens=True),
                output_token_ids=output_ids,output_tokens=len(output_ids),
                output_reached_token_limit=len(output_ids)>=options["max_new_tokens"],
                wall_seconds=round(time.perf_counter()-tick,6))
            event("generation_completed",case_id=case["case_id"],condition=condition,record=record)
            guard()

        for case in cases:
            generate(case,"C0")
            generate(case,"CR")

        prepared = []
        for row in train_examples:
            target_raw = tokenizer.encode(row["target"],add_special_tokens=False)
            if not target_raw or target_raw[-1] != tokenizer.eos_token_id:
                target_raw.append(tokenizer.eos_token_id)
            target = target_raw[:options["max_target_tokens"]]
            budget = min(options["max_input_tokens"],options["max_sequence_tokens"]-len(target))
            source, record = source_tokens(row["prompt"],budget)
            prepared.append({"example_id":row["example_id"],"input_ids":source+target,
                "labels":[-100]*len(source)+target,"target_tokens":len(target),
                "untruncated_target_tokens":len(target_raw),"target_truncated":len(target)<len(target_raw),**record})
        write_json(destination / "training_tokenization.json",prepared)
        for _, module in modules:
            module.enabled=True
        model.train()
        model.config.use_cache=False
        if options["gradient_checkpointing"]:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant":False})
            manifest["gradient_checkpointing_enabled"] = True
        else:
            manifest["gradient_checkpointing_enabled"] = False
        parameters = [p for p in model.parameters() if p.requires_grad]
        optimizer = torch.optim.AdamW(parameters,lr=options["learning_rate"],weight_decay=0)
        manifest["optimizer"]={"class":"torch.optim.AdamW","learning_rate":options["learning_rate"],"weight_decay":0,"trainable_parameters":sum(p.numel() for p in parameters),
            "optimizer_reset_at_update":True,"initial_state_entries":len(optimizer.state),"prior_state_loaded":False}
        event("fresh_optimizer_created",initial_state_entries=len(optimizer.state),prior_state_loaded=False)
        for step in range(options["steps"]):
            guard()
            row=prepared[step%len(prepared)]
            tensor=torch.tensor([row["input_ids"]],device=options["device"])
            labels=torch.tensor([row["labels"]],device=options["device"])
            optimizer.zero_grad(set_to_none=True)
            tick=time.perf_counter()
            outputs=model(input_ids=tensor,attention_mask=torch.ones_like(tensor),labels=labels,use_cache=False)
            loss=outputs.loss
            if not torch.isfinite(loss):
                raise FloatingPointError("nonfinite_training_loss")
            loss.backward()
            grad_norm=torch.nn.utils.clip_grad_norm_(parameters,1.0,error_if_nonfinite=True)
            optimizer.step()
            manifest["optimizer_steps"] += 1
            row_log={"step":step+1,"example_id":row["example_id"],"loss":float(loss.detach().float().cpu()),
                "gradient_norm_before_clip":float(grad_norm.detach().float().cpu()),"input_tokens":len(row["input_ids"]),
                "supervised_target_tokens":row["target_tokens"],"wall_seconds":round(time.perf_counter()-tick,6)}
            manifest["training_steps"].append(row_log)
            event("optimizer_step_completed",**row_log)
            del outputs,loss,tensor,labels
            guard()
        optimizer.zero_grad(set_to_none=True)
        del optimizer
        model.gradient_checkpointing_disable()
        model.config.use_cache=True
        manifest["adapter_hash_after"]=_parameter_hash(model,True,torch)
        manifest["base_hash_after"]=_parameter_hash(model,False,torch)
        manifest["base_weights_updated"]=manifest["base_hash_before"]!=manifest["base_hash_after"]
        manifest["adapter_weights_changed"]=manifest["adapter_hash_before"]!=manifest["adapter_hash_after"]
        if manifest["base_weights_updated"] or not manifest["adapter_weights_changed"]:
            raise RuntimeError("optimizer_weight_identity_invariant_failed")
        adapter={name:p.detach().cpu().contiguous() for name,p in model.named_parameters() if name.endswith((".lora_A",".lora_B"))}
        save_file(adapter,str(destination/"adapter.safetensors"),metadata={"base_revision":revision,"implementation":"local_qv_low_rank_delta_v1",
            "prior_run_id":initial_checkpoint["prior_run_id"],"initial_adapter_sha256":initial_checkpoint["adapter_file_sha256"],"optimizer_reset_at_update":"true"})
        manifest["adapter_file_sha256"]=sha_file(destination/"adapter.safetensors")
        write_json(destination/"adapter_config.json",{"base_revision":revision,"base_snapshot_sha256":manifest["model_identity"]["snapshot_sha256"],
            "prior_run_id":initial_checkpoint["prior_run_id"],"initial_adapter_file_sha256":initial_checkpoint["adapter_file_sha256"],
            "optimizer_reset_at_update":True,**manifest["lora"]})
        event("training_completed",optimizer_steps=manifest["optimizer_steps"],adapter_sha256=manifest["adapter_file_sha256"],base_unchanged=not manifest["base_weights_updated"])
        for case in cases:
            generate(case,"CF")
        manifest["status"]="completed"
    except Exception as exc:
        manifest["status"]="failed"
        manifest["failures"].append({"type":type(exc).__name__,"message":str(exc)[:1600]})
        event("failed",failure=manifest["failures"][-1])
        # Intermediate optimizer events survive timeout/crash. No successful FT
        # claim is made unless both final hashes were actually measured.
    finally:
        manifest["wall_seconds"]=round(time.perf_counter()-started,6)
        if process is not None:
            manifest["peak_rss_bytes"]=max(manifest["peak_rss_bytes"],process.memory_info().rss)
        if torch is not None and options["device"]=="cuda" and torch.cuda.is_initialized():
            manifest["peak_cuda_allocated_bytes"]=max(manifest["peak_cuda_allocated_bytes"],torch.cuda.max_memory_allocated())
        write_json(destination/"manifest.json",manifest)
    return manifest
