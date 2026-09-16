"""Logged, bounded keyless evidence expansion from public YouTube search HTML.

Only one initial /results page per declared query is requested. No cookies,
authentication, internal API calls, continuation requests, retries or challenge
bypass are used. HTML is an unstable public interface: changes/consent/blocking
produce a failure, never fabricated results. Search expansion is kept separate
from unseeded discovery and has no exact historical publication timestamps.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_log import DuplicateRunError, ResearchLog, hash_file, normalize_spec, read_json

ROOT = Path(__file__).resolve().parents[1]
VIDEO = re.compile(r"[A-Za-z0-9_-]{11}\Z")
CHANNEL = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")
MODES = {"discovered_phrase_expansion", "topic_query", "known_name_lookup"}
REASONS = {"invalid_public_url", "redirect_blocked", "http_error", "rate_limited", "transport_error",
           "response_too_large", "unexpected_content_type", "invalid_encoding", "consent_required",
           "captcha_or_traffic_block", "login_required", "initial_data_missing_or_invalid",
           "unsupported_search_structure", "no_video_results", "malformed_renderer", "collector_error",
           "evidence_validation_failed", "artifact_finalization_failed", "local_query_cap"}
AD_RENDERERS = {"adSlotRenderer", "inFeedAdLayoutRenderer", "promotedVideoRenderer", "searchPyvRenderer",
                "promotedSparklesWebRenderer", "promotedSparklesTextSearchRenderer"}


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


def stamp(value):
    return utc(value).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class SearchError(Exception):
    def __init__(self, reason, status=None):
        self.reason = reason if reason in REASONS else "collector_error"
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        super().__init__(self.reason)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def fetch_public_page(url, timeout, max_bytes):
    """Return public HTML bytes, with no redirects, cookies, or automatic retry."""
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "www.youtube.com" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.path != "/results"):
        raise SearchError("invalid_public_url")
    request = urllib.request.Request(url, headers={"Accept": "text/html", "Accept-Language": "ko-KR,ko;q=0.9",
                  "User-Agent": "KoreanMemeResearch-LocalCollector/0.1"}, method="GET")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            if "text/html" not in response.headers.get("Content-Type", "").lower():
                raise SearchError("unexpected_content_type")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise SearchError("response_too_large")
            return body
    except urllib.error.HTTPError as exc:
        reason = "rate_limited" if exc.code == 429 else "redirect_blocked" if 300 <= exc.code < 400 else "http_error"
        raise SearchError(reason, exc.code) from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise SearchError("transport_error") from None


def _text(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    if isinstance(value.get("simpleText"), str):
        return value["simpleText"]
    if isinstance(value.get("content"), str):
        return value["content"]
    runs = value.get("runs")
    if isinstance(runs, list):
        return "".join(run.get("text", "") for run in runs if isinstance(run, dict) and isinstance(run.get("text", ""), str))
    return ""


def _descend(value, path):
    for component in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(component)
    return value


def _walk(value):
    stack = [value]
    visited = 0
    while stack:
        node = stack.pop()
        visited += 1
        if visited > 200_000:
            raise SearchError("unsupported_search_structure")
        if isinstance(node, dict):
            yield node
            stack.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            stack.extend(reversed(node))


def _channel_id(renderer):
    for node in _walk(renderer):
        browse = node.get("browseEndpoint")
        value = browse.get("browseId") if isinstance(browse, dict) else None
        if isinstance(value, str) and CHANNEL.fullmatch(value):
            return value
    return None


def _extract_initial_data(html):
    lowered = html.lower()
    # Match real challenge/page markers, not merely YouTube's generic login
    # links or consent feature flags present on otherwise public result pages.
    if (re.search(r'<form\b[^>]*action=["\'][^"\']*consent\.(?:youtube|google)\.com', html, re.I)
            or "<title>before you continue to youtube" in lowered):
        raise SearchError("consent_required")
    if ("our systems have detected unusual traffic" in lowered or
            re.search(r'<(?:div|form)\b[^>]*(?:g-recaptcha|recaptcha-challenge)', html, re.I)
            or '<title>sorry' in lowered):
        raise SearchError("captcha_or_traffic_block")
    if ("<title>sign in - google accounts" in lowered or
            '<form' in lowered and 'id="gaia_loginform"' in lowered):
        raise SearchError("login_required")
    patterns = [r'\b(?:var\s+)?ytInitialData\s*=\s*', r'window\s*\[\s*["\']ytInitialData["\']\s*\]\s*=\s*']
    decoder = json.JSONDecoder()
    for pattern in patterns:
        for match in list(re.finditer(pattern, html))[:20]:
            try:
                value, end = decoder.raw_decode(html[match.end():].lstrip())
            except (ValueError, RecursionError):
                continue
            if isinstance(value, dict):
                return value
    raise SearchError("initial_data_missing_or_invalid")


def _primary_contents(data):
    for path in ("contents.twoColumnSearchResultsRenderer.primaryContents",
                 "contents.singleColumnSearchResultsRenderer.primaryContents",
                 "contents.sectionListRenderer"):
        primary = _descend(data, path)
        if isinstance(primary, (dict, list)):
            return primary
    raise SearchError("unsupported_search_structure")


def _renderers(primary):
    stack = [primary]
    visited = 0
    while stack:
        node = stack.pop()
        visited += 1
        if visited > 200_000:
            raise SearchError("unsupported_search_structure")
        if isinstance(node, dict):
            if node.keys() & AD_RENDERERS:
                continue
            found = False
            for kind in ("videoRenderer", "reelItemRenderer", "shortsLockupViewModel"):
                if kind in node:
                    yield kind, node[kind]
                    found = True
                    break
            if not found:
                stack.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            stack.extend(reversed(node))


def parse_search_page(body, *, query, source_search_url, received_at, max_results=20,
                      provenance="actual_collected", retention_days=30):
    """Extract initial public result cards; relative dates remain labels, not dates."""
    if isinstance(body, bytes):
        try:
            html = body.decode("utf-8-sig")
        except UnicodeError:
            raise SearchError("invalid_encoding") from None
    elif isinstance(body, str):
        html = body
    else:
        raise SearchError("invalid_encoding")
    initial = _extract_initial_data(html)
    primary = _primary_contents(initial)
    rows, exclusions, seen = [], [], set()
    renderer_count = 0
    for rank, (kind, renderer) in enumerate(_renderers(primary), 1):
        renderer_count += 1
        if not isinstance(renderer, dict):
            exclusions.append({"rank": rank, "reason": "malformed_renderer"})
            continue
        video_id = renderer.get("videoId")
        if kind == "shortsLockupViewModel":
            video_id = (_descend(renderer, "onTap.innertubeCommand.reelWatchEndpoint.videoId") or
                        _descend(renderer, "onTap.innertubeCommand.watchEndpoint.videoId"))
            # Endpoint metadata only supplies a public URL; no endpoint is called.
            if not video_id:
                endpoint_url = _descend(renderer, "onTap.innertubeCommand.commandMetadata.webCommandMetadata.url")
                match = re.fullmatch(r"/shorts/([A-Za-z0-9_-]{11})(?:\?.*)?", endpoint_url or "")
                video_id = match.group(1) if match else None
            title = _text(_descend(renderer, "overlayMetadata.primaryText"))
        else:
            title = _text(renderer.get("title")) or _text(renderer.get("headline"))
        if not isinstance(video_id, str) or not VIDEO.fullmatch(video_id) or not title:
            exclusions.append({"rank": rank, "reason": "malformed_renderer"})
            continue
        if video_id in seen:
            exclusions.append({"rank": rank, "reason": "duplicate_video_card", "video_id": video_id})
            continue
        seen.add(video_id)
        description = _text(renderer.get("descriptionSnippet"))
        snippets = renderer.get("detailedMetadataSnippets", [])
        if not description and isinstance(snippets, list):
            description = "\n".join(_text(value.get("snippetText")) for value in snippets if isinstance(value, dict)).strip()
        publication_label = _text(renderer.get("publishedTimeText")) or None
        channel_id = _channel_id(renderer)
        channel_label = next((text for name in ("ownerText", "longBylineText", "shortBylineText")
                              if (text := _text(renderer.get(name)))), None)
        source_url = "https://www.youtube.com/watch?v=" + video_id
        text = title + ("\n" + description if description else "")
        version = {"video_id": video_id, "channel_id": channel_id, "title": title,
                   "description_snippet": description, "publication_label": publication_label}
        rows.append({"schema_version": "youtube-search-observation-v1", "platform": "youtube",
            "observation_id": "youtube:search:" + video_id + ":" + digest(version)[:24],
            "source_id": video_id, "video_id": video_id, "parent_id": None, "channel_id": channel_id,
            "title": title, "description_snippet": description, "text": text,
            "context": {"title": title, "description_snippet": description, "channel_label": channel_label,
                        "renderer_kind": kind, "publication_label": publication_label},
            "modality": "search_result_metadata", "publication_label": publication_label,
            "posted_at": None, "updated_at": None, "collected_at": stamp(received_at), "available_at": stamp(received_at),
            "temporal_eligibility": False,
            "temporal_exclusion_reason": "Search HTML has no verified exact publication timestamp; resolve through a feed or an authorized API before temporal analysis.",
            "source_url": source_url, "source_search_url": source_search_url,
            "query": query, "search_rank": rank, "provenance": provenance,
            "original_or_repost": "unknown", "independence_status": "unknown", "meme_label": "unassessed",
            "human_review_status": "not_reviewed", "engagement": {},
            "evidence_links": [source_url, source_search_url],
            "retention_due_at": stamp(utc(received_at) + timedelta(days=retention_days))})
        if len(rows) >= max_results:
            break
    if not rows:
        raise SearchError("no_video_results")
    return rows, {"renderer_cards_examined": renderer_count, "returned_observations": len(rows),
                  "exclusions": exclusions, "coverage": "initial_page_bounded",
                  "pagination_attempted": False, "ranking_personalization": "unknown_public_unauthenticated_response"}


def validate_config(config):
    allowed = {"version", "search_mode", "queries", "selection_reason", "known_meme_seed_names",
               "discovery_run_id", "discovery_observations", "discovery_manifest", "discovery_method",
               "max_results_per_query", "max_results_total", "timeout_seconds", "max_response_bytes", "retention_days"}
    if not isinstance(config, dict) or config.keys() - allowed:
        raise ValueError("Unknown config field")
    out = {"version": "youtube-public-search-v1", "search_mode": config.get("search_mode"),
           "queries": config.get("queries"), "selection_reason": config.get("selection_reason"),
           "known_meme_seed_names": config.get("known_meme_seed_names", []),
           "discovery_run_id": config.get("discovery_run_id"), "discovery_observations": config.get("discovery_observations"),
           "discovery_manifest": config.get("discovery_manifest"), "discovery_method": config.get("discovery_method"),
           "max_results_per_query": config.get("max_results_per_query", 10), "max_results_total": config.get("max_results_total", 20),
           "timeout_seconds": config.get("timeout_seconds", 15), "max_response_bytes": config.get("max_response_bytes", 8_000_000),
           "retention_days": config.get("retention_days", 30)}
    if out["search_mode"] not in MODES:
        raise ValueError("Search mode must distinguish expansion, topic query, and known-name lookup")
    if not isinstance(out["selection_reason"], str) or not out["selection_reason"].strip():
        raise ValueError("Selection reason required")
    if not isinstance(out["queries"], list) or not 1 <= len(out["queries"]) <= 5:
        raise ValueError("Provide one to five queries")
    query_values = set()
    for entry in out["queries"]:
        if not isinstance(entry, dict) or entry.keys() - {"query", "evidence_ids"}:
            raise ValueError("Each query requires query and optional evidence_ids")
        query = entry.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 150 or query != query.strip():
            raise ValueError("Query must have 1 to 150 characters")
        if query in query_values:
            raise ValueError("Duplicate queries are not allowed")
        query_values.add(query)
        evidence_ids = entry.get("evidence_ids", [])
        if not isinstance(evidence_ids, list) or any(not isinstance(value, str) or not value for value in evidence_ids):
            raise ValueError("Invalid evidence_ids")
        if out["search_mode"] == "discovered_phrase_expansion" and not evidence_ids:
            raise ValueError("Expansion requires originating observation IDs")
        if out["search_mode"] != "discovered_phrase_expansion" and evidence_ids:
            raise ValueError("Only discovered phrase expansion uses discovery evidence IDs")
    names = out["known_meme_seed_names"]
    if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError("Invalid known names")
    if out["search_mode"] == "known_name_lookup" and not names:
        raise ValueError("Known-name lookup requires the names")
    if out["search_mode"] != "known_name_lookup" and names:
        raise ValueError("Known names must not be labeled expansion or topic query")
    discovery_fields = ("discovery_run_id", "discovery_observations", "discovery_manifest", "discovery_method")
    if out["search_mode"] == "discovered_phrase_expansion":
        if any(not isinstance(out[field], str) or not out[field].strip() for field in discovery_fields):
            raise ValueError("Expansion requires run, manifest, observation file, and discovery method")
    elif any(out[field] is not None for field in discovery_fields):
        raise ValueError("Discovery provenance is exclusive to expansion mode")
    for field, low, high in (("max_results_per_query", 1, 20), ("max_results_total", 1, 20),
                             ("timeout_seconds", 1, 60), ("max_response_bytes", 1024, 12_000_000), ("retention_days", 1, 30)):
        if type(out[field]) is not int or not low <= out[field] <= high:
            raise ValueError("Invalid bounded setting: " + field)
    return out


def _path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _snapshots(config):
    return [{"id": field, "sha256": hash_file(_path(config[field]))}
            for field in ("discovery_manifest", "discovery_observations") if config[field]]


def _validate_evidence(config, *, test_only, collected_at):
    if config["search_mode"] != "discovered_phrase_expansion":
        return
    manifest = read_json(_path(config["discovery_manifest"]))
    if manifest.get("run_id") != config["discovery_run_id"]:
        raise SearchError("evidence_validation_failed")
    rows = {}
    with _path(config["discovery_observations"]).open(encoding="utf-8-sig") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("observation_id") in rows:
                raise SearchError("evidence_validation_failed")
            rows[row.get("observation_id")] = row
    normalize = lambda value: " ".join(unicodedata.normalize("NFC", value).casefold().split())
    for entry in config["queries"]:
        for evidence_id in entry["evidence_ids"]:
            row = rows.get(evidence_id)
            if (not row or row.get("provenance") != ("ai_synthetic" if test_only else "actual_collected")
                    or not isinstance(row.get("text"), str)
                    or normalize(entry["query"]) not in normalize(row["text"])):
                raise SearchError("evidence_validation_failed")
            try:
                if utc(row["available_at"]) > utc(collected_at):
                    raise SearchError("evidence_validation_failed")
            except (KeyError, TypeError, ValueError):
                raise SearchError("evidence_validation_failed") from None


def _save_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def collect(config, *, output_dir="data/youtube_search", logdb="logs/research.sqlite3", transport=None,
            retry_of=None, retry_reason=None, now=None, test_only=False):
    if (transport is not None or now is not None) and not test_only:
        raise ValueError("Injected transport/clock requires test_only=True")
    config = validate_config(config)
    snapshots = _snapshots(config)
    spec = normalize_spec({"objective": "Bounded public YouTube search evidence expansion, separate from unseeded discovery",
        "stage": "offline_validation" if test_only else "access_probe", "parameters": {"config": config, "test_only": test_only},
        "data_snapshots": snapshots, "code_hashes": {name: hash_file(ROOT / "src" / name) for name in ("youtube_web_search.py", "research_log.py")},
        "config_hashes": {"config": digest(config)}, "prompt_hashes": {}, "modality": ["search_result_metadata"],
        "provenance": "ai_synthetic" if test_only else "actual_collected"})
    log = ResearchLog(logdb)
    run = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    clock_fn = now or (lambda: datetime.now(timezone.utc))
    transport_fn = transport or fetch_public_page
    provenance = "ai_synthetic" if test_only else "actual_collected"
    began = stamp(clock_fn())
    destination = Path(output_dir) / run["run_id"]
    observations, requests, issues, raw_files = [], [], [], []
    seen = {}
    try:
        destination.mkdir(parents=True, exist_ok=False)
        (destination / "raw").mkdir()
        _validate_evidence(config, test_only=test_only, collected_at=began)
        # Input data is frozen for this run; changing a local evidence file while
        # preparing the request cannot silently alter its recorded fingerprint.
        if _snapshots(config) != snapshots:
            raise SearchError("evidence_validation_failed")
        stop = False
        for index, entry in enumerate(config["queries"], 1):
            if stop or len(observations) >= config["max_results_total"]:
                requests.append({"query": entry["query"], "status": "not_requested", "reason": "prior_block_or_result_cap"})
                continue
            url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": entry["query"], "hl": "ko", "gl": "KR"})
            request = {"query": entry["query"], "source_search_url": url, "started_at": stamp(clock_fn()), "status": "started"}
            requests.append(request)
            try:
                body = transport_fn(url, config["timeout_seconds"], config["max_response_bytes"])
                received = stamp(clock_fn())
                if isinstance(body, str):
                    body = body.encode("utf-8")
                if not isinstance(body, bytes):
                    raise SearchError("invalid_encoding")
                if len(body) > config["max_response_bytes"]:
                    raise SearchError("response_too_large")
                raw_path = destination / "raw" / f"query_{index:02d}.html"
                with raw_path.open("xb") as handle:
                    handle.write(body)
                raw_files.append(raw_path)
                request.update(received_at=received, raw_file=str(raw_path.resolve()), raw_sha256=hash_file(raw_path),
                               raw_bytes=len(body), retention_due_at=stamp(utc(received) + timedelta(days=config["retention_days"])))
                rows, page = parse_search_page(body, query=entry["query"], source_search_url=url,
                    received_at=received, max_results=min(config["max_results_per_query"], config["max_results_total"] - len(observations)),
                    provenance=provenance, retention_days=config["retention_days"])
                for row in rows:
                    reference = {"query": entry["query"], "source_search_url": url, "search_rank": row["search_rank"],
                                 "observed_at": received}
                    if row["observation_id"] in seen:
                        seen[row["observation_id"]]["search_references"].append(reference)
                        continue
                    row.update(test_only=test_only, search_references=[reference], discovery_evidence_ids=entry.get("evidence_ids", []),
                        sample_method={"mode": "public_search_page", "search_mode": config["search_mode"],
                            "query": entry["query"], "discovery_run_id": config["discovery_run_id"],
                            "discovery_method": config["discovery_method"], "known_meme_seed_names": config["known_meme_seed_names"],
                            "unseeded_discovery": False, "query_selected_after_observation": config["search_mode"] == "discovered_phrase_expansion"},
                        discovery_evaluation_eligible=False)
                    observations.append(row)
                    seen[row["observation_id"]] = row
                request.update(status="succeeded", parse=page)
                if page["exclusions"]:
                    request["parse_exclusions"] = page["exclusions"]
                    if any(item["reason"] == "malformed_renderer" for item in page["exclusions"]):
                        issues.append({"query": entry["query"], "reason": "malformed_renderer"})
            except SearchError as exc:
                request.update(status="failed", reason=exc.reason, http_status=exc.status)
                issues.append({"query": entry["query"], "reason": exc.reason, "http_status": exc.status})
                if exc.reason in {"rate_limited", "captcha_or_traffic_block", "consent_required", "login_required", "redirect_blocked"}:
                    stop = True
            except Exception:
                request.update(status="failed", reason="transport_error")
                issues.append({"query": entry["query"], "reason": "transport_error"})
    except SearchError as exc:
        issues.append({"query": None, "reason": exc.reason})
    except Exception:
        issues.append({"query": None, "reason": "collector_error"})
    status = "partial" if observations and issues else "failed" if issues else "completed"
    manifest = {"schema_version": "youtube-public-search-manifest-v1", "run_id": run["run_id"], "status": status,
        "started_at": began, "finished_at": stamp(clock_fn()), "config": config, "provenance": provenance,
        "test_only": test_only, "http_attempts": sum(request["status"] != "not_requested" for request in requests),
        "observations": len(observations), "requests": requests, "issues": issues, "paid_api_calls": 0,
        "model_calls": 0, "human_review_minutes": 0, "verified_meme_count": None,
        "unseeded_discovery": False, "discovery_evaluation_eligible": False, "temporal_eligible_observations": 0,
        "history_reconstruction": False, "input_snapshots": snapshots,
        "limitations": ["Search query expansion is not independent discovery or population coverage.",
            "Result cards lack verified exact publication timestamps; feed/API resolution is needed before temporal analysis.",
            "Only public initial HTML is inspected; no continuation, video playback, captions, comments or authentication.",
            "Repeated names, topics, series labels and phrases are not proof of independent meme use.",
            "The local retention review deadline does not grant a data reuse licence."], "artifacts": []}
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
            notes=f"Public search {status}; requests={manifest['http_attempts']}, observations={len(observations)}, issues={len(issues)}, test_only={test_only}. No meme accuracy or temporal/discovery evaluation.",
            artifacts=[manifest_path, observation_path, *raw_files], actual_cost={"amount": 0, "currency": "USD", "api_units": 0, "human_minutes": 0})
    except Exception:
        if log.status(run["run_id"])["status"] == "started":
            log.finish(run["run_id"], status="failed", notes="Public search artifact finalization failed. No success claim.")
        raise SearchError("artifact_finalization_failed") from None
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", default="data/youtube_search")
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
        print(json.dumps({"error": exc.reason if isinstance(exc, SearchError) else "invalid_config_or_local_error"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
