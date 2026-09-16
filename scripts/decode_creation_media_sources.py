"""Bounded, logged local decoding of collected audio and control-video bytes.

This prepares media; it does not run a model, identify a meme, or evaluate quality.
Only a new output directory is written. Source files are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from research_log import ResearchLog, hash_file, read_json

PROCESS_SECONDS = 60
MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_FRAME_BYTES = 32 * 1024 * 1024
MAX_WAV_BYTES = 512 * 1024
MAX_PIXELS = 16_000_000


class MediaDecodeError(RuntimeError):
    def __init__(self, category, message):
        self.category = category
        super().__init__(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def local_path(value, *, file=False):
    path = Path(value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("All input and output paths must be inside the project")
    if file and not path.is_file():
        raise ValueError("Required local input file is missing: " + path.name)
    return path


def validate_wav(path):
    with wave.open(str(path), "rb") as handle:
        params = handle.getparams()
        if (params.nchannels, params.sampwidth, params.framerate, params.comptype) != (1, 2, 16000, "NONE"):
            raise MediaDecodeError("wav_format", "Decoded WAV must be mono 16 kHz 16-bit PCM")
        pcm = handle.readframes(params.nframes)
        if not 0 < params.nframes <= 240000 or len(pcm) != params.nframes * 2:
            raise MediaDecodeError("wav_decode", "WAV frames are empty, truncated, or longer than the requested 15 seconds")
    return {"actual_decode_verified": True, "validation": "Python wave decoded all PCM frames",
            "sample_rate": 16000, "channels": 1, "sample_width_bytes": 2,
            "frames": params.nframes, "duration_seconds": params.nframes / 16000,
            "decoded_pcm_sha256": hashlib.sha256(pcm).hexdigest()}


def validate_frame(path):
    from PIL import Image
    with Image.open(path) as frame:
        if frame.format != "PNG" or frame.mode != "RGB" or frame.width * frame.height > MAX_PIXELS:
            raise MediaDecodeError("frame_format", "Frame must be a bounded RGB PNG")
        frame.load()
        pixels = frame.tobytes()
        return {"actual_decode_verified": True, "validation": "Pillow loaded all RGB pixels",
                "width": frame.width, "height": frame.height, "mode": frame.mode,
                "decoded_pixel_sha256": hashlib.sha256(pixels).hexdigest()}


def decode(audio, video, ffmpeg, output_dir, source_manifest, db, *, retry_of=None, retry_reason=None):
    paths = {"audio": local_path(audio, file=True), "video": local_path(video, file=True)}
    binary = local_path(ffmpeg, file=True)
    source_manifest = local_path(source_manifest, file=True)
    output = local_path(output_dir)
    if output.exists():
        raise FileExistsError("Output directory must be new: " + str(output))
    # Content hashes identify this run before any decoding process is started.
    before = {kind: hash_file(path) for kind, path in paths.items()}
    tool_sha = hash_file(binary)
    spec = {"objective": "Decode collected reused soundtrack and nonmeme control video locally; preserve original bytes",
            "stage": "offline_validation",
            "parameters": {"audio_seconds": 15, "sample_rate": 16000, "channels": 1,
                           "pcm": "s16le", "audio_filter": "aresample=16000,atrim=end_sample=240000",
                           "frame_offsets_seconds": [0, 5], "ffmpeg_threads": 1,
                           "process_timeout_seconds": PROCESS_SECONDS, "max_input_bytes": MAX_INPUT_BYTES,
                           "max_frame_bytes": MAX_FRAME_BYTES, "max_wav_bytes": MAX_WAV_BYTES,
                           "max_log_bytes": MAX_LOG_BYTES, "max_frame_pixels": MAX_PIXELS,
                           "source_roles": ["documented_reused_comedic_soundtrack_candidate", "nonmeme_control_candidate"],
                           "network_access": False, "model_calls": 0, "human_evaluations": 0},
            "data_snapshots": [{"id": kind + "_source", "sha256": digest} for kind, digest in before.items()]
                              + [{"id": "collection_manifest", "sha256": hash_file(source_manifest)}],
            "code_hashes": {"decoder": hash_file(__file__), "research_log": hash_file(ROOT / "src/research_log.py")},
            "prompt_hashes": {}, "config_hashes": {"ffmpeg_executable": tool_sha},
            "modality": ["audio", "video", "image"], "provenance": "actual_collected"}
    registry = ResearchLog(local_path(db))
    entry = registry.begin(spec, retry_of=retry_of, retry_reason=retry_reason)
    operations, artifacts, source_rows = [], [], {}
    started_at = now()
    result = {"run_id": entry["run_id"], "started_at": started_at, "status": "started",
              "source_manifest": str(source_manifest.relative_to(ROOT)),
              "source_manifest_sha256": hash_file(source_manifest),
              "ffmpeg": {"path": str(binary.relative_to(ROOT)), "sha256": tool_sha},
              "operations": operations, "outputs": artifacts,
              "research_model_calls": 0, "native_audio_model_run": False, "native_video_model_run": False,
              "human_evaluations": 0, "independent_gold": False,
              "local_compute_cost": {"amount": None, "currency": None},
              "scope": "Actual collected media decoded into derived files. No semantic media review, meme recognition, model exposure, or independent gold validation is claimed."}

    def process(name, arguments, *, source_kind=None, destination=None, output_limit=0, expected_codes=(0,)):
        stdout_path, stderr_path = output / (name + ".stdout.log"), output / (name + ".stderr.log")
        op = {"operation": name, "started_at": now(), "command": [str(binary), *arguments],
              "source_kind": source_kind, "status": "started", "timeout_seconds": PROCESS_SECONDS,
              "stdout_log": stdout_path.name, "stderr_log": stderr_path.name}
        operations.append(op)
        if source_kind:
            op["source_sha256_before"] = hash_file(paths[source_kind])
            if op["source_sha256_before"] != before[source_kind]:
                raise MediaDecodeError("source_changed", "Source bytes changed before conversion")
        tick = time.monotonic()
        try:
            with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
                with subprocess.Popen([str(binary), *arguments], stdin=subprocess.DEVNULL,
                                      stdout=stdout, stderr=stderr, cwd=ROOT,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
                    while child.poll() is None:
                        category = None
                        if time.monotonic() - tick > PROCESS_SECONDS:
                            category = "process_timeout"
                        elif stdout_path.stat().st_size + stderr_path.stat().st_size > MAX_LOG_BYTES:
                            category = "process_log_limit"
                        elif destination and destination.exists() and destination.stat().st_size > output_limit:
                            category = "output_size_limit"
                        if category:
                            child.kill()
                            child.wait(timeout=5)
                            raise MediaDecodeError(category, name + " exceeded its configured bound")
                        time.sleep(0.05)
                    op["returncode"] = child.returncode
                    if child.returncode not in expected_codes:
                        raise MediaDecodeError("ffmpeg_process", name + " returned " + str(child.returncode) + "; inspect its stderr log")
            if stdout_path.stat().st_size + stderr_path.stat().st_size > MAX_LOG_BYTES:
                raise MediaDecodeError("process_log_limit", name + " exceeded log limit")
            if destination and (not destination.is_file() or not 0 < destination.stat().st_size <= output_limit):
                raise MediaDecodeError("output_size_limit", name + " produced no bounded output file")
            op["status"] = "succeeded"
        except Exception as exc:
            op.update(status="failed", error_category=getattr(exc, "category", type(exc).__name__), error=str(exc))
            raise
        finally:
            op.update(finished_at=now(), wall_seconds=round(time.monotonic() - tick, 6))
            if source_kind:
                op["source_sha256_after"] = hash_file(paths[source_kind])
                op["source_bytes_preserved"] = op["source_sha256_after"] == before[source_kind]
            for log_path in (stdout_path, stderr_path):
                if log_path.is_file():
                    op[log_path.suffixes[-2][1:] + "_sha256"] = hash_file(log_path)
            write_json(output / "operations.json", operations)
        if source_kind and not op["source_bytes_preserved"]:
            raise MediaDecodeError("source_changed", "Source bytes changed during conversion")
        return op, stdout_path.read_text(encoding="utf-8", errors="replace"), stderr_path.read_text(encoding="utf-8", errors="replace")

    def base(kind):
        return ["-hide_banner", "-nostdin", "-n", "-threads", "1", "-filter_threads", "1",
                "-filter_complex_threads", "1", "-max_alloc", "67108864",
                "-protocol_whitelist", "file", "-i", str(paths[kind])]

    def record_file(kind, destination, modality, op, check, offset):
        row = {"path": destination.name, "modality": modality, "provenance": "actual_collected",
               "derivation": "local_ffmpeg_decode", "parent_source_id": source_rows[kind]["source_id"],
               "source_group_id": source_rows[kind].get("source_group_id"),
               "source_role": source_rows[kind]["role"], "source_sha256": before[kind],
               "source_url": source_rows[kind].get("source_url"), "author": source_rows[kind].get("author"),
               "license": source_rows[kind].get("license"), "license_url": source_rows[kind].get("license_url"),
               "sha256": hash_file(destination), "bytes": destination.stat().st_size,
               "conversion_started_at": op["started_at"], "available_at": op["finished_at"],
               "requested_source_offset_seconds": offset, "exact_source_frame_pts_seconds": None,
               "offset_scope": "Requested FFmpeg seek/cut offset; exact original frame PTS was not independently measured",
               "changes": "Decoded, " + ("trimmed to at most 15 seconds, resampled to mono 16 kHz PCM" if modality == "audio" else "selected one frame and converted pixels to RGB PNG"),
               "validation": check, "training_supervision": False, "human_reviewed": False}
        artifacts.append(row)

    try:
        output.mkdir(parents=True)
        write_json(output / "registration.json", entry)
        metadata = read_json(source_manifest)
        for kind, path in paths.items():
            if not 0 < path.stat().st_size <= MAX_INPUT_BYTES:
                raise MediaDecodeError("input_size_limit", kind + " source exceeds input bounds")
            matches = [row for row in metadata.get("records", [])
                       if (source_manifest.parent / row.get("local_path", "")).resolve() == path]
            if len(matches) != 1 or matches[0].get("sha256") != before[kind]:
                raise MediaDecodeError("source_manifest_mismatch", kind + " source has no unique matching manifest digest")
            row = matches[0]
            if row.get("provenance") != "actual_collected" or row.get("modality") != kind:
                raise MediaDecodeError("source_provenance", kind + " source must declare actual collected media of its modality")
            expected_role = "documented_reused_comedic_soundtrack_candidate" if kind == "audio" else "nonmeme_control_candidate"
            if row.get("role") != expected_role:
                raise MediaDecodeError("source_role", kind + " source role differs from this bounded preparation run")
            source_rows[kind] = dict(row)
        result["sources"] = source_rows
        _, version_text, _ = process("ffmpeg_version", ["-version"])
        result["ffmpeg"]["version"] = version_text.splitlines()[0]
        result["ffmpeg"]["version_output_sha256"] = hash_file(output / "ffmpeg_version.stdout.log")
        # FFmpeg's input-only inspection convention returns 1 because no output
        # is requested. The identified stream metadata is checked separately.
        _, _, probe = process("video_stream_probe", base("video"), source_kind="video", expected_codes=(1,))
        if "Input #0" not in probe or "Video:" not in probe or "At least one output file must be specified" not in probe:
            raise MediaDecodeError("video_probe", "FFmpeg did not return recognizable video stream metadata")
        has_video_audio = any("Stream #0:" in line and "Audio:" in line for line in probe.splitlines())
        result["video_audio_track"] = {"present": has_video_audio, "basis": "FFmpeg local input stream metadata",
                                        "probe_log": "video_stream_probe.stderr.log"}
        for kind in ("audio", "video"):
            if kind == "video" and not has_video_audio:
                continue
            target = output / ("soundtrack_first15s_16k_mono.wav" if kind == "audio" else "control_video_first15s_16k_mono.wav")
            op, _, _ = process(kind + "_wav", base(kind) + ["-map", "0:a:0", "-vn", "-t", "15",
                                 "-af", "aresample=16000,atrim=end_sample=240000",
                                 "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-threads", "1",
                                 "-fs", str(MAX_WAV_BYTES), str(target)],
                               source_kind=kind, destination=target, output_limit=MAX_WAV_BYTES)
            record_file(kind, target, "audio", op, validate_wav(target), 0)
        for offset in (0, 5):
            target = output / ("control_video_frame_" + str(offset).zfill(2) + "s.png")
            op, _, _ = process("video_frame_" + str(offset), base("video") + ["-ss", str(offset),
                                 "-map", "0:v:0", "-an", "-frames:v", "1", "-pix_fmt", "rgb24",
                                 "-c:v", "png", "-threads", "1", "-fs", str(MAX_FRAME_BYTES), str(target)],
                               source_kind="video", destination=target, output_limit=MAX_FRAME_BYTES)
            record_file("video", target, "image", op, validate_frame(target), offset)
        result["source_sha256_after"] = {kind: hash_file(path) for kind, path in paths.items()}
        result["original_bytes_preserved"] = result["source_sha256_after"] == before
        if not result["original_bytes_preserved"]:
            raise MediaDecodeError("source_changed", "Final source digest does not match original")
        if hash_file(binary) != tool_sha:
            raise MediaDecodeError("tool_changed", "FFmpeg executable changed during conversion")
        result.update(status="succeeded", finished_at=now(), decoded_output_count=len(artifacts))
        write_json(output / "manifest.json", result)
        registry.finish(entry["run_id"], status="succeeded",
                        notes="Actual collected source bytes preserved. Derived PCM and RGB outputs decoded and verified; no model or human evaluation executed.",
                        artifacts=sorted(p for p in output.iterdir() if p.is_file()),
                        actual_cost={"amount": None, "currency": None, "human_minutes": 0})
        return result
    except Exception as exc:
        result.update(status="failed", finished_at=now(), error_category=getattr(exc, "category", type(exc).__name__), error=str(exc))
        result["source_sha256_after"] = {kind: hash_file(path) if path.is_file() else None for kind, path in paths.items()}
        result["original_bytes_preserved"] = result["source_sha256_after"] == before
        if output.is_dir():
            write_json(output / "failure.json", result)
        registry.finish(entry["run_id"], status="failed", notes=result["error_category"] + ": " + str(exc),
                        artifacts=sorted(p for p in output.iterdir() if p.is_file()) if output.is_dir() else [],
                        actual_cost={"amount": None, "currency": None, "human_minutes": 0})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--db", default=str(ROOT / "logs/research.sqlite3"))
    parser.add_argument("--retry-of")
    parser.add_argument("--retry-reason")
    args = parser.parse_args()
    result = decode(args.audio, args.video, args.ffmpeg, args.output_dir, args.source_manifest, args.db,
                    retry_of=args.retry_of, retry_reason=args.retry_reason)
    print(json.dumps({"run_id": result["run_id"], "status": result["status"],
                      "output_dir": str(Path(args.output_dir).resolve()), "decoded_output_count": result["decoded_output_count"],
                      "original_bytes_preserved": result["original_bytes_preserved"],
                      "video_audio_track": result["video_audio_track"]["present"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
