"""Offline inspection and strictly scoped Responses execution for a development pilot."""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from fractions import Fraction
from pathlib import Path

import meme_pipeline as mp

SUPPORTED_MODEL = "gpt-4.1-mini-2025-04-14"
RATE_CARD = {
    "input_usd_per_million": "0.40",
    "cached_input_usd_per_million": "0.10",
    "output_usd_per_million": "1.60",
}
RATE_CARD_ID = "openai_standard_gpt41mini_20260911"
DOCS = [
    "https://developers.openai.com/api/docs/models/gpt-4.1-mini",
    "https://developers.openai.com/api/docs/pricing",
    "https://developers.openai.com/api/docs/guides/token-counting",
    "https://developers.openai.com/api/reference/python/resources/responses/methods/create",
    "https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api",
]


def now():
    return datetime.now(timezone.utc).isoformat()


def money(value):
    mp.require(isinstance(value, (str, int, float)) and not isinstance(value, bool), "Invalid money type")
    try:
        amount = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        raise ValueError("Invalid finite money amount") from None
    mp.require(amount >= 0, "Negative money amount")
    return amount


def count_value(value, label, positive=False):
    mp.require(type(value) is int and value >= (1 if positive else 0), f"Invalid {label} count")
    return value


def validate_scope(scope):
    mp.require(scope.get("model_id") == SUPPORTED_MODEL, "Only the documented fixed snapshot is implemented")
    mp.require(isinstance(scope.get("scope_id"), str) and scope["scope_id"], "Missing scope ID")
    mp.require(scope.get("rate_card_id") == RATE_CARD_ID, "Unsupported rate-card version")
    mp.require(set(scope.get("rates", {})) == set(RATE_CARD), "Missing or unexpected rates")
    for key, rate in RATE_CARD.items():
        mp.require(money(scope["rates"][key]) == money(rate), "Rate does not match verified fixed-model card")
    mp.require(money(scope["budget_usd"]) > 0, "Budget must be positive")
    count_value(scope["max_model_calls"], "model-call limit", True)
    count_value(scope["max_token_count_calls"], "token-count-call limit", True)
    mp.require(isinstance(scope.get("ledger_path"), str) and scope["ledger_path"], "Bind the scope to one ledger path")
    mp.require(scope.get("max_retries") == 0, "Scope must disable retries")
    mp.require(set(scope["limits"]) == {"extraction", "generation"}, "Stage limits are incomplete")
    for limits in scope["limits"].values():
        count_value(limits["max_input_tokens"], "input limit", True)
        count_value(limits["max_output_tokens"], "output limit", True)
        mp.require(limits["max_output_tokens"] <= 32768, "Model output limit exceeded")
        mp.require(limits["max_input_tokens"]+limits["max_output_tokens"] <= 1047576, "Model context limit exceeded")
    for key in ("approved_family_ids", "cleared_source_ids"):
        values = scope.get(key)
        mp.require(isinstance(values, list) and all(isinstance(v, str) and v for v in values), "Invalid scoped IDs")
        mp.require(len(values) == len(set(values)), "Duplicate scoped IDs")
    return scope


def cost_from_usage(usage, scope, limits):
    mp.require(isinstance(usage, dict), "Provider usage is missing")
    n_in = count_value(usage.get("input_tokens"), "input")
    n_out = count_value(usage.get("output_tokens"), "output")
    total = count_value(usage.get("total_tokens"), "total")
    details = usage.get("input_tokens_details")
    mp.require(isinstance(details, dict), "Input token details are missing")
    cached = count_value(details.get("cached_tokens"), "cached")
    written = count_value(details.get("cache_write_tokens", 0), "cache write")
    mp.require(written == 0, "Separately billed cache writes are unsupported")
    mp.require(total == n_in+n_out and cached <= n_in, "Inconsistent token usage")
    mp.require(n_in <= limits["max_input_tokens"] and n_out <= limits["max_output_tokens"], "Usage exceeded reserved limits")
    rates = scope["rates"]
    return ((n_in-cached)*money(rates["input_usd_per_million"])
            +cached*money(rates["cached_input_usd_per_million"])
            +n_out*money(rates["output_usd_per_million"]))/1_000_000


