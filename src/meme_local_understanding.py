"""Bounded, logged U0/U1 understanding using an already cached local model.

No model download, remote code, paid API, training, audio, or video processing.
U0 receives selected source excerpts. U1 receives exactly the same excerpts plus
explicitly marked automatic-rule knowledge. Responses are generated hypotheses,
not gold annotations, semantic accuracy measurements, or human review.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from research_log import DuplicateRunError, ResearchLog, hash_file, read_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = ROOT / "prompts" / "meme_local_understanding_v01.txt"
AUTOMATIC_MARKERS = {"automatic_rule", "automatic_rules", "rule_based", "automatically_structured",
                     "automatic_rule_derived_interpretation"}
SOURCE_FIELDS = ("observation_id", "video_id", "title", "description", "channel_id", "source_url", "posted_at", "available_at")


def stamp():
    return datetime.now(timezone.utc).isoformat()


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("source_available_time_missing")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("source_available_time_not_aware")
    return result.astimezone(timezone.utc)


def _write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _automatic_knowledge(family, result, interpretation_at=None):
    knowledge = family.get("knowledge")
    if not isinstance(knowledge, dict):
        return None, "automatic_knowledge_missing"
    marker = knowledge.get("provenance") or knowledge.get("generation_method") or knowledge.get("method")
    if marker not in AUTOMATIC_MARKERS:
        marker = result.get("knowledge_provenance")
    if marker not in AUTOMATIC_MARKERS:
        return None, "knowledge_not_explicitly_automatic_rule"
    if knowledge.get("human_reviewed") or knowledge.get("manual_correction") or knowledge.get("gold"):
        return None, "human_or_gold_knowledge_excluded"
    if interpretation_at is not None:
        try:
            if _utc(knowledge.get("available_at")) > _utc(interpretation_at):
                return None, "knowledge_not_available_at_interpretation"
        except (ValueError, TypeError):
            return None, "knowledge_available_at_missing_or_invalid"
    selected = {key: knowledge[key] for key in ("meaning", "usage", "confidence", "evidence_ids", "unknowns", "pattern", "slots", "rule") if key in knowledge}
    if not selected:
        return None, "automatic_knowledge_empty"
    selected["provenance"] = "automatic_rule"
    return selected, None


def select_examples(family, interpretation_at, max_examples=5):
    """Choose one common source set, with explicit availability and truncation notes."""
    examples = family.get("examples")
    if not isinstance(examples, list):
        raise ValueError("family_examples_missing")
    selected, excluded, seen = [], [], set()
    cutoff = _utc(interpretation_at)
    for row in examples:
        evidence_id = row.get("observation_id") if isinstance(row, dict) else None
        reason = None
        if not isinstance(row, dict) or not isinstance(evidence_id, str) or not evidence_id:
            reason = "invalid_example"
        elif evidence_id in seen:
            reason = "duplicate_observation"
        else:
            try:
                if _utc(row.get("available_at")) > cutoff:
                    reason = "not_available_at_interpretation"
            except (ValueError, TypeError):
                reason = "available_at_missing_or_invalid"
        if not reason and len(selected) >= max_examples:
            reason = "local_example_cap"
        if reason:
            excluded.append({"observation_id": evidence_id, "reason": reason})
            continue
        seen.add(evidence_id)
        clean = {key: row.get(key) for key in SOURCE_FIELDS}
        truncations = {}
        for field, limit in (("title", 300), ("description", 1000)):
            text = clean[field]
            if text is not None and not isinstance(text, str):
                raise ValueError("source_text_must_be_string")
            if text and len(text) > limit:
                clean[field] = text[:limit]
                truncations[field] = {"original_characters": len(text), "included_characters": limit}
        if truncations:
            clean["excerpt_truncations"] = truncations
        selected.append(clean)
    if not selected:
        raise ValueError("no_available_source_examples")
    return selected, excluded


def build_messages(system_prompt, family_id, examples, condition, knowledge=None):
    if condition not in {"U0", "U1"}:
        raise ValueError("unknown_understanding_condition")
    payload = {"family_id": family_id, "source_excerpts": examples}
    if condition == "U1":
        if not isinstance(knowledge, dict):
            raise ValueError("U1_requires_automatic_knowledge")
        payload["automatic_structure"] = knowledge
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": _json(payload)}]


def _model_identity(model_path):
    model_path = Path(model_path).resolve(strict=True)
    required = ("config.json", "tokenizer_config.json", "tokenizer.json")
    if not model_path.is_dir() or any(not (model_path / name).is_file() for name in required):
        raise ValueError("incomplete_local_model")
    weights = sorted(model_path.glob("*.safetensors"))
    if not weights:
        raise ValueError("local_safetensors_required")
    paths = sorted({*(model_path / name for name in required), *weights,
                    *(p for p in model_path.glob("*.json") if p.is_file())})
    return {"path": str(model_path), "snapshot_name": model_path.name,
            "files": [{"name": p.name, "bytes": p.stat().st_size, "modified_ns": p.stat().st_mtime_ns,
                       "sha256": None if p.suffix == ".safetensors" else hash_file(p)} for p in paths],
            "weight_hashes": "deferred_until_resource_preflight_passes",
            "config": read_json(model_path / "config.json")}


def memory_preflight(identity, dtype, max_input_tokens, max_new_tokens):
    """A conservative local memory estimate, without importing/loading model weights."""
    try:
        import psutil
        memory = psutil.virtual_memory()
        available, total = memory.available, memory.total
    except ImportError:
        if os.name != "nt":
            return {"status": "memory_capacity_unknown", "checked_at": stamp()}
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended", ctypes.c_ulonglong)]
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            return {"status": "memory_capacity_unknown", "checked_at": stamp()}
        available, total = memory.avail_phys, memory.total_phys
    config = identity["config"]
    stored_bytes = sum(row["bytes"] for row in identity["files"] if row["name"].endswith(".safetensors"))
    stored_width = 4 if config.get("torch_dtype") == "float32" else 2
    target_width = 4 if dtype == "float32" else 2
    weight_estimate = (stored_bytes * target_width + stored_width - 1) // stored_width
    kv_estimate = (2 * int(config.get("num_hidden_layers", 32)) *
                   int(config.get("hidden_size", 2560)) * (max_input_tokens + max_new_tokens) * target_width)
    reserve = 2 * 1024 ** 3
    required = weight_estimate + kv_estimate + reserve
    return {"status": "sufficient_estimated_memory" if available >= required else "blocked_local_resources",
        "checked_at": stamp(), "available_bytes": available, "total_bytes": total,
        "estimated_weight_bytes": weight_estimate, "estimated_kv_cache_bytes": kv_estimate,
        "overhead_reserve_bytes": reserve, "estimated_required_available_bytes": required,
        "estimate_is_not_actual_peak_measurement": True}


class LocalBackend:
    """Lazy dependency imports; constructed only after the run is registered."""
    def __init__(self, model_path, *, dtype="float32", threads=4):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch = torch
        torch.set_num_threads(threads)
        torch.manual_seed(0)
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=False)
        if not self.tokenizer.chat_template:
            raise ValueError("cached_tokenizer_has_no_chat_template")
        self.model = AutoModelForCausalLM.from_pretrained(str(model_path), local_files_only=True,
            trust_remote_code=False, use_safetensors=True, torch_dtype=getattr(torch, dtype),
            attn_implementation="eager")
        self.model.to("cpu")
        self.model.eval()

    def render(self, messages):
        return self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    def count_tokens(self, prompt):
        return len(self.tokenizer.encode(prompt, add_special_tokens=False))

    def generate(self, prompt, max_new_tokens):
        inputs = self.tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
        input_count = int(inputs["input_ids"].shape[-1])
        began = time.perf_counter()
        with self.torch.inference_mode():
            generated = self.model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                num_beams=1, use_cache=True, pad_token_id=self.tokenizer.eos_token_id)
        response_ids = generated[0, input_count:]
        return {"text": self.tokenizer.decode(response_ids, skip_special_tokens=True),
                "input_tokens": input_count, "output_tokens": len(response_ids),
                "generation_seconds": round(time.perf_counter() - began, 6),
                "output_token_limit_reached": len(response_ids) >= max_new_tokens}


class CachedPromptEncoder:
    """Render this cached tokenizer's chat template without importing torch/model."""
    def __init__(self, model_path):
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        from tokenizers import Tokenizer
        config = read_json(Path(model_path) / "tokenizer_config.json")
        template = config.get("chat_template")
        if not isinstance(template, str) or not template:
            raise ValueError("cached_tokenizer_has_no_single_chat_template")
        environment = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True,
                                                     extensions=["jinja2.ext.loopcontrols"])
        self.template = environment.from_string(template)
        self.special = {}
        for name in ("bos_token", "eos_token", "unk_token", "pad_token"):
            value = config.get(name)
            self.special[name] = value.get("content", "") if isinstance(value, dict) else value or ""
        self.tokenizer = Tokenizer.from_file(str(Path(model_path) / "tokenizer.json"))

    def render(self, messages):
        return self.template.render(messages=messages, add_generation_prompt=True, **self.special)

    def count_tokens(self, prompt):
        return len(self.tokenizer.encode(prompt, add_special_tokens=False).ids)


