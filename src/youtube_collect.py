"""Bounded, logged YouTube Data API collection of text and source context.

This collects observations, not verified memes. An API key is read only from
YOUTUBE_API_KEY or a caller argument; neither keys nor HTTP exception messages
are persisted. There are no automatic retries and no video/audio downloads.
The default transport only uses the official API host and blocks redirects.

Inspect a config without HTTP:
    python src/youtube_collect.py inspect --config configs/youtube_collect_smoke_v01.json
Collect once (a run is registered before checking credentials or sending HTTP):
    python src/youtube_collect.py collect --config CONFIG --output-dir data/youtube_api

An injected transport requires test_only=True and produces ai_synthetic records.
It must have signature transport(endpoint, params, headers, timeout) -> dict.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_log import DuplicateRunError, ResearchLog, hash_file, normalize_spec, read_json

ROOT = Path(__file__).resolve().parents[1]
API_BASE = "https://www.googleapis.com/youtube/v3/"
SCHEMA_VERSION = "youtube-observation-v1"
MAX_RESPONSE_BYTES = 2_000_000
ENDPOINTS = {"channels", "playlistItems", "search", "videos", "commentThreads", "comments"}
ERROR_REASONS = {
    "commentsDisabled", "videoNotFound", "channelNotFound", "playlistNotFound",
    "commentNotFound", "forbidden", "quotaExceeded", "dailyLimitExceeded",
    "rateLimitExceeded", "keyInvalid", "accessNotConfigured", "badRequest",
    "invalidParameter", "invalidPageToken", "processingFailure", "backendError",
    "ipRefererBlocked", "insufficientPermissions", "operationNotSupported",
    "channelForbidden", "playlistForbidden", "invalidCriteria", "notFound",
}
INTERNAL_REASONS = {
    "api_error", "invalid_endpoint", "invalid_request_parameter", "credential_in_parameter",
    "response_too_large", "redirect_blocked", "transport_error", "invalid_json",
    "malformed_response", "missing_credential", "local_call_cap", "transport_or_response_error",
    "channel_resolution_empty_or_ambiguous", "missing_uploads_playlist", "artifact_finalization_failed",
}
REQUEST_PARAMS = {
    "channels": {"part", "id", "forHandle"},
    "playlistItems": {"part", "playlistId", "maxResults", "pageToken"},
    "search": {"part", "type", "order", "regionCode", "relevanceLanguage", "q", "publishedAfter", "publishedBefore", "maxResults", "pageToken"},
    "videos": {"part", "id"},
    "commentThreads": {"part", "videoId", "order", "textFormat", "maxResults", "pageToken"},
    "comments": {"part", "parentId", "textFormat", "maxResults", "pageToken"},
}
LIMIT_DEFAULTS = {
    "max_videos": 3,
    "max_videos_per_channel": 3,
    "max_comments_total": 20,
    "max_top_level_comments_per_video": 5,
    "max_replies_per_thread": 2,
    "max_playlist_pages_per_channel": 1,
    "max_search_pages": 1,
    "max_comment_pages_per_video": 1,
    "max_reply_pages_per_thread": 1,
    "max_search_calls": 1,
    "max_other_calls": 20,
}
# This first collector intentionally hard-caps a small access test. Raising the
# scope requires a deliberate code/config change, not an unexpected API page.
LIMIT_MAX = {key: 20 for key in LIMIT_DEFAULTS}
LIMIT_MAX.update(max_videos=6, max_videos_per_channel=6, max_comments_total=120,
                 max_top_level_comments_per_video=20, max_replies_per_thread=20,
                 max_search_calls=3, max_other_calls=50)
COMMENT_FIELDS = "id,snippet(parentId,textDisplay,publishedAt,updatedAt,likeCount)"
FIELDS = {
    "channels": "items(id,contentDetails/relatedPlaylists/uploads)",
    "playlistItems": "nextPageToken,items(contentDetails(videoId,videoPublishedAt))",
    "search": "nextPageToken,items(id/videoId)",
    "videos": "items(id,snippet(channelId,title,description,publishedAt,defaultLanguage,defaultAudioLanguage),statistics(viewCount,likeCount,commentCount))",
    "commentThreads": "nextPageToken,items(id,snippet(totalReplyCount,topLevelComment(" + COMMENT_FIELDS + ")))",
    "comments": "nextPageToken,items(" + COMMENT_FIELDS + ")",
}
COMMENT_TREE = {"id": None, "snippet": {name: None for name in
                ("parentId", "textDisplay", "publishedAt", "updatedAt", "likeCount")}}
PROJECTIONS = {
    "channels": {"items": [{"id": None, "contentDetails": {"relatedPlaylists": {"uploads": None}}}]},
    "playlistItems": {"nextPageToken": None, "items": [{"contentDetails": {"videoId": None, "videoPublishedAt": None}}]},
    "search": {"nextPageToken": None, "items": [{"id": {"videoId": None}}]},
    "videos": {"items": [{"id": None, "snippet": {name: None for name in
                ("channelId", "title", "description", "publishedAt", "defaultLanguage", "defaultAudioLanguage")},
                "statistics": {name: None for name in ("viewCount", "likeCount", "commentCount")}}]},
    "commentThreads": {"nextPageToken": None, "items": [{"id": None, "snippet":
                {"totalReplyCount": None, "topLevelComment": COMMENT_TREE}}]},
    "comments": {"nextPageToken": None, "items": [COMMENT_TREE]},
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Timestamp requires an explicit timezone")
    return value.astimezone(timezone.utc)


def _clock():
    return datetime.now(timezone.utc)


def _stamp(value):
    return _utc(value).isoformat()


class CollectionError(Exception):
    """Only fixed, non-secret reason codes may cross the transport boundary."""
    def __init__(self, reason, http_status=None):
        self.reason = reason if isinstance(reason, str) and reason in ERROR_REASONS | INTERNAL_REASONS else "api_error"
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        super().__init__(self.reason)


def _error_from_payload(payload, status=None):
    reason = "api_error"
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        errors = payload["error"].get("errors", [])
        if isinstance(errors, list):
            for error in errors:
                candidate = error.get("reason") if isinstance(error, dict) else None
                if candidate in ERROR_REASONS:
                    reason = candidate
                    break
    return CollectionError(reason, status)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def official_transport(endpoint, params, headers, timeout):
    """Single GET to the literal official host. Never retry or echo a URL/key."""
    if endpoint not in ENDPOINTS:
        raise CollectionError("invalid_endpoint")
    if params.keys() - REQUEST_PARAMS[endpoint] - {"fields"}:
        raise CollectionError("invalid_request_parameter")
    credential = next((value for name, value in headers.items() if name.lower() == "x-goog-api-key"), None)
    if credential and any(isinstance(value, str) and credential in value for value in params.values()):
        raise CollectionError("credential_in_parameter")
    url = API_BASE + endpoint + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise CollectionError("response_too_large")
    except urllib.error.HTTPError as exc:
        status = exc.code
        if 300 <= status < 400:
            raise CollectionError("redirect_blocked", status) from None
        try:
            payload = json.loads(exc.read(MAX_RESPONSE_BYTES))
        except (ValueError, OSError):
            payload = {}
        raise _error_from_payload(payload, status) from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise CollectionError("transport_error") from None
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CollectionError("invalid_json") from None
    return payload


def _project(value, tree):
    if tree is None:
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise CollectionError("malformed_response")
        return value
    if isinstance(tree, list):
        if not isinstance(value, list):
            raise CollectionError("malformed_response")
        return [_project(item, tree[0]) for item in value]
    if not isinstance(value, dict):
        raise CollectionError("malformed_response")
    return {key: _project(value[key], subtree) for key, subtree in tree.items() if key in value}


def _redact(value, credential):
    if isinstance(value, str):
        return value.replace(credential, "[CREDENTIAL_REDACTED]") if credential else value
    if isinstance(value, list):
        return [_redact(item, credential) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, credential) for key, item in value.items()}
    return value


class APIClient:
    def __init__(self, *, api_key, limits, timeout=15, transport=None, now=None, on_response=None):
        self._credential = api_key
        self.limits = limits
        self.timeout = timeout
        self.transport = transport or official_transport
        self.now = now or _clock
        self.on_response = on_response
        self.calls = {"search": 0, "other": 0}
        self.events = []
        self.blocked_buckets = {}

    def get(self, endpoint, **params):
        if endpoint not in ENDPOINTS:
            raise CollectionError("invalid_endpoint")
        if params.keys() - REQUEST_PARAMS[endpoint]:
            raise CollectionError("invalid_request_parameter")
        if not self._credential:
            raise CollectionError("missing_credential")
        if any(isinstance(value, str) and self._credential in value for value in params.values()):
            raise CollectionError("credential_in_parameter")
        bucket = "search" if endpoint == "search" else "other"
        if bucket in self.blocked_buckets:
            raise CollectionError(self.blocked_buckets[bucket])
        if self.calls[bucket] >= self.limits["max_" + bucket + "_calls"]:
            raise CollectionError("local_call_cap")
        params = {**params, "fields": FIELDS[endpoint]}
        # Sensitive data never enters params, URLs, events or raw response paths.
        headers = {"X-Goog-Api-Key": self._credential, "Accept": "application/json",
                   "User-Agent": "KoreanMemeResearch-LocalCollector/0.1"}
        self.calls[bucket] += 1
        event = {"endpoint": endpoint, "bucket": bucket, "attempt": sum(self.calls.values()),
                 "request_started_at": _stamp(self.now())}
        self.events.append(event)
        try:
            payload = self.transport(endpoint, dict(params), headers, self.timeout)
            received_at = _stamp(self.now())
            if not isinstance(payload, dict):
                raise CollectionError("malformed_response")
            if "error" in payload:
                raise _error_from_payload(payload)
            if "items" not in payload or not isinstance(payload["items"], list):
                raise CollectionError("malformed_response")
            data = _redact(_project(payload, PROJECTIONS[endpoint]), self._credential)
            event.update(status="succeeded", received_at=received_at, returned_items=len(data["items"]))
            if self.on_response:
                self.on_response(event, data)
            return data, received_at
        except CollectionError as exc:
            event.update(status="failed", reason=exc.reason, http_status=exc.http_status)
            if exc.reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
                self.blocked_buckets[bucket] = exc.reason
            elif exc.reason in {"keyInvalid", "accessNotConfigured", "ipRefererBlocked"}:
                self.blocked_buckets.update(search=exc.reason, other=exc.reason)
            raise
        except Exception:
            # Arbitrary transport exceptions may include a key/header/URL.
            event.update(status="failed", reason="transport_or_response_error", http_status=None)
            raise CollectionError("transport_or_response_error") from None


def validate_config(config):
    """Normalize a deliberately small, explicit collection scope."""
    if not isinstance(config, dict):
        raise ValueError("Config must be an object")
    allowed = {"version", "mode", "channels", "video_ids", "query", "query_kind", "known_meme_seed_names",
               "selection_reason", "limits", "publication_start", "publication_end", "comment_order",
               "region_code", "relevance_language", "timeout_seconds", "retention_days"}
    if config.keys() - allowed:
        raise ValueError("Unknown configuration field")
    out = {"version": "youtube-collect-v1", "mode": config.get("mode"),
           "channels": config.get("channels", []), "video_ids": config.get("video_ids", []),
           "query": config.get("query"), "query_kind": config.get("query_kind", "no_named_seed"),
           "known_meme_seed_names": config.get("known_meme_seed_names", []),
           "selection_reason": config.get("selection_reason", ""),
           "publication_start": config.get("publication_start"), "publication_end": config.get("publication_end"),
           "comment_order": config.get("comment_order", "time"), "region_code": config.get("region_code", "KR"),
           "relevance_language": config.get("relevance_language", "ko"),
           "timeout_seconds": config.get("timeout_seconds", 15), "retention_days": config.get("retention_days", 30)}
    supplied = config.get("limits", {})
    if not isinstance(supplied, dict) or supplied.keys() - LIMIT_DEFAULTS.keys():
        raise ValueError("Unknown collection limit")
    out["limits"] = {**LIMIT_DEFAULTS, **supplied}
    for name, value in out["limits"].items():
        minimum = 1 if name in {"max_videos", "max_videos_per_channel", "max_playlist_pages_per_channel", "max_search_pages"} else 0
        if type(value) is not int or not minimum <= value <= LIMIT_MAX[name]:
            raise ValueError("Invalid bounded collection limit: " + name)
    if out["mode"] not in {"channels", "search", "videos"}:
        raise ValueError("mode must be channels, search, or videos")
    if out["query_kind"] not in {"known_name_lookup", "topic", "no_named_seed", "diagnostic"}:
        raise ValueError("Invalid query_kind")
    if not isinstance(out["selection_reason"], str) or not out["selection_reason"].strip():
        raise ValueError("A selection_reason is required before collection")
    names = out["known_meme_seed_names"]
    if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError("known_meme_seed_names must be a list of nonempty names")
    if out["query_kind"] == "known_name_lookup" and not names:
        raise ValueError("Known-name lookup requires its names to be declared")
    if out["query_kind"] != "known_name_lookup" and names:
        raise ValueError("Named seeds cannot be labeled independent discovery")
    if out["mode"] == "channels":
        if out["video_ids"] or out["query"] is not None or out["query_kind"] != "no_named_seed":
            raise ValueError("Channel observation requires no query or video seeds")
        if not isinstance(out["channels"], list) or not 1 <= len(out["channels"]) <= 6:
            raise ValueError("Provide one to six channels")
        for channel in out["channels"]:
            if not isinstance(channel, dict) or set(channel) not in ({"id"}, {"handle"}):
                raise ValueError("Each channel requires exactly id or handle")
            if "id" in channel and not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", str(channel["id"])):
                raise ValueError("Invalid channel id")
            if "handle" in channel and (not isinstance(channel["handle"], str) or
                    not re.fullmatch(r"@?[\w.\-\u00b7]{3,30}", channel["handle"])):
                raise ValueError("Invalid channel handle")
    elif out["mode"] == "videos":
        if out["channels"] or out["query"] is not None or out["query_kind"] != "diagnostic":
            raise ValueError("Explicit video IDs must be labeled diagnostic")
        if not isinstance(out["video_ids"], list) or not 1 <= len(out["video_ids"]) <= out["limits"]["max_videos"]:
            raise ValueError("Provide video_ids within max_videos")
        if any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", item) for item in out["video_ids"]):
            raise ValueError("Invalid video id")
    else:
        if out["channels"] or out["video_ids"] or out["query_kind"] == "diagnostic":
            raise ValueError("Search requires an explicit search query kind")
        if out["query"] is not None and (not isinstance(out["query"], str) or not out["query"].strip()):
            raise ValueError("query must be a nonempty string or null")
        if out["query_kind"] == "no_named_seed" and out["query"] is not None:
            raise ValueError("Unseeded search cannot include a textual query; use topic or known_name_lookup")
        if out["query_kind"] != "no_named_seed" and out["query"] is None:
            raise ValueError("This search kind requires a query")
    if out["comment_order"] not in {"time", "relevance"}:
        raise ValueError("Invalid comment_order")
    if not isinstance(out["region_code"], str) or not re.fullmatch("[A-Z]{2}", out["region_code"]):
        raise ValueError("Invalid region_code")
    if not isinstance(out["relevance_language"], str) or not re.fullmatch("[a-z]{2,3}", out["relevance_language"]):
        raise ValueError("Invalid relevance_language")
    if type(out["timeout_seconds"]) is not int or not 1 <= out["timeout_seconds"] <= 60:
        raise ValueError("timeout_seconds must be 1 to 60")
    if type(out["retention_days"]) is not int or not 1 <= out["retention_days"] <= 30:
        raise ValueError("retention_days must be 1 to 30")
    for field in ("publication_start", "publication_end"):
        if out[field] is not None:
            out[field] = _stamp(out[field])
    if out["publication_start"] and out["publication_end"] and _utc(out["publication_start"]) >= _utc(out["publication_end"]):
        raise ValueError("publication_start must precede publication_end")
    return out


def _spec(config, test_only=False):
    return normalize_spec({
        "objective": "YouTube bounded text collection; validate access and provenance, not meme accuracy",
        "stage": "offline_validation" if test_only else "access_probe",
        "parameters": {"collector": "youtube_data_api", "config": config, "test_only": test_only},
        "data_snapshots": [],
        "code_hashes": {name: hash_file(ROOT / "src" / name) for name in ("youtube_collect.py", "research_log.py")},
        "config_hashes": {"normalized_config": _sha(config)}, "prompt_hashes": {},
        "modality": ["text", "metadata"], "provenance": "ai_synthetic" if test_only else "actual_collected",
    })


def inspect(config, *, logdb="logs/research.sqlite3"):
    config = validate_config(config)
    spec = _spec(config)
    return {"config": config, "duplicate_check": ResearchLog(logdb).check(spec),
            "http_requests_sent": 0, "credential_present": bool(os.environ.get("YOUTUBE_API_KEY")),
            "caps": config["limits"], "credential_source": "YOUTUBE_API_KEY environment variable",
            "note": "Readiness only. No API access or meme detection is verified by inspect."}


def _write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _count(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def collect(config, *, output_dir="data/youtube_api", logdb="logs/research.sqlite3", api_key=None,
            transport=None, retry_of=None, retry_reason=None, now=None, test_only=False):
    """Execute a bounded plan, preserving partial output and an immutable manifest.

    ``status=completed`` means the declared bounded collection succeeded; it
    never means complete YouTube coverage or verified meme discovery. A missing
    key is logged as a failure with zero requests. Retrying identical work needs
    the prior run ID and a reason. The output path is excluded from run identity.
    """
    if (transport is not None or now is not None) and not test_only:
        raise ValueError("Injected transport/clock requires test_only=True")
    config = validate_config(config)
    log = ResearchLog(logdb)
    run = log.begin(_spec(config, test_only), retry_of=retry_of, retry_reason=retry_reason)
    run_id = run["run_id"]
    clock_fn = now or _clock
    began_at = _stamp(clock_fn())
    destination = Path(output_dir) / run_id
    observations, issues, coverage, raw_paths, excluded, sample_references = [], [], [], [], [], {}
    seen_observations = set()
    limits = config["limits"]
    provenance = "ai_synthetic" if test_only else "actual_collected"
    client = None
    fatal = None

    def issue(scope, reason, source_id=None, http_status=None):
        issues.append({"scope": scope, "reason": reason, "source_id": source_id, "http_status": http_status})

    def response_writer(event, payload):
        path = destination / "raw" / (f"{event['attempt']:03d}_{event['endpoint']}.json")
        _write_json(path, {"provenance": provenance, "test_only": test_only,
                          "endpoint": event["endpoint"], "received_at": event["received_at"],
                          "retention_due_at": _stamp(_utc(event["received_at"]) + timedelta(days=config["retention_days"])),
                          "payload": payload})
        raw_paths.append(path)

    def pages(endpoint, params, *, scope, source_id, page_cap, item_cap):
        """Consume only a bounded number of pages; record why traversal stopped."""
        seen_tokens, token, consumed, page_count = set(), None, 0, 0
        result = {"scope": scope, "source_id": source_id, "pages": 0, "items_returned": 0,
                  "status": "bounded", "reason": "zero_configured_cap"}
        coverage.append(result)
        if not page_cap or not item_cap:
            return
        for page_count in range(1, page_cap + 1):
            request_params = {**params, "maxResults": min(item_cap - consumed, 50 if endpoint in {"playlistItems", "search"} else 100)}
            if token is not None:
                request_params["pageToken"] = token
            try:
                data, received_at = client.get(endpoint, **request_params)
            except CollectionError as exc:
                result.update(status="failed", reason=exc.reason)
                issue(scope, exc.reason, source_id, exc.http_status)
                return
            items = data["items"]
            if len(items) > request_params["maxResults"]:
                result.update(status="failed", reason="response_exceeds_requested_limit")
                issue(scope, "response_exceeds_requested_limit", source_id)
                return
            consumed += len(items)
            result.update(pages=page_count, items_returned=consumed)
            for item in items:
                yield item, received_at
            next_token = data.get("nextPageToken")
            if next_token is None or next_token == "":
                result.update(status="exhausted", reason="no_next_page")
                return
            if not isinstance(next_token, str) or len(next_token) > 4096 or next_token in seen_tokens or next_token == token:
                result.update(status="failed", reason="repeated_or_invalid_page_token")
                issue(scope, "repeated_or_invalid_page_token", source_id)
                return
            seen_tokens.add(next_token)
            if consumed >= item_cap:
                result.update(status="bounded", reason="item_cap_with_more_results")
                return
            token = next_token
        result.update(status="bounded", reason="page_cap_with_more_results")

    def observe(*, kind, source_id, video_id, channel_id, snippet, received_at, parent_id=None,
                context=None, engagement=None, text=None):
        if not isinstance(source_id, str) or not source_id:
            issue(kind, "missing_source_id")
            return False
        try:
            posted = _utc(snippet["publishedAt"])
            updated = _utc(snippet["updatedAt"]) if snippet.get("updatedAt") else None
            received = _utc(received_at)
        except (KeyError, ValueError, TypeError):
            excluded.append({"kind": kind, "source_id": source_id, "reason": "missing_or_invalid_timestamp"})
            issue(kind, "missing_or_invalid_timestamp", source_id)
            return False
        if posted > received or (updated is not None and updated > received):
            excluded.append({"kind": kind, "source_id": source_id, "reason": "future_dated"})
            issue(kind, "future_dated", source_id)
            return False
        if updated is not None and updated < posted:
            excluded.append({"kind": kind, "source_id": source_id, "reason": "updated_before_posted"})
            issue(kind, "updated_before_posted", source_id)
            return False
        if kind == "video" and ((config["publication_start"] and posted < _utc(config["publication_start"])) or
                                (config["publication_end"] and posted >= _utc(config["publication_end"]))):
            excluded.append({"kind": kind, "source_id": source_id, "reason": "outside_publication_window"})
            return False
        if not isinstance(text, str):
            issue(kind, "missing_text", source_id)
            return False
        # ID identifies source + textual/context version, not retrieval time or
        # mutable engagement. Repeated snapshots of the same text share the ID.
        content = {"kind": kind, "source_id": source_id, "parent_id": parent_id,
                   "text": text, "context": context or {}, "posted_at": _stamp(posted),
                   "updated_at": _stamp(updated) if updated else None}
        observation_id = "yt_" + _sha(content)
        if observation_id in seen_observations:
            return False
        seen_observations.add(observation_id)
        source_url = "https://www.youtube.com/watch?v=" + urllib.parse.quote(video_id, safe="")
        if kind != "video":
            source_url += "&lc=" + urllib.parse.quote(source_id, safe="")
        observations.append({"schema_version": SCHEMA_VERSION, "observation_id": observation_id,
            "platform": "youtube", "source_id": source_id, "parent_id": parent_id,
            "video_id": video_id, "channel_id": channel_id, "kind": kind,
            "text": text, "context": context or {}, "modality": "text", "source_url": source_url,
            "posted_at": _stamp(posted), "updated_at": _stamp(updated) if updated else None,
            "collected_at": received_at, "available_at": received_at,
            "availability_basis": "observed_response_time_not_historical_publication_time",
            "engagement": engagement or {}, "engagement_observed_at": received_at,
            "sample_method": {"mode": config["mode"], "query_kind": config["query_kind"],
                "query": config["query"], "known_meme_seed_names": config["known_meme_seed_names"],
                "comment_order": config["comment_order"], "selection_reason": config["selection_reason"],
                "selection_references": sample_references.get(video_id, [])},
            "original_or_repost": "unknown", "evidence_links": [source_url], "provenance": provenance,
            "test_only": test_only, "human_review_status": "not_reviewed", "meme_label": "unassessed",
            "retention_due_at": _stamp(received + timedelta(days=config["retention_days"]))})
        return True

    try:
        destination.mkdir(parents=True, exist_ok=False)
        (destination / "raw").mkdir()
        client = APIClient(api_key=api_key if api_key is not None else os.environ.get("YOUTUBE_API_KEY"),
            limits=limits, timeout=config["timeout_seconds"], transport=transport, now=clock_fn,
            on_response=response_writer)
        if not client._credential:
            raise CollectionError("missing_credential")
        selected = []

        def select(video_id, reference):
            if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                issue("selection", "missing_or_invalid_video_id")
                return
            sample_references.setdefault(video_id, []).append(reference)
            if video_id not in selected and len(selected) < limits["max_videos"]:
                selected.append(video_id)

        if config["mode"] == "channels":
            for channel in config["channels"]:
                if len(selected) >= limits["max_videos"]:
                    coverage.append({"scope": "channel", "source_id": channel.get("id", channel.get("handle")),
                                     "status": "bounded", "reason": "global_video_cap", "pages": 0, "items_returned": 0})
                    continue
                channel_label = channel.get("id", channel.get("handle"))
                try:
                    resolved, resolved_at = client.get("channels", part="contentDetails", **(
                        {"id": channel["id"]} if "id" in channel else {"forHandle": channel["handle"]}))
                    if len(resolved["items"]) != 1:
                        raise CollectionError("channel_resolution_empty_or_ambiguous")
                    item = resolved["items"][0]
                    channel_id = item["id"]
                    playlist = item["contentDetails"]["relatedPlaylists"]["uploads"]
                    if not isinstance(playlist, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", playlist):
                        raise CollectionError("missing_uploads_playlist")
                except CollectionError as exc:
                    issue("channel", exc.reason, channel_label, exc.http_status)
                    continue
                except (KeyError, TypeError):
                    issue("channel", "malformed_channel_response", channel_label)
                    continue
                cap = min(limits["max_videos_per_channel"], limits["max_videos"] - len(selected))
                for item, received_at in pages("playlistItems", {"part": "contentDetails", "playlistId": playlist},
                        scope="uploads_playlist", source_id=channel_id,
                        page_cap=limits["max_playlist_pages_per_channel"], item_cap=cap):
                    details = item.get("contentDetails", {})
                    select(details.get("videoId"), {"method": "uploads_playlist", "channel_id": channel_id,
                        "playlist_id": playlist, "selected_at": received_at})
        elif config["mode"] == "search":
            params = {"part": "snippet", "type": "video", "order": "date",
                      "regionCode": config["region_code"], "relevanceLanguage": config["relevance_language"]}
            if config["query"] is not None:
                params["q"] = config["query"]
            for key, field in (("publishedAfter", "publication_start"), ("publishedBefore", "publication_end")):
                if config[field]:
                    params[key] = config[field]
            for item, received_at in pages("search", params, scope="search", source_id=None,
                    page_cap=limits["max_search_pages"], item_cap=limits["max_videos"]):
                select(item.get("id", {}).get("videoId"), {"method": "search", "selected_at": received_at,
                       "query_kind": config["query_kind"]})
        else:
            for video_id in config["video_ids"]:
                select(video_id, {"method": "researcher_selected_diagnostic"})
        video_items, video_received_at = [], None
        if selected:
            data, video_received_at = client.get("videos", part="snippet,statistics", id=",".join(selected))
            video_items = data["items"]
            returned = {item.get("id") for item in video_items}
            for missing in sorted(set(selected) - returned):
                issue("video", "requested_video_not_returned", missing)
        comments_collected = 0
        for video in video_items:
            video_id = video.get("id")
            if video_id not in selected:
                issue("video", "unrequested_video_returned")
                continue
            snippet = video.get("snippet", {})
            channel_id = snippet.get("channelId")
            if not isinstance(channel_id, str) or not channel_id:
                issue("video", "missing_channel_id", video_id)
                continue
            title, description = snippet.get("title"), snippet.get("description", "")
            if not isinstance(title, str) or not isinstance(description, str):
                issue("video", "missing_text", video_id)
                continue
            context = {"video_title": title, "video_description": description,
                       "video_posted_at": snippet.get("publishedAt"),
                       "default_language": snippet.get("defaultLanguage"),
                       "default_audio_language": snippet.get("defaultAudioLanguage")}
            metrics = {key: _count(value) for key, value in video.get("statistics", {}).items()}
            if not observe(kind="video", source_id=video_id, video_id=video_id, channel_id=channel_id,
                    snippet=snippet, received_at=video_received_at, text=title + ("\n" + description if description else ""),
                    context=context, engagement=metrics):
                continue
            top_cap = min(limits["max_top_level_comments_per_video"], limits["max_comments_total"] - comments_collected)
            parents = []
            for thread, received_at in pages("commentThreads", {"part": "snippet", "videoId": video_id,
                    "order": config["comment_order"], "textFormat": "plainText"},
                    scope="top_level_comments", source_id=video_id,
                    page_cap=limits["max_comment_pages_per_video"], item_cap=top_cap):
                thread_snippet = thread.get("snippet", {})
                top = thread_snippet.get("topLevelComment", {})
                top_snippet = top.get("snippet", {})
                top_id = top.get("id")
                if observe(kind="comment", source_id=top_id, video_id=video_id, channel_id=channel_id,
                    snippet=top_snippet, received_at=received_at, parent_id=video_id,
                    text=top_snippet.get("textDisplay"), context={**context, "thread_id": thread.get("id")},
                    engagement={"likeCount": _count(top_snippet.get("likeCount")),
                                "totalReplyCount": _count(thread_snippet.get("totalReplyCount"))}):
                    comments_collected += 1
                    parents.append((top_id, top_snippet.get("textDisplay"), _count(thread_snippet.get("totalReplyCount"))))
            for parent_id, parent_text, reply_count in parents:
                cap = min(limits["max_replies_per_thread"], limits["max_comments_total"] - comments_collected)
                if reply_count == 0:
                    coverage.append({"scope": "replies", "source_id": parent_id, "status": "exhausted",
                                     "reason": "reported_zero_replies", "pages": 0, "items_returned": 0})
                    continue
                for reply, received_at in pages("comments", {"part": "snippet", "parentId": parent_id,
                        "textFormat": "plainText"}, scope="replies", source_id=parent_id,
                        page_cap=limits["max_reply_pages_per_thread"], item_cap=cap):
                    reply_snippet = reply.get("snippet", {})
                    if reply_snippet.get("parentId") != parent_id:
                        issue("reply", "reply_parent_mismatch", reply.get("id"))
                        continue
                    if observe(kind="reply", source_id=reply.get("id"), video_id=video_id, channel_id=channel_id,
                            snippet=reply_snippet, received_at=received_at, parent_id=parent_id,
                            text=reply_snippet.get("textDisplay"),
                            context={**context, "parent_text": parent_text, "parent_source_id": parent_id},
                            engagement={"likeCount": _count(reply_snippet.get("likeCount"))}):
                        comments_collected += 1
    except CollectionError as exc:
        fatal = exc.reason
        issue("collection", exc.reason, http_status=exc.http_status)
    except Exception:
        # Do not log arbitrary exception strings, which may contain credentials.
        fatal = "collector_error"
        issue("collection", fatal)

    status = "partial" if observations and issues else "failed" if issues else "completed" if observations else "empty"
    manifest = {
        "schema_version": "youtube-collection-manifest-v1", "run_id": run_id, "status": status,
        "started_at": began_at, "finished_at": _stamp(clock_fn()), "config": config,
        "provenance": provenance, "test_only": test_only,
        "observations": len(observations), "videos": sum(item["kind"] == "video" for item in observations),
        "comments": sum(item["kind"] != "video" for item in observations),
        "issues": issues, "excluded": excluded, "coverage": coverage,
        "http_attempts": client.calls if client else {"search": 0, "other": 0},
        "request_events": client.events if client else [],
        "fatal_reason": fatal, "source_text_stored_in_research_log": False,
        "history_reconstruction": False, "verified_memes": None, "model_calls": 0,
        "human_review_minutes": 0, "comment_author_identifiers_collected": False,
        "independent_commenter_counts_available": False,
        "retention_policy": "Refresh or delete API-derived observations, contexts and raw files by their retention_due_at; no automatic retention enforcement in this collector.",
        "claim_scope": "Only the configured bounded observation sample; no platform-wide recall or meme accuracy claim.",
        "quota_accounting": "Request attempts by bucket; not measured project quota usage or a monetary charge.",
        "quota_reference": "https://developers.google.com/youtube/v3/determine_quota_cost",
        "credential_header_reference": "https://docs.cloud.google.com/apis/docs/system-parameters",
        "artifacts": [],
    }
    try:
        destination.mkdir(parents=True, exist_ok=True)
        observation_path = destination / "observations.jsonl"
        with observation_path.open("x", encoding="utf-8", newline="\n") as handle:
            for observation in observations:
                handle.write(_json(observation) + "\n")
        for path in [observation_path, *raw_paths]:
            manifest["artifacts"].append({"path": str(path.resolve()), "sha256": hash_file(path), "bytes": path.stat().st_size})
        manifest_path = destination / "manifest.json"
        _write_json(manifest_path, manifest)
        log.finish(run_id, status="failed" if issues else "succeeded",
            notes=f"YouTube bounded collection {status}; videos={manifest['videos']}, comments={manifest['comments']}, issues={len(issues)}, test_only={test_only}. No meme accuracy evaluation.",
            artifacts=[manifest_path, observation_path, *raw_paths], actual_cost={"amount": None, "api_units": None, "human_minutes": 0})
    except Exception:
        # A failed artifact write is not a successful collection, even if HTTP
        # worked. Finish the registry if possible, without unsafe exception text.
        if log.status(run_id)["status"] == "started":
            log.finish(run_id, status="failed", notes="Collection artifact write or finalization failed; inspect output directory. No success claim.")
        raise CollectionError("artifact_finalization_failed") from None
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "collect"):
        command = sub.add_parser(name)
        command.add_argument("--config", required=True)
        command.add_argument("--db", default="logs/research.sqlite3")
        if name == "collect":
            command.add_argument("--output-dir", default="data/youtube_api")
            command.add_argument("--retry-of")
            command.add_argument("--retry-reason")
    args = parser.parse_args(argv)
    try:
        config = read_json(args.config)
        if args.command == "inspect":
            result = inspect(config, logdb=args.db)
        else:
            result = collect(config, output_dir=args.output_dir, logdb=args.db,
                             retry_of=args.retry_of, retry_reason=args.retry_reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if args.command == "inspect" or result["status"] in {"completed", "empty"} else 2
    except DuplicateRunError as exc:
        print(_json({"error": "duplicate_run", "existing_run_id": exc.existing["run_id"],
                     "instruction": "Inspect prior run; intentional retry requires --retry-of and --retry-reason."}))
        return 3
    except Exception as exc:
        # Config parser/path/transport text is untrusted and must not be echoed.
        print(_json({"error": exc.reason if isinstance(exc, CollectionError) else "invalid_config_or_local_error"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
