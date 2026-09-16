"""One bounded public Google Trends KR RSS observation, with pre-request logging.

Google Search trending queries are not YouTube popularity or verified memes.
RSS news fields are metadata; no linked article bodies are fetched or claimed read.
No authentication, cookies, internal APIs, redirects or automatic retries.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from research_log import DuplicateRunError, ResearchLog, hash_file, read_json

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://trends.google.com/trending/rss?geo=KR"
HELP_URL = "https://support.google.com/trends/answer/3076011?hl=en"
ATTRIBUTION_URL = "https://support.google.com/trends/answer/4365538?hl=en"
HARD_MAX_BYTES = 2 * 1024 * 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None:
        raise ValueError("An explicit timezone is required")
    return parsed.astimezone(timezone.utc)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _children(element, name):
    return [child for child in element if _tag(child) == name]


def _text(element, name):
    nodes = _children(element, name)
    return "".join(nodes[0].itertext()) if nodes else None


def parse_feed(body, received_at, *, max_entries=200, provenance="actual_collected"):
    if not isinstance(body, bytes) or len(body) > HARD_MAX_BYTES:
        raise ValueError("Expected bounded XML bytes")
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("DTD and entity declarations are unsupported")
    if provenance not in {"actual_collected", "ai_synthetic"}:
        raise ValueError("Invalid collection provenance")
    if type(max_entries) is not int or not 1 <= max_entries <= 200:
        raise ValueError("Invalid entry limit")
    received = _utc(received_at)
    root = ET.fromstring(body)
    if _tag(root) != "rss" or len(_children(root, "channel")) != 1:
        raise ValueError("Expected RSS with one channel")
    channel = _children(root, "channel")[0]
    items = _children(channel, "item")
    observations, exclusions, seen = [], [], set()
    for position, item in enumerate(items, 1):
        topic, published_raw = _text(item, "title"), _text(item, "pubDate")
        rejection = {"feed_position": position, "query_or_topic": topic}
        if not topic or not topic.strip():
            exclusions.append({**rejection, "reason": "empty_topic"})
            continue
        try:
            published = _utc(parsedate_to_datetime(published_raw))
        except (TypeError, ValueError, IndexError, OverflowError):
            exclusions.append({**rejection, "reason": "missing_or_invalid_timezone_pubDate"})
            continue
        if published > received:
            exclusions.append({**rejection, "reason": "future_pubDate"})
            continue
        if len(observations) >= max_entries:
            exclusions.append({**rejection, "reason": "configured_entry_limit"})
            continue
        news = []
        for news_item in _children(item, "news_item"):
            news.append({name: _text(news_item, "news_item_" + name) for name in
                         ("title", "url", "source", "snippet", "picture")})
        # Keep provider-formatted approximate traffic. Do not interpret 1K+/1천+
        # as exact counts, assume a unit conversion, or aggregate with YouTube.
        traffic = _text(item, "approx_traffic")
        link = _text(item, "link")
        identity = {"topic": topic, "pubDate": published_raw, "link": link}
        source_id = "google_trends:KR:" + _digest(_json(identity).encode("utf-8"))[:24]
        if source_id in seen:
            exclusions.append({**rejection, "reason": "duplicate_feed_item"})
            continue
        seen.add(source_id)
        content = {**identity, "approx_traffic": traffic, "news": news,
                   "description": _text(item, "description"), "picture": _text(item, "picture"),
                   "picture_source": _text(item, "picture_source")}
        version = _digest(_json(content).encode("utf-8"))[:24]
        observations.append({
            "observation_id": source_id + ":" + version, "platform": "google_trends", "source_id": source_id,
            "geo": "KR", "feed_position": position, "text": topic, "query_or_topic": topic,
            "modality": "search_trend_metadata", "context": {"rss_description": content["description"],
                "news_items_metadata": news, "picture_url": content["picture"], "picture_source": content["picture_source"],
                "news_article_bodies_read": False},
            "posted_at": published.isoformat(), "rss_pubDate_raw": published_raw,
            "posted_at_role": "RSS_pubDate_not_verified_trend_origin_or_article_publication",
            "collected_at": received.isoformat(), "available_at": received.isoformat(),
            "availability_basis": "actual_feed_response_time_not_historical_pubDate",
            "source_url": link, "feed_url": ENDPOINT, "evidence_links": [ENDPOINT] + ([link] if link else []),
            "approx_traffic_raw": traffic, "approx_traffic_numeric": None,
            "metric_scope": "Google_Search_provider_bucket_not_YouTube_views_or_meme_use_frequency",
            "provider_grouping": "may_represent_related_queries_no_RSS_breakdown_inferred",
            "provenance": provenance, "test_only": provenance == "ai_synthetic",
            "sample_method": {"mode": "official_public_RSS_snapshot", "geo": "KR", "known_meme_name_seed": False,
                              "historical_completeness": "unknown", "representativeness": "provider_selected_trending_queries"},
            "meme_label": "unassessed", "verified_meme": False, "human_review_status": "not_reviewed",
            "data_attribution": "Data source: Google Trends"})
    namespaces = sorted({node.tag.split("}", 1)[0][1:] for node in root.iter() if node.tag.startswith("{")})
    return {"observations": observations, "exclusions": exclusions,
            "feed": {"title": _text(channel, "title"), "link": _text(channel, "link"),
                     "description": _text(channel, "description"), "item_count": len(items),
                     "extension_namespaces_observed": namespaces, "pagination_used": False}}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect_blocked")


def http_get(url, timeout, max_bytes):
    if url != ENDPOINT or timeout > 15 or max_bytes > HARD_MAX_BYTES:
        raise ValueError("Invalid bounded public RSS request")
    request = urllib.request.Request(url, headers={
        "User-Agent": "KoreanMemeResearch/0.1 (bounded public Google Trends RSS observation)",
        "Accept": "application/rss+xml, application/xml, text/xml", "Accept-Encoding": "identity"})
    # A fresh opener has no cookie/auth handlers or session state. No redirects.
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("response_byte_limit")
        return {"body": body, "status": response.status, "content_type": response.headers.get("Content-Type", "")}


def validate_config(config):
    required = {"snapshot_id", "geo", "max_requests", "timeout_seconds", "max_response_bytes", "max_entries"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("Config requires only bounded RSS fields; credentials or alternate endpoints are not accepted")
    if not isinstance(config["snapshot_id"], str) or not config["snapshot_id"].strip():
        raise ValueError("snapshot_id must name a deliberate collection round")
    if config["geo"] != "KR" or type(config["max_requests"]) is not int or config["max_requests"] != 1:
        raise ValueError("This collector is limited to one KR RSS request")
    for name, maximum in (("timeout_seconds", 15), ("max_response_bytes", HARD_MAX_BYTES), ("max_entries", 200)):
        if type(config[name]) is not int or not 1 <= config[name] <= maximum:
            raise ValueError("Invalid limit: " + name)
    return dict(config)


def _save(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run(config_path, output_dir, *, db="logs/research.sqlite3", fixture_body=None, retry_of=None, retry_reason=None):
    config = validate_config(read_json(config_path))
    output = Path(output_dir)
    if output.exists():
        raise ValueError("Output directory exists; prior collection is preserved")
    if fixture_body is not None and not isinstance(fixture_body, bytes):
        raise ValueError("Fixture must be bytes and is always synthetic")
    synthetic = fixture_body is not None
    spec = {"objective": "Observe one public Google Trends KR RSS snapshot; no verified meme or YouTube popularity claim",
            "stage": "offline_validation" if synthetic else "access_probe",
            "parameters": {**config, "endpoint": ENDPOINT, "test_only": synthetic, "automatic_retries": 0},
            "data_snapshots": [{"id": "ai_synthetic_XML_fixture", "sha256": _digest(fixture_body)}] if synthetic else [],
            "code_hashes": {name: hash_file(ROOT / "src" / name) for name in ("google_trends_collect.py", "research_log.py")},
            "prompt_hashes": {}, "config_hashes": {"validated_config": _digest(_json(config).encode("utf-8"))},
            "modality": ["search_trend_metadata"], "provenance": "ai_synthetic" if synthetic else "actual_collected"}
    log = ResearchLog(db)
    entry = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    artifacts, attempted = [], 0
    manifest = {"run_id": entry["run_id"], "platform": "google_trends", "endpoint": ENDPOINT,
                "config": config, "provenance": spec["provenance"], "test_only": synthetic,
                "status": "started", "model_calls": 0, "verified_meme_count": None,
                "official_help_sources": [HELP_URL, ATTRIBUTION_URL], "news_article_bodies_read": False,
                "cross_platform_metric_aggregation": "prohibited_different_measures",
                "limitations": ["Provider-selected Google Search trend metadata, not YouTube trends or verified memes.",
                                "Traffic is retained as provider-formatted approximate text; no exact counts inferred.",
                                "One current snapshot cannot establish historical discovery, sustained popularity or origin.",
                                "Linked news bodies are not fetched; headlines and snippets are RSS metadata only."]}
    try:
        output.mkdir(parents=True, exist_ok=False)
        if synthetic:
            response = {"body": fixture_body, "status": 200, "content_type": "text/xml; fixture"}
        else:
            attempted = 1
            response = http_get(ENDPOINT, config["timeout_seconds"], config["max_response_bytes"])
        received_at = _now()
        body = response["body"]
        if not isinstance(body, bytes) or len(body) > config["max_response_bytes"]:
            raise ValueError("response_byte_limit_or_invalid_body")
        raw = output / "raw.xml"
        with raw.open("xb") as stream:
            stream.write(body)
        artifacts.append(raw)
        manifest.update({"received_at": received_at, "http_status": response["status"],
                         "content_type": response["content_type"], "raw_bytes": len(body), "raw_sha256": _digest(body)})
        if response["status"] != 200:
            raise ValueError("unexpected_http_status")
        parsed = parse_feed(body, received_at, max_entries=config["max_entries"], provenance=spec["provenance"])
        for name in ("observations", "exclusions"):
            path = output / (name + ".jsonl")
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                for row in parsed[name]:
                    stream.write(_json(row) + "\n")
            artifacts.append(path)
        manifest.update({"status": "collected_metadata_not_meme_validation", "feed": parsed["feed"],
                         "observation_count": len(parsed["observations"]), "exclusion_count": len(parsed["exclusions"]),
                         "http_requests_attempted": attempted, "fixture_reads": 1 if synthetic else 0,
                         "finished_at": _now()})
        path = output / "manifest.json"
        _save(path, manifest)
        artifacts.append(path)
        log.finish(entry["run_id"], status="succeeded", notes=f"Read one {'synthetic fixture' if synthetic else 'public RSS response'}; {len(parsed['observations'])} Google Search trend metadata records. No article bodies, model calls or meme validation.",
                   artifacts=artifacts, actual_cost={"amount": 0, "currency": "USD", "api_units": 0, "human_minutes": 0})
        return manifest
    except BaseException as exc:
        manifest.update({"status": "failed", "error_type": type(exc).__name__, "http_requests_attempted": attempted,
                         "finished_at": _now()})
        if isinstance(exc, urllib.error.HTTPError):
            manifest["http_status"] = exc.code
        if output.exists() and not (output / "manifest.json").exists():
            _save(output / "manifest.json", manifest)
            artifacts.append(output / "manifest.json")
        log.finish(entry["run_id"], status="failed", notes="Bounded Google Trends RSS observation failed: " + type(exc).__name__, artifacts=artifacts)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/google_trends_kr_v01.json")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--db", default="logs/research.sqlite3")
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = vars(parser.parse_args(argv))
    args["config_path"] = args.pop("config")
    try:
        print(json.dumps(run(**args), ensure_ascii=False, indent=2))
    except (DuplicateRunError, ValueError, OSError, ET.ParseError) as exc:
        parser.exit(2, "Google Trends collection failed: " + type(exc).__name__ + "\n")


if __name__ == "__main__":
    main()
