"""Create browser-oriented delivery copies from existing creation artifacts.

Original text, images and MP4 files are preserved. H.264 conversion and optional
shared soundtrack muxing are fixed media processing, not model generation or
native audio understanding. No process runs until build_delivery is called.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
from html import escape
import json
from pathlib import Path
import re
import subprocess
import time
import wave

from research_log import ResearchLog, hash_file, read_json
from creation_pilot_report import build_report

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FFMPEG = ROOT / ".local_dependencies/media_ffmpeg/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe"
SETTINGS = {"codec": "libx264", "pixel_format": "yuv420p", "crf": 18, "preset": "veryfast",
            "audio_codec": "aac", "audio_bitrate": "128k", "duration_limit_seconds": 3,
            "threads": 1, "process_timeout_seconds": 60, "max_source_video_bytes": 35 * 1024 * 1024,
            "max_source_audio_bytes": 8 * 1024 * 1024, "max_output_bytes": 35 * 1024 * 1024,
            "max_process_log_bytes": 2 * 1024 * 1024, "max_video_pixels": 1920 * 1080,
            "max_decoded_frames": 180}


class DeliveryError(ValueError):
    def __init__(self, category, message):
        self.category = category
        super().__init__(message)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _path(value, base=ROOT, *, required=True):
    if not isinstance(value, (str, Path)) or not str(value):
        if not required:
            return None
        raise DeliveryError("source_path", "A local path is required")
    path = Path(value)
    path = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(ROOT):
        raise DeliveryError("source_path", "Input and output files must be inside the project")
    if required and not path.is_file():
        raise DeliveryError("source_missing", "Missing local file: " + path.name)
    return path


def _read(value):
    if isinstance(value, (str, Path)):
        path = _path(value)
        return read_json(path), path.parent, hash_file(path)
    if not isinstance(value, dict):
        raise DeliveryError("input_schema", "Inputs must be dictionaries or local JSON paths")
    data = copy.deepcopy(value)
    digest = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return data, ROOT, digest


def _audio(value):
    declared = dict(value) if isinstance(value, dict) else {"path": value}
    path = _path(declared.get("path") or declared.get("local_path"))
    if not 0 < path.stat().st_size <= SETTINGS["max_source_audio_bytes"]:
        raise DeliveryError("audio_size", "Mapped source audio exceeds its byte limit")
    digest = hash_file(path)
    metadata = {}
    manifest = _path(declared["manifest"]) if declared.get("manifest") else path.parent / "manifest.json"
    if manifest.is_file():
        data = read_json(manifest)
        for row in data.get("outputs", []) + data.get("records", []):
            candidate = row.get("path") or row.get("local_path")
            if candidate and (manifest.parent / candidate).resolve() == path and row.get("sha256") == digest:
                metadata = dict(row)
                metadata["provenance_manifest"] = str(manifest)
                metadata["provenance_manifest_sha256"] = hash_file(manifest)
                break
    metadata.update(declared)
    metadata.update(path=str(path), sha256=digest)
    if not all(isinstance(metadata.get(name), str) and metadata[name].strip()
               for name in ("source_url", "author", "license", "license_url")):
        raise DeliveryError("audio_attribution", "Mapped audio needs source URL, author, license and license URL, directly or from its matching manifest")
    if metadata.get("provenance") != "actual_collected":
        raise DeliveryError("audio_provenance", "Mapped soundtrack must explicitly preserve actual_collected provenance")
    return metadata


def _run(binary, args, folder, label, operations, *, destination=None, byte_limit=None, expected=(0,)):
    stdout, stderr = folder / (label + ".stdout.log"), folder / (label + ".stderr.log")
    row = {"operation": label, "started_at": _now(), "command": [str(binary), *args],
           "status": "started", "stdout_path": str(stdout), "stderr_path": str(stderr)}
    operations.append(row)
    tick = time.monotonic()
    try:
        with stdout.open("xb") as out, stderr.open("xb") as err:
            with subprocess.Popen([str(binary), *args], stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                  cwd=ROOT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
                while child.poll() is None:
                    problem = None
                    if time.monotonic() - tick > SETTINGS["process_timeout_seconds"]:
                        problem = "process_timeout"
                    elif stdout.stat().st_size + stderr.stat().st_size > SETTINGS["max_process_log_bytes"]:
                        problem = "process_log_limit"
                    elif destination and destination.exists() and destination.stat().st_size > byte_limit:
                        problem = "output_size_limit"
                    if problem:
                        child.kill()
                        child.wait(timeout=5)
                        raise DeliveryError(problem, label + " exceeded its configured bound")
                    time.sleep(0.05)
                row["returncode"] = child.returncode
                if child.returncode not in expected:
                    raise DeliveryError("ffmpeg_process", label + " returned " + str(child.returncode) + "; inspect stderr")
        if stdout.stat().st_size + stderr.stat().st_size > SETTINGS["max_process_log_bytes"]:
            raise DeliveryError("process_log_limit", label + " exceeded its log limit")
        if destination and (not destination.is_file() or not 0 < destination.stat().st_size <= byte_limit):
            raise DeliveryError("output_size_limit", label + " produced no bounded output")
        row["status"] = "succeeded"
        return stdout.read_text(encoding="utf-8", errors="replace"), stderr.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        row.update(status="failed", error_category=getattr(exc, "category", type(exc).__name__), message=str(exc))
        raise
    finally:
        row.update(finished_at=_now(), wall_seconds=round(time.monotonic() - tick, 6))
        row["log_sha256"] = {p.name: hash_file(p) for p in (stdout, stderr) if p.is_file()}


def _base():
    return ["-hide_banner", "-nostdin", "-n", "-threads", "1", "-filter_threads", "1",
            "-filter_complex_threads", "1", "-max_alloc", "67108864"]


def _input(path):
    return ["-protocol_whitelist", "file", "-threads", "1", "-i", str(path)]


def _probe(binary, path, folder, label, operations):
    _, text = _run(binary, _base() + _input(path), folder, label, operations, expected=(1,))
    video = next((line.strip() for line in text.splitlines() if "Stream #0:" in line and "Video:" in line), None)
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not video or not duration or "At least one output file must be specified" not in text:
        raise DeliveryError("stream_probe", "Video metadata was not recognized")
    seconds = int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])
    dimensions = re.search(r"\b(\d{2,5})x(\d{2,5})\b", video)
    if not dimensions:
        raise DeliveryError("stream_dimensions", "Video dimensions were not recognized")
    width, height = int(dimensions[1]), int(dimensions[2])
    if not 0 < seconds <= 3.05 or width * height > SETTINGS["max_video_pixels"] or width % 2 or height % 2:
        raise DeliveryError("video_bounds", "Source/delivery video must be at most 3 seconds with bounded even dimensions")
    return {"video_stream": video, "duration_seconds_reported": seconds, "width": width, "height": height,
            "has_audio": any("Stream #0:" in line and "Audio:" in line for line in text.splitlines()),
            "audio_streams": [line.strip() for line in text.splitlines() if "Stream #0:" in line and "Audio:" in line]}


def _validate_video(binary, path, folder, operations, metadata):
    hashes = folder / "decoded_frames.framemd5"
    _run(binary, _base() + ["-xerror"] + _input(path) + ["-map", "0:v:0", "-an", "-c:v", "rawvideo",
         "-pix_fmt", "rgb24", "-threads", "1", "-f", "framemd5", str(hashes)],
         folder, "decode_video", operations, destination=hashes, byte_limit=512 * 1024)
    content = hashes.read_text(encoding="utf-8")
    time_base_match = re.search(r"#tb 0: (\d+/\d+)", content)
    rows = [line.split(",") for line in content.splitlines() if line.strip() and not line.startswith("#")]
    if not time_base_match or not 0 < len(rows) <= SETTINGS["max_decoded_frames"]:
        raise DeliveryError("video_decode", "Decoded frame count or time base is invalid")
    tb = Fraction(time_base_match[1])
    decoded_end = max(float((int(row[2]) + int(row[3])) * tb) for row in rows)
    if decoded_end > 3.05 or any(int(row[4]) != metadata["width"] * metadata["height"] * 3 for row in rows):
        raise DeliveryError("video_decode_bounds", "Decoded RGB frame timing or dimensions violate the delivery limits")
    result = {"actual_decode_verified": True, "validation": "FFmpeg decoded every delivered video frame into RGB frame hashes",
              "decoded_frame_count": len(rows), "decoded_end_seconds": decoded_end,
              "frame_hash_file": str(hashes), "frame_hash_sha256": hash_file(hashes),
              "playback_codec_verified": True, "browser_playback_manually_verified": False}
    if metadata["has_audio"]:
        wav = folder / "decoded_audio_validation.wav"
        _run(binary, _base() + ["-xerror"] + _input(path) + ["-map", "0:a:0", "-vn", "-t", "3",
             "-af", "aresample=16000,atrim=end_sample=48000", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", "-threads", "1", str(wav)], folder, "decode_audio", operations,
             destination=wav, byte_limit=128 * 1024)
        with wave.open(str(wav), "rb") as handle:
            frames, channels, rate, width = handle.getnframes(), handle.getnchannels(), handle.getframerate(), handle.getsampwidth()
            pcm = handle.readframes(frames)
        if (channels, rate, width) != (1, 16000, 2) or not 0 < frames <= 48000 or len(pcm) != frames * 2:
            raise DeliveryError("audio_decode", "Delivered AAC could not be decoded as bounded PCM")
        result["audio"] = {"actual_decode_verified": True, "frames": frames, "sample_rate": rate,
                           "decoded_seconds": frames / rate, "wav_path": str(wav), "sha256": hash_file(wav)}
    return result


def _credit_html(credits):
    def link(value, title=None):
        value = str(value or "")
        label = escape(str(title or value))
        return '<a href="' + escape(value, quote=True) + '" rel="noreferrer">' + label + '</a>' if value.startswith("https://") else label
    rows = []
    for credit in credits:
        rows.append('<li>' + escape(credit["author"]) + ' · ' + link(credit["source_url"], "음원 출처") + ' · '
                    + link(credit["license_url"], credit["license"]) + '<br>' + escape(credit["changes"]) + '</li>')
    text = "해당 음원은 동일한 요청의 세 조건 영상에 공통으로 결합했습니다. 원곡을 새로 생성하거나 음원 생성기를 학습한 결과가 아닙니다. 이 변환 작업은 모델의 음원 청취·이해를 검증하지 않습니다." if rows else "영상의 코덱을 H.264로 변환했습니다. 새로운 본문·이미지·음악을 생성하지 않았습니다."
    return '<section class="sources"><h2>영상 변환과 음원 출처</h2><p>' + text + '</p>' + ('<ul>' + ''.join(rows) + '</ul>' if rows else '') + '</section>'


def build_delivery(spec, model_summary, render_summary, outdir, audio_by_family=None, *, ffmpeg=None,
                   db=None, retry_of=None, retry_reason=None):
    """Return {render_summary, report, manifest}; paths or dictionaries accepted.

    audio_by_family maps exact family IDs to a WAV path or metadata object
    {path, author, source_url, license, license_url, provenance}. Matching local
    decode manifests can supply these metadata fields. No family is inferred.
    """
    study, study_base, study_sha = _read(spec)
    model, _, model_sha = _read(model_summary)
    rendered, render_base, render_sha = _read(render_summary)
    output = _path(outdir, required=False)
    if output is None or output.exists():
        raise DeliveryError("output_exists", "A new output directory is required")
    binary = _path(ffmpeg or DEFAULT_FFMPEG)
    mappings = audio_by_family or {}
    if isinstance(mappings, (str, Path)):
        mappings = read_json(_path(mappings))
    if not isinstance(mappings, dict) or not isinstance(rendered.get("outputs"), list):
        raise DeliveryError("input_schema", "Audio mapping must be an object and render outputs must be an array")
    audio_map = {str(family): _audio(value) for family, value in mappings.items()}
    original_hashes = {}
    snapshots = [{"id": "study_spec", "sha256": study_sha}, {"id": "model_summary", "sha256": model_sha},
                 {"id": "original_render_summary", "sha256": render_sha}]
    rows = copy.deepcopy(rendered["outputs"])
    for index, row in enumerate(rows):
        for kind in ("text", "image", "video"):
            value = row.get(kind + "_path")
            if value:
                path = _path(value, render_base, required=False)
                row[kind + "_path"] = str(path)
                if path.is_file():
                    original_hashes[str(path)] = hash_file(path)
                    snapshots.append({"id": f"output_{index}_{kind}", "sha256": original_hashes[str(path)]})
    for index, (family, info) in enumerate(audio_map.items()):
        original_hashes[info["path"]] = info["sha256"]
        snapshots.append({"id": f"audio_{index}", "sha256": info["sha256"]})
    # Source reference cards may contain their own local text/image files.
    study = copy.deepcopy(study)
    for index, source in enumerate(study.get("sources", [])):
        if source.get("path") or source.get("local_path"):
            source_path = _path(source.get("path") or source.get("local_path"), study_base, required=False)
            source["path"] = str(source_path)
            if source_path.is_file():
                original_hashes[str(source_path)] = hash_file(source_path)
                snapshots.append({"id": f"reference_source_{index}", "sha256": original_hashes[str(source_path)]})
    binary_sha = hash_file(binary)
    registration_spec = {"objective": "Create bounded H264 delivery copies and optional common licensed soundtrack, preserving all original artifacts",
        "stage": "offline_validation", "parameters": {"settings": SETTINGS, "audio_by_family": audio_map,
            "model_run_id": model.get("run_id"), "original_render_run_id": rendered.get("run_id"),
            "planned_outputs": len(rows), "model_calls": 0, "human_evaluations": 0},
        "data_snapshots": snapshots, "code_hashes": {"delivery": hash_file(__file__),
            "report": hash_file(ROOT / "src/creation_pilot_report.py"), "registry": hash_file(ROOT / "src/research_log.py")},
        "config_hashes": {"ffmpeg_executable": binary_sha}, "prompt_hashes": {},
        "modality": ["text", "image", "video", "audio"], "provenance": "mixed"}
    registry = ResearchLog(_path(db or ROOT / "logs/research.sqlite3", required=False))
    entry = registry.begin(registration_spec, retry_of=retry_of, retry_reason=retry_reason)
    operations, conversions, failures, credits = [], [], [], []
    manifest = {"schema_version": "creation-media-delivery-v1", "run_id": entry["run_id"], "started_at": _now(),
        "status": "started", "settings": SETTINGS, "ffmpeg": {"path": str(binary), "sha256": binary_sha},
        "original_input_sha256": original_hashes, "conversions": conversions, "failures": failures,
        "model_calls": 0, "human_evaluations": 0, "native_audio_model_run": False,
        "music_generator_weights_trained": False, "local_compute_cost": {"amount": None, "currency": None},
        "scope": "Codec delivery copies and optional identical source-audio excerpt across conditions; no new model or quality experiment"}
    try:
        output.mkdir(parents=True)
        _write(output / "registration.json", entry)
        version, _ = _run(binary, ["-version"], output, "ffmpeg_version", operations)
        manifest["ffmpeg"]["version"] = version.splitlines()[0]
        family_by_case = {row["case_id"]: row.get("family_id") for row in study.get("cases", [])}
        for index, row in enumerate(rows):
            source_value = row.get("video_path")
            row["original_video_path"] = source_value
            row["original_render_status"] = row.get("status")
            row["video_path"] = None
            if not source_value:
                row["delivery_status"] = "no_original_video"
                continue
            folder = output / "videos" / f"{index:04d}"
            folder.mkdir(parents=True)
            try:
                source = _path(source_value)
                if not 0 < source.stat().st_size <= SETTINGS["max_source_video_bytes"]:
                    raise DeliveryError("video_size", "Original video exceeds the 35 MiB limit")
                source_metadata = _probe(binary, source, folder, "source_probe", operations)
                if source_metadata["has_audio"]:
                    raise DeliveryError("unexpected_original_audio", "Original video already contains audio; this silent-compositor delivery route would discard it")
                family = row.get("family_id") or family_by_case.get(row.get("case_id"))
                audio_info = audio_map.get(family)
                args = _base() + _input(source)
                if audio_info:
                    args += _input(audio_info["path"])
                target = folder / "playback.mp4"
                args += ["-map", "0:v:0"]
                if audio_info:
                    args += ["-map", "1:a:0", "-af", "atrim=start=0:end=3,asetpts=PTS-STARTPTS", "-c:a", "aac", "-b:a", "128k"]
                else:
                    args += ["-an"]
                args += ["-t", "3", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                         "-threads", "1", "-movflags", "+faststart", "-map_metadata", "-1", "-map_chapters", "-1",
                         "-fs", str(SETTINGS["max_output_bytes"]), str(target)]
                _run(binary, args, folder, "encode", operations, destination=target, byte_limit=SETTINGS["max_output_bytes"])
                metadata = _probe(binary, target, folder, "delivery_probe", operations)
                if abs(metadata["duration_seconds_reported"] - source_metadata["duration_seconds_reported"]) > 0.1:
                    raise DeliveryError("delivery_duration", "Delivery duration differs from the original static-card video")
                if "Video: h264" not in metadata["video_stream"] or "yuv420p" not in metadata["video_stream"]:
                    raise DeliveryError("delivery_codec", "Delivery codec is not H264/yuv420p")
                if metadata["has_audio"] != bool(audio_info) or any("Audio: aac" not in line for line in metadata["audio_streams"]):
                    raise DeliveryError("delivery_audio", "Delivery audio does not match the requested optional AAC track")
                validation = _validate_video(binary, target, folder, operations, metadata)
                if hash_file(source) != original_hashes[str(source)]:
                    raise DeliveryError("original_changed", "Original video hash changed")
                conversion = {"response_id": row.get("response_id"), "case_id": row.get("case_id"),
                    "condition": row.get("condition"), "family_id": family, "original_path": str(source),
                    "original_sha256": original_hashes[str(source)], "video_path": str(target), "sha256": hash_file(target),
                    "bytes": target.stat().st_size, "source_metadata": source_metadata, "delivery_metadata": metadata,
                    "validation": validation, "audio_source_sha256": audio_info["sha256"] if audio_info else None,
                    "audio_source_offset_seconds": 0 if audio_info else None, "audio_excerpt_seconds": 3 if audio_info else None}
                conversions.append(conversion)
                row.update(video_path=str(target), delivery_status="succeeded", delivery_run_id=entry["run_id"],
                           playback_validation=validation, audio_source_sha256=conversion["audio_source_sha256"])
                if audio_info and not any(c["sha256"] == audio_info["sha256"] for c in credits):
                    credits.append({k: audio_info.get(k) for k in ("author", "source_url", "license", "license_url", "sha256", "provenance")} | {
                        "changes": "원본에서 디코딩한 16 kHz 모노 WAV의 앞 3초를 잘라 AAC로 인코딩하고 해당 요청의 모든 조건 영상에 같은 음원으로 결합했습니다.",
                        "native_audio_model_input": False, "music_generated": False})
            except Exception as exc:
                failure = {"response_id": row.get("response_id"), "case_id": row.get("case_id"), "condition": row.get("condition"),
                           "error_category": getattr(exc, "category", type(exc).__name__), "message": str(exc), "partial_files": str(folder)}
                failures.append(failure)
                row.update(status="delivery_failed", delivery_status="failed", delivery_error=failure)
                _write(folder / "failure.json", failure)
            _write(output / "operations.json", operations)
        after = {path: hash_file(path) for path in original_hashes}
        manifest["original_sha256_after"] = after
        manifest["original_bytes_preserved"] = after == original_hashes
        if not manifest["original_bytes_preserved"] or hash_file(binary) != binary_sha:
            raise DeliveryError("input_changed", "An original input or FFmpeg executable changed during delivery")
        delivery_summary = {**rendered, "schema_version": "creation-delivery-render-v1", "run_id": entry["run_id"],
            "original_render_run_id": rendered.get("run_id"), "created_at": _now(), "outputs": rows,
            "delivery_succeeded": len(conversions), "delivery_failed": len(failures), "delivery_settings": SETTINGS,
            "delivery_model_calls": 0, "delivery_native_audio_understanding_verified": False,
            "delivery_scope": "Original text/image preserved; H264 video copies with optional identical prerecorded audio excerpt. No music generator training."}
        _write(output / "render_summary.json", delivery_summary)
        _write(output / "attribution.json", {"audio": credits, "original_artifact_source": str(render_summary) if isinstance(render_summary, (str, Path)) else "provided render summary", "original_artifacts_preserved": True})
        credit_text = "\n\n".join(c["author"] + "\n" + c["source_url"] + "\n" + c["license"] + " " + c["license_url"] + "\n" + c["changes"] for c in credits)
        (output / "attribution.txt").write_text(credit_text or "No audio was added. Original artifact attribution remains applicable.\n", encoding="utf-8")
        report_dir = output / "report"
        report = build_report(study, model, delivery_summary, report_dir)
        credit_html = _credit_html(credits)
        for page in (report_dir / "gallery.html", report_dir / "human_pack/index.html"):
            text = page.read_text(encoding="utf-8")
            page.write_text(text.replace("</main>", credit_html + "</main>", 1), encoding="utf-8")
        # These are new reports only. Refresh their own hashes after adding the
        # same condition-neutral credits to the public gallery and rating pack.
        report["delivery_audio_credits"] = credits
        report["files_sha256"] = {str(p.relative_to(report_dir)): hash_file(p) for p in sorted(report_dir.rglob("*"))
                                  if p.is_file() and p != report_dir / "manifest.json"}
        _write(report_dir / "manifest.json", report)
        manifest.update(status="succeeded" if not failures else "completed_with_failures", finished_at=_now(),
                        converted_count=len(conversions), failed_count=len(failures), planned_output_count=len(rows),
                        report_gallery=str(report_dir / "gallery.html"), human_pack=str(report_dir / "human_pack/index.html"),
                        render_summary_path=str(output / "render_summary.json"))
        _write(output / "operations.json", operations)
        _write(output / "manifest.json", manifest)
        registry.finish(entry["run_id"], status="failed" if failures else "succeeded",
            notes=f"Delivery converted {len(conversions)} existing videos; {len(failures)} conversion failures retained. Original bytes preserved; no model calls or human ratings.",
            artifacts=sorted(p for p in output.rglob("*") if p.is_file()),
            actual_cost={"amount": None, "currency": None, "api_units": 0, "human_minutes": 0})
        return {"render_summary": delivery_summary, "report": report, "manifest": manifest}
    except Exception as exc:
        manifest.update(status="failed", finished_at=_now(), error_category=getattr(exc, "category", type(exc).__name__), message=str(exc))
        if output.is_dir():
            _write(output / "operations.json", operations)
            _write(output / "failure.json", manifest)
        registry.finish(entry["run_id"], status="failed", notes=manifest["error_category"] + ": " + str(exc),
            artifacts=sorted(p for p in output.rglob("*") if p.is_file()) if output.is_dir() else [],
            actual_cost={"amount": None, "currency": None, "api_units": 0, "human_minutes": 0})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--model-summary", required=True)
    parser.add_argument("--render-summary", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--audio-by-family", help="Local JSON mapping exact family IDs to audio paths/metadata")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--db")
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = parser.parse_args()
    result = build_delivery(args.spec, args.model_summary, args.render_summary, args.output_dir,
                            audio_by_family=args.audio_by_family, ffmpeg=args.ffmpeg, db=args.db,
                            retry_of=args.retry_of, retry_reason=args.retry_reason)
    print(json.dumps({"run_id": result["manifest"]["run_id"], "status": result["manifest"]["status"],
                      "report": result["manifest"]["report_gallery"], "converted_count": result["manifest"]["converted_count"],
                      "failed_count": result["manifest"]["failed_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