def prepare_family(family, data, interpretation_at, system_prompt, encoder, max_input_tokens):
    family_id = family.get("family_id") if isinstance(family, dict) else None
    if not isinstance(family_id, str) or not family_id:
        raise ValueError("family_id_missing")
    examples, exclusions = select_examples(family, interpretation_at)
    knowledge, knowledge_reason = _automatic_knowledge(family, data, interpretation_at)
    if knowledge:
        valid_ids = {row["observation_id"] for row in examples}
        cited = knowledge.get("evidence_ids")
        if not isinstance(cited, list) or not cited or any(value not in valid_ids for value in cited):
            knowledge, knowledge_reason = None, "automatic_knowledge_references_excluded_or_missing_sources"
    record = {"family_id": family_id, "conditions": ["U0", "U1"] if knowledge else ["U0"],
              "knowledge_exclusion_reason": knowledge_reason, "excluded_examples": exclusions}
    while True:
        prepared = []
        for condition in record["conditions"]:
            messages = build_messages(system_prompt, family_id, examples, condition, knowledge)
            prompt = encoder.render(messages)
            prepared.append((condition, messages, prompt, encoder.count_tokens(prompt)))
        if max(item[3] for item in prepared) <= max_input_tokens:
            break
        if len(examples) <= 1:
            raise ValueError("single_example_exceeds_input_budget")
        dropped = examples.pop()
        exclusions.append({"observation_id": dropped["observation_id"], "reason": "shared_condition_token_budget"})
        if knowledge and dropped["observation_id"] in knowledge.get("evidence_ids", []):
            knowledge = None
            record["conditions"] = ["U0"]
            record["knowledge_exclusion_reason"] = "automatic_knowledge_references_token_budget_excluded_source"
    record["included_evidence_ids"] = [row["observation_id"] for row in examples]
    record["source_excerpts_sha256"] = hashlib.sha256(_json(examples).encode()).hexdigest()
    return record, examples, prepared


