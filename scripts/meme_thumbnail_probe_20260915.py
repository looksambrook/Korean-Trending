"""Bounded public thumbnail observations, selected from saved watch metadata.

This reads three still images, not video frames, audio or captions.
"""
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from research_log import ResearchLog, hash_file
from youtube_video_metadata import _json_assignment

SOURCES = [
    ("viEJSCPHnFg", "youtube_meme_topic_metadata_20260915_v01/e335655ae43d4f458edc5c1d8d845ac3"),
    ("dY_aF0dHn8U", "youtube_meme_smurf_metadata_20260915_v01/c2f755e0220f4e3782c9c05a38381440"),
    ("tjJJhCrUuCE", "youtube_meme_smurf_metadata_20260915_v01/c2f755e0220f4e3782c9c05a38381440"),
]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirect not allowed in this bounded probe")


def main():
    selected = []
    for video_id, parent in SOURCES:
        path = ROOT / "data/collected" / parent / "raw" / (video_id + ".html")
        player = _json_assignment(path.read_text(encoding="utf-8"), "ytInitialPlayerResponse")
        details = player["videoDetails"]
        assert details["videoId"] == video_id
        url = details["thumbnail"]["thumbnails"][-1]["url"]
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.netloc == "i.ytimg.com"
        selected.append({"video_id": video_id, "url": url, "source_path": str(path),
                         "source_sha256": hash_file(path)})
    registry = ResearchLog(ROOT / "logs/research.sqlite3")
    run = registry.begin({
        "objective": "Observe actual public thumbnails for a character-meme candidate and historical uses",
        "stage": "access_probe", "parameters": {"selected": selected, "max_http_gets": 3,
            "max_bytes_each": 2000000, "timeout_seconds": 15, "video_audio_access": False},
        "data_snapshots": [{"id": x["source_path"], "sha256": x["source_sha256"]} for x in selected],
        "code_hashes": {"probe": hash_file(__file__)}, "prompt_hashes": {}, "config_hashes": {},
        "modality": ["thumbnail_image"], "provenance": "actual_collected",
    })
    out = ROOT / "data/collected/youtube_meme_thumbnails_20260915_v01" / run["run_id"]
    out.mkdir(parents=True, exist_ok=False)
    results = []
    artifacts = []
    opener = urllib.request.build_opener(NoRedirect)
    for item in selected:
        row = dict(item)
        try:
            request = urllib.request.Request(item["url"], headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(request, timeout=15) as response:
                content_type = response.headers.get_content_type()
                if content_type != "image/jpeg":
                    raise ValueError("Expected JPEG thumbnail")
                body = response.read(2000001)
            if len(body) > 2000000 or not body.startswith(b"\xff\xd8"):
                raise ValueError("Invalid or oversized JPEG")
            path = out / (item["video_id"] + ".jpg")
            with path.open("xb") as handle:
                handle.write(body)
            row.update(status="succeeded", path=str(path), sha256=hash_file(path), bytes=len(body))
            artifacts.append(path)
        except Exception as exc:
            row.update(status="failed", error_type=type(exc).__name__)
        row["received_at"] = datetime.now(timezone.utc).isoformat()
        results.append(row)
    manifest = out / "manifest.json"
    manifest.write_text(json.dumps({"run_id": run["run_id"], "observations": results,
        "paid_api_calls": 0, "human_review_minutes": 0,
        "scope": "Public still thumbnails only; visual interpretation is a separate AI assessment."},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifacts.append(manifest)
    registry.finish(run["run_id"], status="succeeded" if all(x["status"] == "succeeded" for x in results) else "failed",
        notes="Attempted exactly three public thumbnail GETs; actual per-image outcomes and hashes are in the manifest.",
        artifacts=artifacts, actual_cost={"amount": 0, "currency": "USD", "human_minutes": 0})
    print(manifest)
    print(json.dumps([{k: v for k, v in x.items() if k in ("video_id", "status", "path", "error_type")} for x in results]))


if __name__ == "__main__":
    main()