def payload_for(request, scope):
    mp.require(request["model_id"] == scope["model_id"] == SUPPORTED_MODEL, "Prepared model differs from scope")
    mp.require(request["stage"] in {"extraction", "generation"}, "Unknown stage")
    mp.require(request["mode"] == "pilot", "Executor accepts development pilot only")
    mp.require(request.get("record_origin") in (None, "automatic"), "Fixture/human record cannot be executed")
    messages = request["messages"]
    mp.require(set(messages) == {"system", "user"} and all(isinstance(v, str) and v for v in messages.values()),
               "Unexpected or empty messages")
    n_out = count_value(request["max_output_tokens"], "requested output", True)
    mp.require(n_out <= scope["limits"][request["stage"]]["max_output_tokens"], "Output cap exceeds scope")
    mp.require(type(request["temperature"]) in (int, float) and 0 <= request["temperature"] <= 2, "Invalid temperature")
    base = {"model": scope["model_id"], "instructions": messages["system"], "input": messages["user"], "truncation": "disabled"}
    create = {**base, "max_output_tokens": n_out, "temperature": request["temperature"],
              "service_tier": "default", "store": False, "stream": False, "background": False}
    return base, create


def material_hashes(cfg, families, supports, scenarios=None):
    hashes = {"config": mp.stable_hash(cfg), "families": mp.stable_hash(families),
              "supports": mp.stable_hash(supports), "extract_prompt": mp.file_hash(cfg["extract_prompt"]),
              "generate_prompt": mp.file_hash(cfg["generate_prompt"]),
              "pipeline_code": mp.file_hash(mp.__file__), "executor_code": mp.file_hash(__file__)}
    if scenarios is not None:
        hashes["scenarios"] = mp.stable_hash(scenarios)
    return hashes


def verify_scoped_materials(scope, cfg, families, supports, scenarios=None):
    actual = material_hashes(cfg, families, supports, scenarios)
    approved = scope.get("material_hashes", {})
    mp.require(set(approved) in (set(actual), set(actual)|{"scenarios"}), "Scope has incomplete material bindings")
    mp.require(all(approved.get(k) == v for k,v in actual.items()), "Materials differ from the proposed/approved hashes")


def verify_materials(requests, cfg, families, supports, scenarios=None, records=None, extraction_responses=None):
    """Rebuild the entire batch, not only self-declared hashes. Extraction never accepts scenarios."""
    mp.validate_dataset(families, supports, cfg)
    mp.index_unique(requests, "request_id")
    mp.require(bool(requests), "No requests to inspect or execute")
    mp.require(cfg["mode"] == "pilot" and cfg["record_origin"] == "automatic", "Real development pilot required")
    mp.require(all(f["split"] == "dev" for f in families), "This executor accepts development families only")
    stages = {r["stage"] for r in requests}
    mp.require(len(stages) == 1, "Execute extraction and generation as separate stages")
    if stages == {"extraction"}:
        mp.require(scenarios is None and records is None and extraction_responses is None, "Extraction must not receive scenarios or records")
        expected = mp.make_extractions(families, supports, cfg)
    elif stages == {"generation"}:
        mp.require(scenarios is not None and records is not None, "Generation requires original scenarios and automatic records")
        mp.require(bool(extraction_responses), "Generation requires persisted extraction responses")
        automatic, _ = mp.import_extractions(mp.make_extractions(families, supports, cfg), extraction_responses, cfg)
        mp.require(records == automatic, "Records differ from original automatic extraction responses")
        expected, _, _ = mp.generation_requests(families, supports, scenarios, records, cfg)
    else:
        raise ValueError("Unknown request stage")
    mp.require(requests == expected, "Prepared requests differ from canonical source/config reconstruction")


def blockers(scope, requests, supports, key_available):
    validate_scope(scope)
    reasons = []
    if scope.get("approved") is not True or not scope.get("approval_reference"):
        reasons.append("Researcher has not approved this concrete execution scope")
    if not key_available:
        reasons.append("OPENAI_API_KEY is not configured; set it locally, never in chat/data files")
    try:
        age = (date.today()-date.fromisoformat(scope.get("rates_verified_on", ""))).days
        current = scope.get("rates_verified") is True and age in range(8)
    except (TypeError, ValueError):
        current = False
    if not current:
        reasons.append("Reverify this rate card before execution (verification missing or over seven days old)")
    if len(requests) > min(scope["max_model_calls"], scope["max_token_count_calls"]):
        reasons.append("Request count exceeds the approved call limit")
    needed = {r["family_id"] for r in requests}
    if not needed <= set(scope["approved_family_ids"]):
        reasons.append("Some families are outside the proposed/approved scope")
    if any(s["source_id"] not in set(scope["cleared_source_ids"]) for s in supports if s["family_id"] in needed):
        reasons.append("Source-use review has not cleared every support source for external input")
    return reasons


