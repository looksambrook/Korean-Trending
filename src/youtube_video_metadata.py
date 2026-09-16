"""Resolve at most three public search results through initial watch-page HTML.

This fetches HTML metadata only: no player execution, media, comments, cookies,
credentials, internal API, redirects, challenges, or automatic retries. Results
remain posthoc expansion, even when an exact public publication date is found.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_log import DuplicateRunError, ResearchLog, hash_file, normalize_spec, read_json
from youtube_web_search import CHANNEL, VIDEO, _NoRedirect, _text, digest, stamp, utc

ROOT = Path(__file__).resolve().parents[1]
REASONS = {"invalid_public_url", "redirect_blocked", "http_error", "rate_limited", "transport_error",
           "response_too_large", "unexpected_content_type", "consent_required", "captcha_or_traffic_block",
           "login_required", "invalid_html", "metadata_unavailable", "video_identity_mismatch",
           "source_validation_failed", "collector_error", "artifact_finalization_failed"}


class MetadataError(Exception):
    def __init__(self, reason, status=None):
        self.reason = reason if reason in REASONS else "collector_error"
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        super().__init__(self.reason)


def fetch_watch_page(url, timeout, max_bytes):
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query)
    if (parsed.scheme != "https" or parsed.hostname != "www.youtube.com" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.path != "/watch"
            or set(query) != {"v", "hl"} or len(query["v"]) != 1 or not VIDEO.fullmatch(query["v"][0])):
        raise MetadataError("invalid_public_url")
    request = urllib.request.Request(url, headers={"Accept": "text/html", "Accept-Language": "ko-KR,ko;q=0.9",
                          "User-Agent": "KoreanMemeResearch-LocalCollector/0.1"}, method="GET")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            if "text/html" not in response.headers.get("Content-Type", "").lower():
                raise MetadataError("unexpected_content_type")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise MetadataError("response_too_large")
            return body
    except urllib.error.HTTPError as exc:
        reason = "rate_limited" if exc.code == 429 else "redirect_blocked" if 300 <= exc.code < 400 else "http_error"
        raise MetadataError(reason, exc.code) from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise MetadataError("transport_error") from None


def _json_assignment(html, name):
    decoder = json.JSONDecoder()
    pattern = r'(?:\b' + re.escape(name) + r'|window\s*\[\s*["\']' + re.escape(name) + r'["\']\s*\])\s*=\s*'
    for match in list(re.finditer(pattern, html))[:20]:
        try:
            value, _ = decoder.raw_decode(html[match.end():].lstrip())
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _video_objects(html):
    for match in re.finditer(r'<script\b[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script\s*>', html, re.I | re.S):
        try:
            value = json.loads(match.group(1))
        except (ValueError, RecursionError):
            continue
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(reversed(item))
            elif isinstance(item, dict):
                if item.get("@type") == "VideoObject" or isinstance(item.get("@type"), list) and "VideoObject" in item["@type"]:
                    yield item
                if isinstance(item.get("@graph"), list):
                    stack.extend(reversed(item["@graph"]))


def _date_info(raw):
    if not isinstance(raw, str) or not raw:
        return {"raw": raw, "timestamp": None, "date": None, "precision": "unknown"}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return {"raw": raw, "timestamp": None, "date": None, "precision": "invalid"}
        return {"raw": raw, "timestamp": None, "date": raw, "precision": "date"}
    try:
        parsed = utc(raw)
        return {"raw": raw, "timestamp": stamp(parsed), "date": parsed.date().isoformat(), "precision": "timestamp"}
    except (ValueError, TypeError):
        return {"raw": raw, "timestamp": None, "date": None, "precision": "unknown"}


def parse_watch_metadata(body, video_id, received_at, *, provenance="actual_collected", retention_days=30):
    if not isinstance(video_id, str) or not VIDEO.fullmatch(video_id):
        raise MetadataError("video_identity_mismatch")
    try:
        html = body.decode("utf-8-sig") if isinstance(body, bytes) else body
    except UnicodeError:
        raise MetadataError("invalid_html") from None
    if not isinstance(html, str):
        raise MetadataError("invalid_html")
    low = html.lower()
    if (re.search(r'<form\b[^>]*action=["\'][^"\']*consent\.(?:youtube|google)\.com', html, re.I)
            or "<title>before you continue to youtube" in low):
        raise MetadataError("consent_required")
    if ("our systems have detected unusual traffic" in low or '<title>sorry' in low
            or re.search(r'<(?:div|form)\b[^>]*(?:g-recaptcha|recaptcha-challenge)', html, re.I)):
        raise MetadataError("captcha_or_traffic_block")
    if "<title>sign in - google accounts" in low or 'id="gaia_loginform"' in low:
        raise MetadataError("login_required")
    player = _json_assignment(html, "ytInitialPlayerResponse")
    details = player.get("videoDetails", {})
    details = details if isinstance(details, dict) else {}
    micro = player.get("microformat", {})
    micro = micro.get("playerMicroformatRenderer", {}) if isinstance(micro, dict) else {}
    micro = micro if isinstance(micro, dict) else {}
    player_id = details.get("videoId")
    if player_id is not None and player_id != video_id:
        raise MetadataError("video_identity_mismatch")
    playability = player.get("playabilityStatus", {})
    if isinstance(playability, dict) and playability.get("status") == "LOGIN_REQUIRED":
        raise MetadataError("login_required")
    linked = {}
    for item in _video_objects(html):
        urls = [item.get("url"), item.get("embedUrl"), item.get("contentUrl")]
        if any(isinstance(url, str) and re.search(r"(?:[?&]v=|/embed/|/shorts/)" + re.escape(video_id) + r"(?:[?&#/]|$)", url) for url in urls):
            linked = item
            break
    # A JSON-LD VideoObject lacking video identity is insufficient on its own;
    # other page cards and recommendations must not be mistaken for the target.
    if player_id != video_id and not linked:
        raise MetadataError("metadata_unavailable")
    fields = {}
    def pick(name, candidates):
        for source, value in candidates:
            if isinstance(value, str) and value:
                fields[name] = source
                return value
        return None
    title = pick("title", [("player.videoDetails.title", details.get("title")),
              ("player.microformat.title", _text(micro.get("title"))), ("jsonld.name", linked.get("name"))])
    description = pick("description", [("player.videoDetails.shortDescription", details.get("shortDescription")),
              ("player.microformat.description", _text(micro.get("description"))), ("jsonld.description", linked.get("description"))])
    channel_id = pick("channel_id", [("player.videoDetails.channelId", details.get("channelId")),
              ("player.microformat.externalChannelId", micro.get("externalChannelId"))])
    if channel_id and not CHANNEL.fullmatch(channel_id):
        channel_id = None
        fields.pop("channel_id", None)
    linked_author = linked.get("author", {})
    linked_author_name = linked_author.get("name") if isinstance(linked_author, dict) else linked_author
    author = pick("author", [("player.videoDetails.author", details.get("author")),
              ("player.microformat.ownerChannelName", micro.get("ownerChannelName")), ("jsonld.author.name", linked_author_name)])
    category = pick("category", [("player.microformat.category", micro.get("category")), ("jsonld.genre", linked.get("genre"))])
    published_raw = pick("publication", [("player.microformat.publishDate", micro.get("publishDate")), ("jsonld.datePublished", linked.get("datePublished"))])
    uploaded_raw = pick("upload", [("player.microformat.uploadDate", micro.get("uploadDate")), ("jsonld.uploadDate", linked.get("uploadDate"))])
    if not title:
        raise MetadataError("metadata_unavailable")
    publication, upload = _date_info(published_raw), _date_info(uploaded_raw)
    posted_at = publication["timestamp"]
    temporal_reason = None
    if posted_at and utc(posted_at) > utc(received_at):
        posted_at = None
        temporal_reason = "publication_timestamp_is_after_collection"
    elif not posted_at:
        temporal_reason = "No exact publication timestamp; date-only and upload dates are not converted to a publication instant."
    source_url = "https://www.youtube.com/watch?v=" + video_id
    text = title + ("\n" + description if description else "")
    version = {"video_id": video_id, "title": title, "description": description, "channel_id": channel_id,
               "author": author, "publication": publication, "upload": upload, "category": category}
    return {"schema_version": "youtube-watch-metadata-v1", "platform": "youtube", "source_id": video_id,
        "video_id": video_id, "observation_id": "youtube:watch:" + video_id + ":" + digest(version)[:24],
        "parent_id": None, "channel_id": channel_id, "author": author, "category": category,
        "title": title, "description": description, "text": text,
        "context": {"title": title, "description": description, "channel_label": author, "category": category},
        "modality": "video_metadata", "source_url": source_url, "metadata_field_sources": fields,
        "publication_metadata": publication, "upload_metadata": upload,
        "posted_at": posted_at, "posted_date": publication["date"], "posted_precision": publication["precision"],
        "uploaded_at": upload["timestamp"], "uploaded_date": upload["date"], "upload_precision": upload["precision"],
        "updated_at": None, "collected_at": stamp(received_at), "available_at": stamp(received_at),
        "temporal_eligibility": posted_at is not None, "temporal_exclusion_reason": temporal_reason,
        "provenance": provenance, "sample_method": {"mode": "public_watch_html", "posthoc_expansion": True,
                         "unseeded_discovery": False, "video_and_audio_examined": False},
        "discovery_evaluation_eligible": False, "original_or_repost": "unknown", "independence_status": "unknown",
        "meme_label": "unassessed", "human_review_status": "not_reviewed", "evidence_links": [source_url],
        "retention_due_at": stamp(utc(received_at) + timedelta(days=retention_days))}


def validate_config(config):
    allowed = {"version", "mode", "video_ids", "source_run_id", "source_manifest", "source_observations",
               "selection_reason", "timeout_seconds", "max_response_bytes", "retention_days"}
    if not isinstance(config, dict) or config.keys() - allowed:
        raise ValueError("Unknown configuration field")
    out = {"version": "youtube-watch-metadata-v1", "mode": config.get("mode"), "video_ids": config.get("video_ids"),
        "source_run_id": config.get("source_run_id"), "source_manifest": config.get("source_manifest"),
        "source_observations": config.get("source_observations"), "selection_reason": config.get("selection_reason"),
        "timeout_seconds": config.get("timeout_seconds", 15), "max_response_bytes": config.get("max_response_bytes", 8_000_000),
        "retention_days": config.get("retention_days", 30)}
    if out["mode"] != "posthoc_search_expansion":
        raise ValueError("Watch metadata must remain posthoc_search_expansion")
    if (not isinstance(out["video_ids"], list) or not 1 <= len(out["video_ids"]) <= 3
            or any(not isinstance(value, str) or not VIDEO.fullmatch(value) for value in out["video_ids"])
            or len(set(out["video_ids"])) != len(out["video_ids"])):
        raise ValueError("Provide one to three distinct video IDs")
    for field in ("source_run_id", "source_manifest", "source_observations", "selection_reason"):
        if not isinstance(out[field], str) or not out[field].strip():
            raise ValueError("Origin and selection fields are required")
    for field, low, high in (("timeout_seconds", 1, 60), ("max_response_bytes", 1024, 12_000_000), ("retention_days", 1, 30)):
        if type(out[field]) is not int or not low <= out[field] <= high:
            raise ValueError("Invalid bounded setting")
    return out


def _path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _save_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def collect(config, *, output_dir="data/youtube_watch", logdb="logs/research.sqlite3", transport=None,
            retry_of=None, retry_reason=None, now=None, test_only=False):
    if (transport is not None or now is not None) and not test_only:
        raise ValueError("Injected transport or clock requires test_only=True")
    config = validate_config(config)
    snapshots = [{"id": field, "sha256": hash_file(_path(config[field]))} for field in ("source_manifest", "source_observations")]
    spec = normalize_spec({"objective": "Resolve posthoc public search evidence through bounded watch-page HTML metadata",
        "stage": "offline_validation" if test_only else "access_probe", "parameters": {"config": config, "test_only": test_only},
        "data_snapshots": snapshots, "code_hashes": {name: hash_file(ROOT / "src" / name) for name in
                  ("youtube_video_metadata.py", "youtube_web_search.py", "research_log.py")},
        "config_hashes": {"config": digest(config)}, "prompt_hashes": {}, "modality": ["video_metadata"],
        "provenance": "ai_synthetic" if test_only else "actual_collected"})
    log = ResearchLog(logdb)
    run = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    clock_fn = now or (lambda: datetime.now(timezone.utc))
    transport_fn = transport or fetch_watch_page
    provenance = "ai_synthetic" if test_only else "actual_collected"
    began = stamp(clock_fn())
    destination = Path(output_dir) / run["run_id"]
    observations, requests, issues, raw_files = [], [], [], []
    try:
        destination.mkdir(parents=True, exist_ok=False)
        (destination / "raw").mkdir()
        source_manifest = read_json(_path(config["source_manifest"]))
        if source_manifest.get("run_id") != config["source_run_id"]:
            raise MetadataError("source_validation_failed")
        source_rows = {}
        with _path(config["source_observations"]).open(encoding="utf-8-sig") as handle:
            for line in handle:
                row = json.loads(line)
                source_rows.setdefault(row.get("video_id"), []).append(row)
        for video_id in config["video_ids"]:
            sources = source_rows.get(video_id, [])
            if (not sources or any(row.get("provenance") != provenance or utc(row["available_at"]) > utc(began) for row in sources)):
                raise MetadataError("source_validation_failed")
        if any(hash_file(_path(config[item["id"]])) != item["sha256"] for item in snapshots):
            raise MetadataError("source_validation_failed")
        stop = False
        for video_id in config["video_ids"]:
            if stop:
                requests.append({"video_id": video_id, "status": "not_requested", "reason": "prior_access_block"})
                continue
            url = "https://www.youtube.com/watch?" + urllib.parse.urlencode({"v": video_id, "hl": "ko"})
            request = {"video_id": video_id, "source_url": url, "started_at": stamp(clock_fn()), "status": "started"}
            requests.append(request)
            try:
                body = transport_fn(url, config["timeout_seconds"], config["max_response_bytes"])
                received = stamp(clock_fn())
                if isinstance(body, str):
                    body = body.encode("utf-8")
                if not isinstance(body, bytes):
                    raise MetadataError("invalid_html")
                if len(body) > config["max_response_bytes"]:
                    raise MetadataError("response_too_large")
                raw_path = destination / "raw" / (video_id + ".html")
                with raw_path.open("xb") as handle:
                    handle.write(body)
                raw_files.append(raw_path)
                request.update(received_at=received, raw_file=str(raw_path.resolve()), raw_sha256=hash_file(raw_path),
                               raw_bytes=len(body), retention_due_at=stamp(utc(received) + timedelta(days=config["retention_days"])))
                row = parse_watch_metadata(body, video_id, received, provenance=provenance, retention_days=config["retention_days"])
                row.update(source_run_id=config["source_run_id"], source_observation_ids=[item["observation_id"] for item in source_rows[video_id]], test_only=test_only)
                observations.append(row)
                request.update(status="succeeded", channel_id_available=row["channel_id"] is not None,
                               exact_publication_time_available=row["posted_at"] is not None,
                               posted_precision=row["posted_precision"], description_available=bool(row["description"]))
            except MetadataError as exc:
                request.update(status="failed", reason=exc.reason, http_status=exc.status)
                issues.append({"video_id": video_id, "reason": exc.reason, "http_status": exc.status})
                if exc.reason in {"rate_limited", "consent_required", "captcha_or_traffic_block", "login_required", "redirect_blocked"}:
                    stop = True
            except Exception:
                request.update(status="failed", reason="transport_error")
                issues.append({"video_id": video_id, "reason": "transport_error"})
    except MetadataError as exc:
        issues.append({"video_id": None, "reason": exc.reason})
    except Exception:
        issues.append({"video_id": None, "reason": "collector_error"})
    status = "partial" if observations and issues else "failed" if issues else "completed"
    manifest = {"schema_version": "youtube-watch-metadata-manifest-v1", "run_id": run["run_id"], "status": status,
        "started_at": began, "finished_at": stamp(clock_fn()), "config": config, "provenance": provenance, "test_only": test_only,
        "http_attempts": sum(request["status"] != "not_requested" for request in requests), "observations": len(observations),
        "requests": requests, "issues": issues, "channel_ids_available": sum(bool(row["channel_id"]) for row in observations),
        "exact_publication_timestamps": sum(row["posted_at"] is not None for row in observations),
        "date_only_publications": sum(row["posted_precision"] == "date" for row in observations),
        "paid_api_calls": 0, "model_calls": 0, "human_review_minutes": 0, "verified_meme_count": None,
        "posthoc_expansion": True, "discovery_evaluation_eligible": False, "history_reconstruction": False,
        "video_and_audio_examined": False, "input_snapshots": snapshots,
        "limitations": ["Public source metadata only; no video/audio content examination or meme accuracy claim.",
            "Query-selected posthoc expansion remains excluded from independent discovery evaluation.",
            "Date-only publication labels and upload dates are not converted into exact publication timestamps.",
            "All metadata becomes available at actual retrieval time, never retroactively at publication time.",
            "Channel attribution does not establish the origin of an expression or distinguish copies from independent reuse."], "artifacts": []}
    try:
        destination.mkdir(parents=True, exist_ok=True)
        observation_path = destination / "observations.jsonl"
        with observation_path.open("x", encoding="utf-8", newline="\n") as handle:
            for row in observations:
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        for path in [observation_path, *raw_files]:
            manifest["artifacts"].append({"path": str(path.resolve()), "sha256": hash_file(path), "bytes": path.stat().st_size})
        manifest_path = destination / "manifest.json"
        _save_json(manifest_path, manifest)
        log.finish(run["run_id"], status="failed" if issues else "succeeded",
            notes=f"Watch metadata {status}; requests={manifest['http_attempts']}, observations={len(observations)}, issues={len(issues)}, test_only={test_only}. No video/audio or meme accuracy evaluation.",
            artifacts=[manifest_path, observation_path, *raw_files], actual_cost={"amount": 0, "currency": "USD", "api_units": 0, "human_minutes": 0})
    except Exception:
        if log.status(run["run_id"])["status"] == "started":
            log.finish(run["run_id"], status="failed", notes="Watch metadata artifact finalization failed. No success claim.")
        raise MetadataError("artifact_finalization_failed") from None
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", default="data/youtube_watch")
    parser.add_argument("--db", default="logs/research.sqlite3")
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = parser.parse_args(argv)
    try:
        result = collect(read_json(args.config), output_dir=args.output_dir, logdb=args.db,
                         retry_of=args.retry_of, retry_reason=args.retry_reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "completed" else 2
    except DuplicateRunError as exc:
        print(json.dumps({"error": "duplicate_run", "existing_run_id": exc.existing["run_id"]}))
        return 3
    except Exception as exc:
        print(json.dumps({"error": exc.reason if isinstance(exc, MetadataError) else "invalid_config_or_local_error"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
