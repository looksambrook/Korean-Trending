"""Bounded, keyless collection of the official YouTube Atom feed.

This observes recent channel metadata, not comments, captions or the whole
platform. Transport fixtures are always labelled synthetic. Every external
attempt is registered before HTTP and no automatic retries are performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_log import DuplicateRunError, ResearchLog, hash_file, read_json

NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "m": "http://search.yahoo.com/mrss/"}
CHANNEL = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")
VIDEO = re.compile(r"[A-Za-z0-9_-]{11}\Z")


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Timestamp requires an explicit timezone")
    return value.astimezone(timezone.utc)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def save_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def save_lines(path, rows):
    with Path(path).open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_feed(body, channel_id, received_at, *, max_entries=20, retention_days=30,
               provenance="actual_collected"):
    if not CHANNEL.fullmatch(channel_id):
        raise ValueError("Invalid channel ID")
    received = utc(received_at)
    if not isinstance(body, bytes) or b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("Expected XML bytes without DTD or entity declarations")
    root = ET.fromstring(body)
    if root.tag != "{" + NS["a"] + "}feed":
        raise ValueError("Not an Atom feed")
    # Actual feed-level IDs sometimes omit the UC prefix; the entry IDs do not.
    feed_channel = root.findtext("yt:channelId", namespaces=NS)
    if feed_channel and feed_channel not in {channel_id, channel_id[2:]}:
        raise ValueError("Feed channel does not match the requested channel")
    entries = root.findall("a:entry", NS)
    observations, exclusions, seen = [], [], set()
    label = root.findtext("a:title", default="", namespaces=NS)
    for index, entry in enumerate(entries):
        video_id = entry.findtext("yt:videoId", default="", namespaces=NS)
        entry_channel = entry.findtext("yt:channelId", default="", namespaces=NS)
        reason = None
        if not VIDEO.fullmatch(video_id):
            reason = "invalid_video_id"
        elif entry_channel != channel_id:
            reason = "entry_channel_mismatch"
        elif video_id in seen:
            reason = "duplicate_video_id"
        if reason is None:
            try:
                published = utc(entry.findtext("a:published", namespaces=NS))
                updated = utc(entry.findtext("a:updated", namespaces=NS))
                if max(published, updated) > received:
                    reason = "future_dated"
            except (TypeError, ValueError):
                reason = "missing_or_invalid_timestamp"
        if reason is None and len(observations) >= max_entries:
            reason = "configured_entry_limit"
        if reason:
            exclusions.append({"channel_id": channel_id, "video_id": video_id,
                               "entry_index": index, "reason": reason})
            continue
        seen.add(video_id)
        title = entry.findtext("a:title", default="", namespaces=NS)
        description = entry.findtext("m:group/m:description", default="", namespaces=NS)
        text = "\n".join(part for part in (title, description) if part)
        if not text.strip():
            exclusions.append({"channel_id": channel_id, "video_id": video_id,
                               "entry_index": index, "reason": "empty_text"})
            continue
        url = "https://www.youtube.com/watch?v=" + video_id
        statistics = entry.find("m:group/m:community/m:statistics", NS)
        views = statistics.get("views") if statistics is not None else None
        engagement = {"view_count": int(views)} if views and views.isdigit() else {}
        # One title+description document per video; neither is an independent use.
        version = digest((video_id + "\n" + updated.isoformat() + "\n" + text).encode("utf-8"))[:20]
        observations.append({
            "observation_id": "youtube:feed:" + video_id + ":" + version,
            "platform": "youtube", "source_id": video_id, "parent_id": None,
            "video_id": video_id, "channel_id": channel_id,
            "text": text, "context": {"title": title, "description": description, "channel_label": label},
            "modality": "video_metadata", "posted_at": published.isoformat(),
            "updated_at": updated.isoformat(), "collected_at": received.isoformat(),
            "available_at": received.isoformat(), "provenance": provenance,
            "source_url": url, "engagement": engagement,
            "sample_method": {"mode": "channel_feed", "known_meme_name_seed": False,
                              "selection": "convenience_access_probe", "past_completeness": "unknown"},
            "original_or_repost": "unknown", "independence_status": "unknown",
            "evidence_links": [url],
            "retention_due_at": (received + timedelta(days=retention_days)).isoformat()
        })
    return {"observations": observations, "exclusions": exclusions,
            "feed": {"channel_id": channel_id, "channel_label": label, "entry_count": len(entries),
                     "description_entries": sum(bool(o["context"]["description"]) for o in observations),
                     "pagination_available": False, "historical_completeness": "unknown"}}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected redirect; inspect the logged endpoint before retrying")


def http_get(url, timeout, max_bytes):
    request = urllib.request.Request(url, headers={
        "User-Agent": "KoreanMemeResearch/0.1 (bounded public Atom access check)",
        "Accept": "application/atom+xml, application/xml, text/xml"})
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("Response exceeds configured byte limit")
        return {"body": body, "status": response.status,
                "content_type": response.headers.get("Content-Type", "")}


def validate_config(config):
    result = dict(config)
    if not isinstance(result.get("snapshot_id"), str) or not result["snapshot_id"].strip():
        raise ValueError("snapshot_id must identify a planned observation round")
    channels = result.get("channels")
    if not isinstance(channels, list) or not 1 <= len(channels) <= 20:
        raise ValueError("Choose 1 to 20 channels per bounded run")
    ids = []
    for item in channels:
        channel_id = item.get("channel_id", "") if isinstance(item, dict) else ""
        if not CHANNEL.fullmatch(channel_id):
            raise ValueError("Every channel needs a valid UC channel ID")
        ids.append(channel_id)
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate channel ID in cohort")
    for field, default, lower, upper in [
        ("max_entries_per_channel", 20, 1, 100), ("timeout_seconds", 15, 1, 60),
        ("max_response_bytes", 2000000, 1024, 5000000), ("retention_days", 30, 1, 30)]:
        result.setdefault(field, default)
        if type(result[field]) is not int or not lower <= result[field] <= upper:
            raise ValueError("Invalid bound: " + field)
    return result


def collect(config, *, output_dir, logdb="logs/research.sqlite3", transport=None,
            retry_of=None, retry_reason=None, now=None):
    config = validate_config(config)
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Output already exists; preserve previous snapshots")
    synthetic = transport is not None
    provenance = "ai_synthetic" if synthetic else "actual_collected"
    clock = now or (lambda: datetime.now(timezone.utc))
    if now is not None and not synthetic:
        raise ValueError("A substituted clock is allowed only with fixture transport")
    transport = transport or http_get
    spec = {"objective": "Collect bounded official YouTube channel Atom metadata",
            "stage": "access_probe", "parameters": {"config": config, "transport": "fixture" if synthetic else "https",
                                                      "no_named_meme_seed": True},
            "data_snapshots": [], "code_hashes": {"collector": hash_file(__file__),
            "registry": hash_file(Path(__file__).with_name("research_log.py"))},
            "prompt_hashes": {}, "config_hashes": {}, "modality": ["text", "metadata"], "provenance": provenance}
    log = ResearchLog(logdb)
    run = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    try:
        output.mkdir(parents=True, exist_ok=False)
        (output / "raw").mkdir()
        save_json(output / "spec.json", spec)
        observations, exclusions, requests = [], [], []
        for channel in config["channels"]:
            cid = channel["channel_id"]
            url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + cid
            attempt = {"channel_id": cid, "url": url, "started_at": utc(clock()).isoformat()}
            try:
                response = transport(url, config["timeout_seconds"], config["max_response_bytes"])
                received = utc(clock()).isoformat()
                attempt.update({"received_at": received, "http_status": response["status"],
                                "content_type": response.get("content_type", "")})
                if response["status"] != 200:
                    raise ValueError("Non-200 HTTP status")
                body = response["body"]
                if len(body) > config["max_response_bytes"]:
                    raise ValueError("Response exceeds configured byte limit")
                raw_path = output / "raw" / (cid + ".xml")
                with raw_path.open("xb") as stream:
                    stream.write(body)
                attempt.update({"raw_file": "raw/" + raw_path.name, "sha256": digest(body), "bytes": len(body)})
                parsed = parse_feed(body, cid, received,
                    max_entries=config["max_entries_per_channel"], retention_days=config["retention_days"], provenance=provenance)
                observations.extend(parsed["observations"])
                exclusions.extend(parsed["exclusions"])
                attempt.update({"status": "succeeded", "feed": parsed["feed"],
                                "observations": len(parsed["observations"]), "exclusions": len(parsed["exclusions"])})
            except Exception as exc:
                attempt.update({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:400]})
                if isinstance(exc, urllib.error.HTTPError):
                    attempt["http_status"] = exc.code
            requests.append(attempt)
        successful = sum(r["status"] == "succeeded" for r in requests)
        status = "complete" if successful == len(requests) else "partial" if successful else "failed"
        manifest = {"run_id": run["run_id"], "status": status, "snapshot_id": config["snapshot_id"],
                    "provenance": provenance, "finished_at": utc(clock()).isoformat(),
                    "history_reconstruction": False, "source_method": "official_channel_atom_feed",
                    "counts": {"requests": len(requests), "channels_ok": successful,
                               "channels_failed": len(requests)-successful,
                               "observations": len(observations), "exclusions": len(exclusions)},
                    "requests": requests,
                    "files": {"observations": "observations.jsonl", "exclusions": "exclusions.jsonl"},
                    "limitations": ["Convenience channel cohort; no platform-wide or historical completeness",
                                    "Metadata only; no comments, captions, audio or video",
                                    "Repeated phrases are candidates, not confirmed memes",
                                    "Retention due date is a local review deadline, not a licence"],
                    "cost": {"paid_api_calls": 0, "model_calls": 0, "human_review_minutes": None}}
        save_lines(output / "observations.jsonl", observations)
        save_lines(output / "exclusions.jsonl", exclusions)
        save_json(output / "manifest.json", manifest)
        log.finish(run["run_id"], status="succeeded" if status == "complete" else "failed",
                   notes=f"Feed scope {status}; {successful}/{len(requests)} channels; {len(observations)} metadata observations. No meme verification performed.",
                   artifacts=sorted(path for path in output.rglob("*") if path.is_file()),
                   actual_cost={"amount": 0, "currency": "USD", "human_minutes": None})
        return manifest
    except BaseException as exc:
        log.finish(run["run_id"], status="failed", notes="Collection aborted: " + type(exc).__name__)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--db", default="logs/research.sqlite3")
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = parser.parse_args()
    try:
        result = collect(read_json(args.config), output_dir=args.output_dir, logdb=args.db,
                         retry_of=args.retry_of, retry_reason=args.retry_reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "complete" and result["counts"]["observations"] else 2
    except DuplicateRunError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