def persist_prompts(destination, index, prepared):
    artifacts, tasks = [], []
    for condition, messages, prompt, input_count in prepared:
        stem = f"family_{index:02d}_{condition}"
        messages_path, prompt_output = destination / (stem + "_messages.json"), destination / (stem + "_prompt.txt")
        _write_json(messages_path, messages)
        with prompt_output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(prompt)
        artifacts.extend([messages_path, prompt_output])
        tasks.append({"condition": condition, "messages": messages, "prompt": prompt,
                      "input_count": input_count, "prompt_output": prompt_output, "stem": stem})
    return artifacts, tasks


def _response_checks(response, examples):
    """Mechanical checks only; valid JSON and citations do not prove understanding."""
    try:
        parsed = json.loads(response)
    except (ValueError, TypeError):
        return {"valid_json_object": False, "semantic_accuracy": None}
    valid = isinstance(parsed, dict)
    allowed = {row["observation_id"] for row in examples}
    citations = parsed.get("evidence_ids") if valid else None
    return {"valid_json_object": valid, "required_fields_present": valid and
            {"meaning", "usage", "evidence_ids", "uncertainty"} <= set(parsed),
            "cited_evidence_ids": citations, "all_citations_in_input": isinstance(citations, list) and
            bool(citations) and all(isinstance(item, str) and item in allowed for item in citations),
            "semantic_accuracy": None, "human_reviewed": False}


