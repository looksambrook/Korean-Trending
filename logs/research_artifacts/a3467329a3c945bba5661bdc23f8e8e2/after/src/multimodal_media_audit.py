"""Bounded, read-only local media decoding audit, with an execution registry.

This verifies readable bytes/pixels/samples, never meme meaning, provenance,
rights, content creation, or model learning. No network or installation occurs.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

from research_log import DuplicateRunError, ResearchLog, hash_file

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {"max_assets": 20, "max_file_bytes": 32 * 1024**2, "max_total_bytes": 128 * 1024**2,
    "max_manifest_bytes": 4 * 1024**2, "timeout_seconds": 15, "max_pixels": 12_000_000,
    "max_image_frames": 12, "max_audio_seconds": 30, "max_decoded_audio_bytes": 64 * 1024**2,
    "max_video_frames": 12, "video_mode": "sample_frames"}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def _bounded_hash(path, maximum):
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as handle:
        while chunk := handle.read(min(1024 * 1024, maximum - size + 1)):
            size += len(chunk)
            if size > maximum:
                raise ValueError("hash_read_byte_limit")
            digest.update(chunk)
    return digest.hexdigest()


def decoder_inventory():
    result = {"python": sys.version.split()[0], "wave": {"available": True, "scope": "PCM WAV via Python stdlib"}}
    for module, distribution in (("PIL", "Pillow"), ("cv2", "opencv-python"), ("av", "av"),
                                  ("soundfile", "soundfile"), ("imageio_ffmpeg", "imageio-ffmpeg")):
        available = importlib.util.find_spec(module) is not None
        try:
            version = importlib.metadata.version(distribution) if available else None
        except importlib.metadata.PackageNotFoundError:
            version = "unknown_distribution"
        result[module] = {"available": available, "version": version,
                          "adapter_implemented": module in {"PIL", "cv2"}}
    result["ffmpeg"] = {"path": shutil.which("ffmpeg"), "adapter_implemented": False}
    result["ffprobe"] = {"path": shutil.which("ffprobe"), "adapter_implemented": False}
    return result


def _text(path, limits):
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    characters, lines = 0, 0
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            text = decoder.decode(chunk)
            characters += len(text)
            lines += text.count("\n")
        text = decoder.decode(b"", final=True)
        characters += len(text)
        lines += text.count("\n")
    return {"status": "decoded_full", "decoder": "stdlib.incremental_utf8", "encoding": "utf-8-sig",
            "characters": characters, "newline_count": lines, "coverage": "all_file_bytes"}


def _image(path, limits):
    if importlib.util.find_spec("PIL") is None:
        return {"status": "decoder_unavailable", "reason": "pillow_not_installed"}
    import warnings
    from PIL import Image, __version__
    Image.MAX_IMAGE_PIXELS = limits["max_pixels"]
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            width, height = image.size
            if width * height > limits["max_pixels"]:
                return {"status": "skipped_limit", "reason": "image_pixel_limit", "width": width, "height": height}
            count = getattr(image, "n_frames", 1)
            indices = _indices(count, limits["max_image_frames"])
            decoded = []
            for index in indices:
                image.seek(index)
                if image.width * image.height > limits["max_pixels"]:
                    return {"status": "skipped_limit", "reason": "image_frame_pixel_limit", "decoded_frames": decoded}
                image.load()  # Actual pixel decoding; no save, resize, or image mutation.
                decoded.append(index)
            return {"status": "decoded_full" if len(decoded) == count else "decoded_sample",
                "decoder": "Pillow", "decoder_version": __version__, "format_detected": image.format,
                "width": width, "height": height, "mode": image.mode, "frame_count_declared": count,
                "decoded_frame_indices": decoded,
                "coverage": "all_image_frames" if len(decoded) == count else "selected_image_frames"}


def _indices(count, maximum):
    if count <= maximum:
        return list(range(max(count, 0)))
    if maximum == 1:
        return [0]
    return sorted({round(i * (count - 1) / (maximum - 1)) for i in range(maximum)})


def _audio(path, limits):
    import wave
    with path.open("rb") as handle:
        header = handle.read(12)
    if not (header[:4] in {b"RIFF", b"RIFX"} and header[8:12] == b"WAVE"):
        return {"status": "decoder_unavailable", "reason": "only_pcm_wav_audio_adapter_available",
                "audio_stream_status": "uninspected", "audio_decoded": False}
    try:
        with wave.open(str(path), "rb") as audio:
            channels, rate, width, count = audio.getnchannels(), audio.getframerate(), audio.getsampwidth(), audio.getnframes()
            if min(channels, rate, width, count) <= 0 or audio.getcomptype() != "NONE":
                raise ValueError("invalid_or_empty_pcm_stream")
            bytes_per_frame = channels * width
            target = min(count, int(rate * limits["max_audio_seconds"]), limits["max_decoded_audio_bytes"] // bytes_per_frame)
            if target < 1:
                return {"status": "skipped_limit", "reason": "decoded_audio_byte_limit"}
            decoded, all_zero = 0, True
            while decoded < target:
                requested = min(8192, target - decoded)
                chunk = audio.readframes(requested)
                if len(chunk) != requested * bytes_per_frame:
                    raise ValueError("truncated_pcm_payload")
                decoded += requested
                silent_value = 128 if width == 1 else 0
                all_zero = all_zero and all(byte == silent_value for byte in chunk)
            full = decoded == count
            return {"status": "decoded_full" if full else "decoded_sample", "decoder": "stdlib.wave",
                "audio_stream_status": "present", "audio_stream_count": 1, "audio_decoded": True,
                "channels": channels, "sample_rate": rate, "sample_width_bytes": width,
                "duration_seconds_declared": count / rate, "decoded_frames": decoded,
                "decoded_duration_seconds": decoded / rate, "decoded_samples_exact_digital_silence": all_zero,
                "silence_claim_scope": "decoded_samples_only", "coverage": "all_pcm_frames" if full else "prefix_audio_segment"}
    except wave.Error as exc:
        reason = "wav_encoding_not_supported_by_stdlib" if "unknown format" in str(exc) else "invalid_wav"
        return {"status": "decoder_unavailable" if "unknown format" in str(exc) else "decode_failed",
                "reason": reason, "audio_decoded": False, "audio_stream_status": "uninspected"}


def _video(path, limits):
    if importlib.util.find_spec("cv2") is None:
        return {"status": "decoder_unavailable", "reason": "opencv_not_installed", "audio_stream_status": "uninspected"}
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return {"status": "decode_failed", "reason": "opencv_cannot_open_video",
                    "audio_stream_status": "uninspected", "audio_decoded": False}
        def number(prop):
            value = cap.get(prop)
            return value if math.isfinite(value) and value > 0 else None
        count_value, fps = number(cv2.CAP_PROP_FRAME_COUNT), number(cv2.CAP_PROP_FPS)
        count = int(count_value) if count_value else None
        result = {"decoder": "OpenCV", "decoder_version": cv2.__version__, "backend": cap.getBackendName(),
            "video_stream_status": "present", "video_stream_count": None, "audio_stream_status": "uninspected",
            "audio_stream_count": None, "audio_decoded": False, "silent_video_confirmed": False,
            "frame_count_decoder_reported": count, "fps_decoder_reported": fps,
            "duration_seconds_estimated": count / fps if count and fps else None,
            "duration_source": "decoder_frame_count_divided_by_fps_estimate", "decoded_frames": []}
        width, height = number(cv2.CAP_PROP_FRAME_WIDTH), number(cv2.CAP_PROP_FRAME_HEIGHT)
        if width and height and width * height > limits["max_pixels"]:
            return {**result, "status": "skipped_limit", "reason": "video_frame_pixel_limit"}
        sequential = limits["video_mode"] == "sequential" or not count
        requested = range(limits["max_video_frames"]) if sequential else _indices(count, limits["max_video_frames"])
        ended = False
        for index in requested:
            if not sequential and index != 0 and not cap.set(cv2.CAP_PROP_POS_FRAMES, index):
                return {**result, "status": "decode_failed", "reason": "video_seek_failed", "requested_frame": index}
            ok, frame = cap.read()
            if not ok:
                ended = True
                if not sequential:
                    return {**result, "status": "decode_failed", "reason": "sampled_video_frame_decode_failed", "requested_frame": index}
                break
            if frame is None or frame.size == 0:
                return {**result, "status": "decode_failed", "reason": "empty_decoded_video_frame"}
            if frame.shape[0] * frame.shape[1] > limits["max_pixels"]:
                return {**result, "status": "skipped_limit", "reason": "decoded_video_frame_pixel_limit"}
            result["decoded_frames"].append({"requested_index": index,
                "decoder_position_after_read": cap.get(cv2.CAP_PROP_POS_FRAMES),
                "decoder_timestamp_ms": cap.get(cv2.CAP_PROP_POS_MSEC),
                "width": int(frame.shape[1]), "height": int(frame.shape[0])})
        decoded = len(result["decoded_frames"])
        if not decoded or (sequential and ended and count and decoded < count):
            return {**result, "status": "decode_failed", "reason": "video_ended_before_reported_frame_count"}
        complete_reported = bool(count and decoded == count and (sequential or count <= limits["max_video_frames"]))
        return {**result, "status": "decoded_full" if complete_reported else "decoded_sample",
            "coverage": "all_reported_video_frames" if complete_reported else "prefix_video_frames" if sequential else "selected_video_frames",
            "full_container_integrity_verified": False,
            "limitations": ["OpenCV does not establish audio track absence or inspect audio samples.",
                "Reported frame count and timestamps can be approximate; no claim of full audiovisual decoding."]}
    finally:
        cap.release()


def _worker(payload):
    began = time.perf_counter()
    cpu = time.process_time()
    try:
        path = Path(payload["path"])
        limits = payload["limits"]
        if not path.is_file() or path.stat().st_size > limits["max_file_bytes"]:
            return {"status": "skipped_limit", "reason": "worker_file_guard"}
        result = {"text": _text, "image": _image, "audio": _audio, "video": _video}[payload["modality"]](path, limits)
    except Exception as exc:
        result = {"status": "decode_failed", "reason": type(exc).__name__}
    result["decode_wall_seconds"] = round(time.perf_counter() - began, 6)
    result["decode_cpu_seconds"] = round(time.process_time() - cpu, 6)
    return result


def _subprocess_decode(path, modality, limits):
    payload = json.dumps({"path": str(path), "modality": modality, "limits": limits})
    start = time.perf_counter()
    try:
        proc = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--_worker", payload],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=limits["timeout_seconds"], check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"status": "decode_timeout", "reason": "decoder_process_timeout", "wall_seconds": round(time.perf_counter()-start, 6)}
    if len(proc.stdout) > 1_000_000 or proc.returncode != 0:
        return {"status": "decode_failed", "reason": "decoder_process_failed", "returncode": proc.returncode}
    try:
        result = json.loads(proc.stdout.decode("utf-8"))
    except (ValueError, UnicodeError):
        return {"status": "decode_failed", "reason": "decoder_result_invalid"}
    result["process_wall_seconds"] = round(time.perf_counter()-start, 6)
    result["decoder_stderr_bytes"] = len(proc.stderr)
    return result


def _write(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _assets(path, max_bytes):
    if path.stat().st_size > max_bytes:
        raise ValueError("input_manifest_byte_limit")
    assets = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("JSONL_requires_objects")
            entries = row.get("assets", [row])
            if not isinstance(entries, list):
                raise ValueError("assets_must_be_array")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("asset_must_be_object")
                assets.append({**entry, "input_line": line_number})
    return assets


def _asset_snapshots(source, base, options):
    """Bounded identity preparation only; decode starts after registry begin."""
    snapshots, digests, cache, total = [], {}, {}, 0
    try:
        assets = _assets(source, options["max_manifest_bytes"])
    except Exception as exc:
        return snapshots, digests, type(exc).__name__
    for index, asset in enumerate(assets[:options["max_assets"]]):
        try:
            if asset.get("status") in {"restricted", "missing", "excluded"}:
                continue
            value = asset.get("local_path")
            if not isinstance(value, str) or not value:
                continue
            path = Path(value)
            path = (base / path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_relative_to(base) or not path.is_file():
                continue
            size = path.stat().st_size
            if size > options["max_file_bytes"]:
                continue
            if path not in cache:
                if total + size > options["max_total_bytes"]:
                    continue
                cache[path] = _bounded_hash(path, options["max_file_bytes"])
                total += size
            digests[index] = cache[path]
            snapshots.append({"id": f"asset_bytes_{index}", "sha256": cache[path]})
        except (OSError, ValueError):
            # The registered audit reports unavailable/changed assets without decoding them.
            continue
    return snapshots, digests, None


def audit(input_path, *, output_dir, db=None, asset_root=ROOT, limits=None, retry_of=None, retry_reason=None, test_only=False):
    options = {**DEFAULTS, **(limits or {})}
    if set(options) != set(DEFAULTS) or options["video_mode"] not in {"sample_frames", "sequential"}:
        raise ValueError("invalid_audit_limits")
    for key, value in options.items():
        if key != "video_mode" and (type(value) is not int or value < 1):
            raise ValueError("limits_must_be_positive_integers")
    if options["max_assets"] > 100 or options["timeout_seconds"] > 60 or options["max_file_bytes"] > 512 * 1024**2 or options["max_pixels"] > 40_000_000:
        raise ValueError("audit_hard_limit_exceeded")
    source, destination, base = Path(input_path).resolve(), Path(output_dir).resolve(), Path(asset_root).resolve()
    inventory = decoder_inventory()
    source_size = source.stat().st_size
    source_digest = _bounded_hash(source, options["max_manifest_bytes"]) if source_size <= options["max_manifest_bytes"] else None
    asset_snapshots, registered_digests, identity_error = _asset_snapshots(source, base, options)
    spec = {"objective": "Bounded local media readability audit, without interpreting meme content or changing source files",
        "stage": "offline_validation", "parameters": {"limits": options, "asset_root": str(base), "decoders": inventory, "test_only": test_only,
            "input_manifest_bytes": source_size, "input_manifest_over_limit": source_digest is None, "identity_preparation_error": identity_error},
        "data_snapshots": ([{"id": "asset_input_jsonl", "sha256": source_digest}] if source_digest else []) + asset_snapshots,
        "code_hashes": {"media_audit": hash_file(Path(__file__))}, "config_hashes": {}, "prompt_hashes": {},
        "modality": ["text", "image", "audio", "video"], "provenance": "ai_synthetic" if test_only else "not_applicable"}
    log = ResearchLog(db or ROOT / "logs/research.sqlite3")
    run = log.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    manifest = {"schema_version": "multimodal-media-audit-v1", "run_id": run["run_id"], "started_at": stamp(),
        "status": "started", "input_sha256": source_digest, "test_only": test_only, "limits": options,
        "registered_asset_snapshots": asset_snapshots,
        "decoders": inventory, "results": [], "issues": [], "total_audited_file_bytes": 0,
        "readability_verified_assets": 0, "unavailable_or_failed_or_limited_assets": 0,
        "source_content_modified": False, "source_provenance_verified": False, "model_calls": 0,
        "network_requests": 0, "human_review_minutes": 0, "local_compute_cost": None,
        "claim_scope": "existing_local_file_readability_only_not_meme_understanding_generation_or_training"}
    created, artifacts, start = False, [], time.perf_counter()
    try:
        destination.mkdir(parents=True, exist_ok=False)
        created = True
        if source_digest and _bounded_hash(source, options["max_manifest_bytes"]) != source_digest:
            manifest["issues"].append({"reason": "input_changed_after_identity_preparation"})
            raise ValueError("input_changed_after_identity_preparation")
        assets = _assets(source, options["max_manifest_bytes"])
        manifest["planned_assets"] = len(assets)
        if not assets:
            manifest["issues"].append({"reason": "empty_input"})
            raise ValueError("empty_input")
        seen = {}
        for index, asset in enumerate(assets):
            result = {"asset_id": asset.get("asset_id"), "input_line": asset["input_line"],
                "declared_modality": asset.get("modality"), "declared_provenance": asset.get("provenance"),
                "expected_sha256": asset.get("sha256"), "checked_at": stamp()}
            manifest["results"].append(result)
            if index >= options["max_assets"]:
                result.update(status="skipped_limit", reason="asset_count_limit")
                continue
            try:
                if asset.get("status") in {"restricted", "missing", "excluded"}:
                    result.update(status="input_unavailable", reason="declared_asset_" + asset["status"])
                    continue
                value = asset.get("local_path")
                if not isinstance(value, str) or not value:
                    result.update(status="input_unavailable", reason="local_path_missing")
                    continue
                path = Path(value)
                path = (base / path).resolve() if not path.is_absolute() else path.resolve()
                result["resolved_local_path"] = str(path)
                if not path.is_relative_to(base):
                    result.update(status="input_unavailable", reason="asset_outside_declared_root")
                    continue
                if not path.is_file():
                    result.update(status="input_unavailable", reason="local_file_missing")
                    continue
                size = path.stat().st_size
                result["file_bytes"] = size
                if size > options["max_file_bytes"] or manifest["total_audited_file_bytes"] + size > options["max_total_bytes"]:
                    result.update(status="skipped_limit", reason="file_or_total_byte_limit")
                    continue
                measured = _bounded_hash(path, options["max_file_bytes"])
                result["measured_sha256"] = measured
                if registered_digests.get(index) != measured:
                    result.update(status="source_changed_before_audit", reason="asset_bytes_differ_from_registered_identity")
                    continue
                expected = asset.get("sha256")
                if expected is not None and expected != measured:
                    result.update(status="hash_mismatch", reason="declared_asset_hash_differs")
                    continue
                key = (str(path), measured, asset.get("modality"))
                if key in seen:
                    previous = seen[key]
                    result.update(status="duplicate_asset" if previous.get("status") in {"decoded_full", "decoded_sample"} else "duplicate_of_unreadable_asset",
                        reason="same_path_hash_modality_already_audited", referenced_asset_id=previous["asset_id"], referenced_status=previous.get("status"))
                    continue
                seen[key] = result
                if asset.get("modality") not in {"text", "image", "audio", "video"}:
                    result.update(status="input_unavailable", reason="unsupported_declared_modality")
                    continue
                manifest["total_audited_file_bytes"] += size
                result.update(_subprocess_decode(path, asset["modality"], options))
                result["post_read_sha256"] = _bounded_hash(path, options["max_file_bytes"])
                result["source_bytes_unchanged"] = result["post_read_sha256"] == measured
                if not result["source_bytes_unchanged"]:
                    result.update(status="source_changed_during_audit", reason="before_after_hash_different")
            except Exception as exc:
                result.update(status="audit_failed", reason=type(exc).__name__)
        successes = {"decoded_full", "decoded_sample", "duplicate_asset"}
        failures = [r for r in manifest["results"] if r["status"] not in successes]
        manifest["status"] = "completed" if not failures else "partial" if any(r["status"] in successes for r in manifest["results"]) else "failed"
        manifest["readability_verified_assets"] = sum(r["status"] in {"decoded_full", "decoded_sample"} for r in manifest["results"])
        manifest["unavailable_or_failed_or_limited_assets"] = len(failures)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["issues"].append({"reason": type(exc).__name__})
    finally:
        manifest["finished_at"] = stamp()
        manifest["wall_seconds"] = round(time.perf_counter()-start, 6)
        if created:
            _write(destination / "manifest.json", manifest)
            artifacts.append(destination / "manifest.json")
        log.finish(run["run_id"], status="succeeded" if manifest["status"] == "completed" else "failed",
            notes=f"Local read-only media audit {manifest['status']}; inspected={manifest.get('readability_verified_assets',0)}; test_only={test_only}. Decode coverage is per asset; no source attribution, meme interpretation, model execution or full audiovisual claim.",
            artifacts=artifacts, actual_cost={"amount": None, "currency": None, "api_units": 0, "human_minutes": 0})
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--asset-root", default=str(ROOT))
    parser.add_argument("--db")
    parser.add_argument("--max-assets", type=int, default=DEFAULTS["max_assets"])
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULTS["max_file_bytes"])
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULTS["timeout_seconds"])
    parser.add_argument("--video-mode", choices=["sample_frames", "sequential"], default="sample_frames")
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = parser.parse_args(argv)
    try:
        result = audit(args.input, output_dir=args.output_dir, db=args.db, asset_root=args.asset_root,
            limits={key: getattr(args, key) for key in ("max_assets", "max_file_bytes", "timeout_seconds", "video_mode")},
            retry_of=args.retry_of, retry_reason=args.retry_reason)
        print(json.dumps({key: result.get(key) for key in ("run_id", "status", "readability_verified_assets", "unavailable_or_failed_or_limited_assets")}, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 2
    except DuplicateRunError as exc:
        print(json.dumps({"error": "duplicate_run", "existing_run_id": exc.existing["run_id"]}))
        return 3
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--_worker":
        sys.stdout.buffer.write(json.dumps(_worker(json.loads(sys.argv[2])), ensure_ascii=False, allow_nan=False).encode("utf-8"))
    else:
        raise SystemExit(main())