def ceiling(scope, stage, max_output, n_input=None):
    n_input = scope["limits"][stage]["max_input_tokens"] if n_input is None else n_input
    return (n_input*money(scope["rates"]["input_usd_per_million"])
            +max_output*money(scope["rates"]["output_usd_per_million"]))/1_000_000


def inspect_requests(requests, scope, supports, key_available=False):
    validate_scope(scope)
    stages, payloads, charge = {}, [], Fraction(0)
    for r in requests:
        base, create = payload_for(r, scope)
        charge += ceiling(scope, r["stage"], r["max_output_tokens"])
        stages[r["stage"]] = stages.get(r["stage"], 0)+1
        payloads.append({"request_id": r["request_id"], "count_payload": base, "create_payload": create})
    return {"inspected_at": now(), "network_executed": False, "model_calls": 0,
            "request_counts": stages, "planned_token_count_calls": len(requests),
            "conservative_model_token_ceiling_usd": float(charge), "exact_ceiling_fraction_usd": str(charge),
            "ceiling_basis": "ASSUMED input caps and configured output caps; not measured usage or the whole bill",
            "proposed_budget_usd": scope["budget_usd"], "ledger_path": scope["ledger_path"],
            "blockers": blockers(scope, requests, supports, key_available),
            "seed_support": "Responses has no seed field; local ordering only", "payloads": payloads}


class DurableBudget:
    """One scope-bound ledger; stop after uncertain calls; no automatic recovery or retries."""
    def __init__(self, path, scope):
        validate_scope(scope)
        self.path, self.scope = Path(path), scope
        mp.require(self.path.resolve() == Path(scope["ledger_path"]).resolve(), "Ledger path differs from approved scope")
        self.lock_path = self.path.with_suffix(self.path.suffix+".lock")

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.lock_path.open("x", encoding="utf-8")
        try:
            self.handle.write(json.dumps({"pid": os.getpid(), "opened_at": now()}))
            self.handle.flush()
            os.fsync(self.handle.fileno())
            fingerprint = mp.stable_hash(self.scope)
            if self.path.exists():
                self.state = mp.read_json(self.path)
                mp.require(self.state["scope_hash"] == fingerprint, "Ledger scope changed; reconcile before reuse")
            else:
                self.state = {"scope_hash": fingerprint, "budget_usd": str(money(self.scope["budget_usd"])),
                              "spent_usd": "0", "reservations": {}, "attempted_ids": [],
                              "count_attempted_ids": [], "count_inflight": None, "blocked": False, "events": []}
                self.save()
            mp.require(not self.state["blocked"] and not self.state["reservations"] and not self.state["count_inflight"],
                       "Ledger has an unresolved prior request; reconcile manually, do not retry")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        self.handle.close()
        self.lock_path.unlink()

    def save(self):
        temp = self.path.with_suffix(self.path.suffix+".tmp")
        with temp.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(self.state, ensure_ascii=False, indent=2)+"\n")
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(self.path)

    def capacity(self, rid, amount):
        mp.require(not self.state["blocked"], "Ledger blocked")
        mp.require(rid not in self.state["attempted_ids"], "Request already attempted; no automatic retry")
        mp.require(len(self.state["attempted_ids"]) < self.scope["max_model_calls"], "Model-call ceiling exhausted")
        held = sum((money(v) for v in self.state["reservations"].values()), Fraction(0))
        mp.require(money(self.state["spent_usd"])+held+amount <= money(self.state["budget_usd"]), "Insufficient budget for next request")

    def begin_count(self, rid, upper_bound):
        self.capacity(rid, upper_bound)
        mp.require(rid not in self.state["count_attempted_ids"], "Token count already attempted; no retry")
        mp.require(not self.state["count_inflight"], "Unresolved token-count operation")
        mp.require(len(self.state["count_attempted_ids"]) < self.scope["max_token_count_calls"], "Token-count ceiling exhausted")
        self.state["count_attempted_ids"].append(rid)
        self.state["count_inflight"] = rid
        self.state["events"].append({"at": now(), "event": "count_started", "request_id": rid})
        self.save()

    def finish_count(self, rid):
        mp.require(self.state["count_inflight"] == rid, "Count operation mismatch")
        self.state["count_inflight"] = None
        self.save()

    def reserve(self, rid, amount):
        self.capacity(rid, amount)
        mp.require(rid in self.state["count_attempted_ids"] and not self.state["count_inflight"], "Exact count must finish first")
        self.state["reservations"][rid] = str(amount)
        self.state["attempted_ids"].append(rid)
        self.state["events"].append({"at": now(), "event": "reserved", "request_id": rid, "amount_usd": str(amount)})
        self.save()

    def settle(self, rid, actual):
        held = money(self.state["reservations"][rid])
        self.state["spent_usd"] = str(money(self.state["spent_usd"])+actual)
        del self.state["reservations"][rid]
        self.state["events"].append({"at": now(), "event": "settled", "request_id": rid, "actual_usd": str(actual)})
        if actual > held:
            self.state["blocked"] = True
        self.save()
        mp.require(actual <= held, "Actual charge exceeded reservation; ledger stopped")

    def stop(self, rid, reason):
        self.state["blocked"] = True
        self.state["events"].append({"at": now(), "event": "stopped", "request_id": rid, "reason": reason})
        self.save()