def run(result=None, *, result_path=None, output_dir, model_path, max_families=1, max_new_tokens=160,
        logdb="logs/research.sqlite3", max_input_tokens=3500, dtype="float32", threads=4,
        prompt_path=DEFAULT_PROMPT, backend_factory=None, test_only=False,
        retry_of=None, retry_reason=None, db=None):
    if result is not None and result_path is not None:
        raise ValueError("provide_result_or_result_path_not_both")
    result = result if result is not None else result_path
    if result is None:
        raise ValueError("result_path_required")
    if db is not None:
        logdb = db
    if backend_factory is not None and not test_only:
        raise ValueError("injected_backend_requires_test_only")
    for value, low, high in ((max_families, 1, 10), (max_new_tokens, 16, 512),
                             (max_input_tokens, 256, 3500), (threads, 1, 16)):
        if type(value) is not int or not low <= value <= high:
            raise ValueError("invalid_local_bound")
    if dtype not in {"float32", "bfloat16"}:
        raise ValueError("unsupported_cpu_dtype")
    result_path, prompt_path = Path(result).resolve(), Path(prompt_path).resolve()
    data = read_json(result_path)
    families = data.get("families")
    if not isinstance(families, list) or not families:
        raise ValueError("result_families_missing")
    identity = _model_identity(model_path)
    if max_input_tokens + max_new_tokens > int(identity["config"].get("max_position_embeddings", 4096)):
        raise ValueError("input_plus_output_exceeds_cached_model_context")
    system_prompt = prompt_path.read_text(encoding="utf-8-sig").strip()
    spec = {"objective": "Local U0/U1 understanding of discovered meme families using identical source excerpts",
        "stage": "offline_validation" if test_only else "model_experiment",
        "parameters": {"max_families": max_families, "max_new_tokens": max_new_tokens,
            "max_input_tokens": max_input_tokens, "max_examples": 5, "dtype": dtype, "device": "cpu",
            "threads": threads, "do_sample": False, "seed": 0, "conditions": ["U0", "U1"],
            "automatic_markers": sorted(AUTOMATIC_MARKERS), "local_files_only": True,
            "trust_remote_code": False, "use_safetensors": True, "test_only": test_only,
            "model_files": identity["files"], "interpretation_time_mode": "actual_current_time_not_source_cutoff"},
        "data_snapshots": [{"id": "discovered_family_result", "sha256": hash_file(result_path)}],
        "code_hashes": {name: hash_file(ROOT / "src" / name) for name in ("meme_local_understanding.py", "research_log.py")},
        "prompt_hashes": {"understanding": hash_file(prompt_path)},
        "config_hashes": {"model_config": hash_file(Path(model_path) / "config.json")},
        "model_version": identity["snapshot_name"], "modality": ["source_text", "automatic_rule_structure"],
        "provenance": "ai_synthetic" if test_only else "mixed"}
    log = ResearchLog(logdb)
    registered = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    destination = Path(output_dir)
    interpretation_at = stamp()
    manifest = {"schema_version": "meme-local-understanding-v1", "run_id": registered["run_id"],
        "started_at": interpretation_at, "interpretation_at": interpretation_at, "status": "started",
        "result_sha256": hash_file(result_path), "model_identity": identity, "device": "cpu", "dtype": dtype,
        "test_only": test_only, "responses": [], "families": [], "issues": [], "local_model_calls": 0,
        "local_model_attempts": 0,
        "external_api_calls": 0, "model_downloads": 0, "model_weights_trained": False,
        "human_review_minutes": 0, "semantic_accuracy": None,
        "limitations": ["Model responses are generated interpretations, not verified annotations.",
            "No video, audio, comments, original-author or popularity verification.",
            "Automatic U1 structure can contain rule errors; U0/U1 share source excerpts.",
            "A present-time local model may contain prior knowledge; this is not a historical model evaluation.",
            "This is an understanding pilot on available families, not a full discovery recall evaluation."]}
    artifacts, created, phase = [], False, "output_setup"
    try:
        destination.mkdir(parents=True, exist_ok=False)
        created = True
        _write_json(destination / "model_identity.json", identity)
        artifacts.append(destination / "model_identity.json")
        phase = "resource_preflight"
        preflight = {"status": "test_only_injected_backend"} if test_only else memory_preflight(
            identity, dtype, max_input_tokens, max_new_tokens)
        manifest["resource_preflight"] = preflight
        if preflight["status"] in {"blocked_local_resources", "memory_capacity_unknown"}:
            manifest["status"] = "blocked_local_resources"
            # A ~3 MB cached tokenizer can prepare exact inputs without importing
            # torch or allocating the multi-billion-parameter model.
            phase = "blocked_prompt_preparation"
            encoder = CachedPromptEncoder(model_path)
            manifest["prepared_prompts"] = []
            for index, family in enumerate(families[:max_families], 1):
                record, examples, prepared = prepare_family(family, data, interpretation_at,
                    system_prompt, encoder, max_input_tokens)
                manifest["families"].append(record)
                paths, tasks = persist_prompts(destination, index, prepared)
                artifacts.extend(paths)
                manifest["prepared_prompts"].extend({"family_id": record["family_id"],
                    "condition": task["condition"], "path": str(task["prompt_output"].resolve()),
                    "input_tokens": task["input_count"], "not_sent_to_model": True,
                    "renderer": "cached_tokenizer_json_and_sandboxed_jinja_template"} for task in tasks)
            phase = "resource_preflight"
            raise MemoryError("local_model_not_loaded_due_to_resource_preflight")
        if not test_only:
            for row in identity["files"]:
                if row["sha256"] is None:
                    row["sha256"] = hash_file(Path(model_path) / row["name"])
            identity["weight_hashes"] = "all_local_weights_sha256"
            _write_json(destination / "loaded_model_identity.json", identity)
            artifacts.append(destination / "loaded_model_identity.json")
        phase = "model_loading"
        start_load = time.perf_counter()
        backend = (backend_factory or LocalBackend)(Path(model_path), dtype=dtype, threads=threads)
        manifest["model_loading_seconds"] = round(time.perf_counter() - start_load, 6)
        manifest["library_versions"] = {name: importlib.metadata.version(name) for name in ("torch", "transformers")}
        phase = "prompt_preparation"
        for index, family in enumerate(families[:max_families], 1):
            record, examples, prepared = prepare_family(family, data, interpretation_at,
                system_prompt, backend, max_input_tokens)
            family_id = record["family_id"]
            manifest["families"].append(record)
            paths, tasks = persist_prompts(destination, index, prepared)
            artifacts.extend(paths)
            for task in tasks:
                condition, prompt, prompt_output, stem, input_count = (task[key] for key in
                    ("condition", "prompt", "prompt_output", "stem", "input_count"))
                phase = "inference_" + condition
                began = stamp()
                manifest["local_model_attempts"] += 1
                generated = backend.generate(prompt, max_new_tokens)
                manifest["local_model_calls"] += 1
                response = {"family_id": family_id, "condition": condition, "started_at": began,
                    "finished_at": stamp(), "interpretation_at": interpretation_at, "provenance": "local_model_generated",
                    "test_only": test_only, "source_excerpts_sha256": record["source_excerpts_sha256"],
                    "prompt_sha256": hash_file(prompt_output), "prompt_input_tokens": input_count,
                    **generated, "mechanical_checks": _response_checks(generated.get("text"), examples)}
                output = destination / (stem + "_response.json")
                _write_json(output, response)
                artifacts.append(output)
                manifest["responses"].append({"family_id": family_id, "condition": condition,
                    "path": str(output.resolve()), "input_tokens": generated["input_tokens"],
                    "output_tokens": generated["output_tokens"], "mechanical_checks": response["mechanical_checks"]})
                phase = "prompt_preparation"
        manifest["status"] = "completed"
    except Exception as exc:
        if manifest["status"] != "blocked_local_resources":
            manifest["status"] = "failed"
        manifest["issues"].append({"phase": phase, "error_type": type(exc).__name__,
            "reason": str(exc) if isinstance(exc, (ValueError, FileExistsError, MemoryError)) and len(str(exc)) < 250 else "local_operation_failed"})
    finally:
        manifest["finished_at"] = stamp()
        if created:
            _write_json(destination / "manifest.json", manifest)
            artifacts.append(destination / "manifest.json")
        log.finish(registered["run_id"], status="succeeded" if manifest["status"] == "completed" else "failed",
            notes=f"Local understanding {manifest['status']}; completed calls={manifest['local_model_calls']}; semantic accuracy unmeasured; no training/download/external API. Phase={phase}.",
            artifacts=artifacts, actual_cost={"amount": 0, "currency": "USD", "api_units": 0,
                "input_tokens": sum(row["input_tokens"] for row in manifest["responses"]),
                "output_tokens": sum(row["output_tokens"] for row in manifest["responses"]), "human_minutes": 0})
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--max-families", type=int, default=1)
    parser.add_argument("--max-new-tokens", type=int, default=160)
    parser.add_argument("--max-input-tokens", type=int, default=3500)
    parser.add_argument("--dtype", choices=["float32", "bfloat16"], default="float32")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--db", default="logs/research.sqlite3")
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = parser.parse_args(argv)
    try:
        manifest = run(args.result, output_dir=args.output_dir, model_path=args.model_path,
            max_families=args.max_families, max_new_tokens=args.max_new_tokens, logdb=args.db,
            max_input_tokens=args.max_input_tokens, dtype=args.dtype, threads=args.threads,
            retry_of=args.retry_of, retry_reason=args.retry_reason)
        print(json.dumps({key: manifest[key] for key in ("run_id", "status", "local_model_calls", "issues")}, ensure_ascii=False))
        return 0 if manifest["status"] == "completed" else 2
    except DuplicateRunError as exc:
        print(json.dumps({"error": "duplicate_run", "existing_run_id": exc.existing["run_id"]}))
        return 3
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