def append_response(path, response):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(response, ensure_ascii=False)+"\n")
        stream.flush()
        os.fsync(stream.fileno())


def run_requests(client, requests, scope, ledger_path, output_dir, *, cfg, families, supports, scenarios=None, records=None, extraction_responses=None):
    verify_materials(requests, cfg, families, supports, scenarios, records, extraction_responses)
    verify_scoped_materials(scope, cfg, families, supports, scenarios)
    reasons = blockers(scope, requests, supports, key_available=True)
    mp.require(not reasons, "Execution blocked: "+"; ".join(reasons))
    mp.require(getattr(client, "max_retries", None) == 0, "Client retries must be disabled")
    mp.require(str(getattr(client, "base_url", "")).rstrip("/") == "https://api.openai.com/v1", "Unexpected API endpoint")
    budget = DurableBudget(ledger_path, scope)
    out = Path(output_dir)
    mp.require(not out.exists(), "Choose a new output directory")
    out.mkdir(parents=True)
    (out/"raw").mkdir()
    responses_path = out/"responses.jsonl"
    responses_path.touch()
    with budget:
        for r in requests:
            rid = r["request_id"]
            base, create = payload_for(r, scope)
            limits = scope["limits"][r["stage"]]
            started = time.monotonic()
            event = {"request_id": rid, "record_origin": "automatic", "model_id": scope["model_id"],
                     "response_id": None, "created_at": now(), "usage": None, "status": "error", "output_text": "",
                     "error": None, "retry_count": 0, "error_stage": "pre_count",
                     "requested_parameters": {k:v for k,v in create.items() if k not in {"input", "instructions"}},
                     "seed_requested": r.get("seed_requested"), "seed_sent": False, "input_count": None}
            try:
                # Check remaining cap/budget BEFORE externally sending text, and persist the count attempt.
                budget.begin_count(rid, ceiling(scope, r["stage"], r["max_output_tokens"]))
                event["error_stage"] = "token_count"
                count_obj = client.responses.input_tokens.count(**base)
                n = count_value(count_obj.input_tokens, "preflight input")
                event["input_count"] = n
                mp.write_json(out/"raw"/(rid+".token_count.json"),
                              {"input_tokens": n, "payload_hash": mp.stable_hash(base), "counted_at": now()})
                mp.require(n <= limits["max_input_tokens"], "Exact count exceeds input cap")
                budget.finish_count(rid)
                budget.reserve(rid, ceiling(scope, r["stage"], r["max_output_tokens"], n))
                event["error_stage"] = "generation_request"
                response = client.responses.create(**create)
                raw = response.model_dump(mode="json")
                mp.write_json(out/"raw"/(rid+".response.json"), raw)
                event.update(response_id=raw.get("id"), created_at=now(), usage=raw.get("usage"),
                             output_text=response.output_text, provider_status=raw.get("status"),
                             provider_request_id=getattr(response, "_request_id", None), provider_created_at=raw.get("created_at"))
                event["error_stage"] = "response_validation"
                mp.require(isinstance(raw.get("id"), str) and bool(raw["id"]), "Missing provider response ID")
                mp.require(raw.get("model") == scope["model_id"], "Unexpected response model")
                mp.require(raw.get("service_tier") == "default", "Unexpected service tier")
                cost = cost_from_usage(raw.get("usage"), scope, limits)
                mp.require(raw["usage"]["input_tokens"] == n, "Exact count and billed input differ")
                budget.settle(rid, cost)
                event["model_token_cost_usd"] = str(cost)
                echoed = {k: raw[k] for k in ("temperature", "max_output_tokens", "store", "truncation", "background") if k in raw}
                event["returned_parameters"] = echoed
                mp.require(all(v == create[k] for k,v in echoed.items()), "Returned parameters differ from request")
                has_refusal = any(part.get("type") == "refusal" for item in raw.get("output", []) for part in item.get("content", []))
                if raw.get("status") != "completed" or has_refusal or not response.output_text.strip():
                    event["error"] = "incomplete_refusal_or_empty"
                    budget.stop(rid, event["error"])
                else:
                    event.update(status="ok", error_stage=None)
                event["latency_ms"] = round((time.monotonic()-started)*1000)
                append_response(responses_path, event)
            except BaseException as exc:
                # Never serialize exception messages, request headers, keys, or URLs from provider exceptions.
                event["status"] = "error"
                event["error"] = "execution_or_accounting_error:"+type(exc).__name__
                status = getattr(exc, "status_code", None)
                event["http_status"] = status if type(status) is int else None
                event["latency_ms"] = round((time.monotonic()-started)*1000)
                budget.stop(rid, event["error"])
                append_response(responses_path, event)
                raise RuntimeError("Execution stopped; inspect redacted logs and reconcile the bound ledger before any retry.") from None
            if event["status"] != "ok":
                raise RuntimeError("Provider returned incomplete/refused/empty output; ledger stopped without retry.")
    return responses_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "execute"))
    for name in ("requests", "config", "scope", "families", "supports", "out"):
        parser.add_argument("--"+name, required=True)
    for name in ("scenarios", "records", "extraction-responses", "ledger"):
        parser.add_argument("--"+name)
    args = parser.parse_args()
    cfg, scope = mp.load_config(args.config), mp.read_json(args.scope)
    requests = mp.read_jsonl(args.requests)
    families, supports = mp.read_jsonl(args.families), mp.read_jsonl(args.supports)
    # Check stage before opening optional files: extraction never reads evaluation scenarios or records.
    if {r["stage"] for r in requests} == {"extraction"}:
        mp.require(not args.scenarios and not args.records and not args.extraction_responses, "Extraction must not open scenarios or records")
    scenarios = mp.read_jsonl(args.scenarios) if args.scenarios else None
    records = mp.read_jsonl(args.records) if args.records else None
    extraction_responses = mp.read_jsonl(args.extraction_responses) if args.extraction_responses else None
    verify_materials(requests, cfg, families, supports, scenarios, records, extraction_responses)
    verify_scoped_materials(scope, cfg, families, supports, scenarios)
    report = inspect_requests(requests, scope, supports, bool(os.environ.get("OPENAI_API_KEY")))
    inputs = [args.requests, args.config, args.scope, args.families, args.supports, __file__,
              "src/meme_pipeline.py", cfg["extract_prompt"], cfg["generate_prompt"]]
    inputs += [p for p in (args.scenarios, args.records, args.extraction_responses) if p]
    out = Path(args.out)
    if args.action == "inspect":
        mp.require(not out.exists(), "Choose a new inspection directory")
        mp.write_json(out/"preflight.json", report)
        mp.write_json(out/"manifest.json", mp.manifest(out, "inspect_responses", inputs, cfg,
                                                      {"prepared_requests": len(requests), "actual_model_calls": 0}))
        print(json.dumps({k:v for k,v in report.items() if k != "payloads"}, ensure_ascii=False, indent=2))
        return
    mp.require(not report["blockers"], "Execution blocked: "+"; ".join(report["blockers"]))
    ledger_path = args.ledger or scope["ledger_path"]
    DurableBudget(ledger_path, scope)  # Validate binding before constructing the network client.
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url="https://api.openai.com/v1", max_retries=0, timeout=60.0)
    snapshot_dir = out.parent/(out.name+"_authorized_inputs")
    mp.require(not out.exists() and not snapshot_dir.exists(), "Choose a new run name")
    mp.write_json(snapshot_dir/"manifest.json", mp.manifest(snapshot_dir, "authorized_execute_inputs", inputs, cfg,
                                                          {"prepared_requests": len(requests)}))
    output = run_requests(client, requests, scope, ledger_path, out, cfg=cfg, families=families,
                          supports=supports, scenarios=scenarios, records=records, extraction_responses=extraction_responses)
    print(json.dumps({"responses": str(output), "ledger": ledger_path}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, KeyError, OSError, TypeError) as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        sys.exit(2)