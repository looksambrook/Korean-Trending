"""Fixed local compositor for text, caption-card PNG and static-card MP4.

This renders a supplied structured plan. It does not generate a plan, identify
memes, train a model, or synthesize visual/audio content with a generative model.
Source assets are read-only. Optional WAV is preserved separately, not muxed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
import wave

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, __version__ as PIL_VERSION

ROOT = Path(__file__).resolve().parents[1]
FONT_PATH = Path("C:/Windows/Fonts/malgun.ttf")
STYLES = {"caption_card", "image_caption"}


class RenderError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _check(value, code, message):
    if not value:
        raise RenderError(code, message)


def _sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _project_file(value, project_root):
    _check(isinstance(value, str) and value, "invalid_source_path", "Source path must be nonempty")
    path = Path(value)
    path = (project_root / path).resolve() if not path.is_absolute() else path.resolve()
    _check(path.is_relative_to(project_root) and path.is_file(), "source_unavailable", "Source must be an existing file inside the project")
    return path


def _normalize(plan):
    _check(isinstance(plan, dict), "invalid_plan", "Plan must be an object")
    allowed = {"schema_version", "plan_id", "provenance", "plan_origin", "model_run_id", "text", "visual", "video", "source_image_path", "source_audio_path"}
    _check(not set(plan) - allowed, "invalid_plan", "Plan has unsupported fields")
    _check(plan.get("schema_version", "creation-plan-v1") == "creation-plan-v1", "invalid_plan", "Unsupported plan schema")
    _check(isinstance(plan.get("plan_id"), str) and plan["plan_id"], "invalid_plan", "plan_id required")
    _check(plan.get("provenance") in {"actual_collected", "researcher_authored", "ai_synthetic"}, "invalid_provenance", "Explicit plan provenance required")
    _check(plan.get("plan_origin") in {"synthetic_fixture", "model_generated", "researcher_authored", "source_transcription"}, "invalid_provenance", "Explicit plan_origin required")
    if plan["plan_origin"] in {"synthetic_fixture", "model_generated"}:
        _check(plan["provenance"] == "ai_synthetic", "invalid_provenance", "Synthetic/model plans must remain AI synthetic")
    if plan["plan_origin"] == "model_generated":
        _check(isinstance(plan.get("model_run_id"), str) and plan["model_run_id"], "invalid_provenance", "Model plan must cite its generating run ID")
    _check(isinstance(plan.get("text"), str) and 0 < len(plan["text"]) <= 20000, "invalid_plan", "Nonempty text of at most 20000 characters required")
    visual = plan.get("visual", {})
    video = plan.get("video", {})
    _check(isinstance(visual, dict) and isinstance(video, dict), "invalid_plan", "visual and video must be objects")
    _check(not set(visual) - {"style", "width", "height", "background_color", "foreground_color", "caption", "font_size"}, "invalid_plan", "Unsupported visual option")
    _check(not set(video) - {"duration_seconds", "fps"}, "invalid_plan", "Unsupported video option")
    style = visual.get("style", "caption_card")
    _check(style in STYLES, "unsupported_style", "Supported styles: caption_card and image_caption")
    width, height = visual.get("width", 960), visual.get("height", 540)
    _check(type(width) is int and type(height) is int and 256 <= width <= 1920 and 144 <= height <= 1080 and width % 2 == height % 2 == 0,
           "invalid_dimensions", "Even dimensions within 256..1920 by 144..1080 required")
    caption = visual.get("caption", plan["text"])
    _check(isinstance(caption, str) and 0 < len(caption) <= 1000, "invalid_caption", "Caption must contain 1..1000 characters")
    background, foreground = visual.get("background_color", "#142032"), visual.get("foreground_color", "#FFFFFF")
    _check(all(isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in (background, foreground)), "invalid_color", "Colors must be six-digit hex values")
    font_size = visual.get("font_size", max(20, min(52, width // 18)))
    _check(type(font_size) is int and 12 <= font_size <= 160, "invalid_font_size", "font_size must be 12..160")
    fps, seconds = video.get("fps", 12), video.get("duration_seconds", 3)
    _check(type(fps) is int and 1 <= fps <= 30, "invalid_video", "fps must be an integer from 1 to 30")
    _check(type(seconds) in {int, float} and math.isfinite(seconds) and .5 <= seconds <= 15, "invalid_video", "Video duration must be .5..15 seconds")
    _check(style != "image_caption" or bool(plan.get("source_image_path")), "source_image_required", "image_caption requires a local source image")
    _check(style != "caption_card" or not plan.get("source_image_path"), "unsupported_source_combination", "Use image_caption when providing a source image")
    return {**plan, "schema_version": "creation-plan-v1", "visual": {"style": style, "width": width, "height": height,
            "background_color": background, "foreground_color": foreground, "caption": caption, "font_size": font_size},
            "video": {"fps": fps, "duration_seconds": seconds, "frame_count": max(1, round(fps * seconds))}}


def _wrap(draw, caption, font, max_width):
    lines = []
    for paragraph in caption.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        line = ""
        for character in paragraph:
            candidate = line + character
            if line and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
                lines.append(line.rstrip())
                line = character.lstrip()
            else:
                line = candidate
        lines.append(line.rstrip())
    return lines


def _caption(image, caption, box, foreground, requested_size):
    draw = ImageDraw.Draw(image)
    left, top, width, height = box
    _check(FONT_PATH.is_file(), "font_unavailable", "Pinned Korean-capable font is unavailable")
    for size in range(requested_size, 11, -1):
        font = ImageFont.truetype(str(FONT_PATH), size=size)
        lines = _wrap(draw, caption, font, width)
        ascent, descent = font.getmetrics()
        line_height = ascent + descent + max(2, size // 6)
        if line_height * len(lines) <= height and all(draw.textbbox((0, 0), line, font=font)[2] <= width for line in lines):
            y = top + (height - line_height * len(lines)) // 2
            for line in lines:
                bounds = draw.textbbox((0, 0), line, font=font)
                x = left + (width - (bounds[2] - bounds[0])) // 2 - bounds[0]
                draw.text((x, y), line, font=font, fill=foreground)
                y += line_height
            return {"font_size": size, "line_count": len(lines), "font_path": str(FONT_PATH), "font_sha256": _sha(FONT_PATH)}
    raise RenderError("caption_layout_overflow", "Caption cannot fit without truncation at the supported minimum font size")


def _image(plan, project_root):
    settings = plan["visual"]
    width, height = settings["width"], settings["height"]
    canvas = Image.new("RGB", (width, height), settings["background_color"])
    padding = max(12, width // 30)
    sources = []
    if settings["style"] == "image_caption":
        source = _project_file(plan["source_image_path"], project_root)
        digest = _sha(source)
        try:
            with Image.open(source) as opened:
                _check(opened.width * opened.height <= 30_000_000, "source_image_too_large", "Source image exceeds decode size bound")
                opened.load()
                original = ImageOps.exif_transpose(opened).convert("RGBA")
        except RenderError:
            raise
        except Exception as exc:
            raise RenderError("source_image_decode_failed", "Source image could not be decoded") from exc
        panel_height = int(height * .63)
        fitted = ImageOps.contain(original, (width - padding * 2, panel_height - padding), method=Image.Resampling.LANCZOS)
        position = ((width - fitted.width) // 2, padding + (panel_height - padding - fitted.height) // 2)
        canvas.paste(fitted, position, fitted)
        caption_box = (padding, panel_height, width - 2 * padding, height - panel_height - padding)
        _check(_sha(source) == digest, "source_changed", "Source image changed while reading")
        sources.append({"kind": "source_image", "path": str(source), "sha256": digest, "decoded_size": list(original.size)})
    else:
        caption_box = (padding, padding, width - 2 * padding, height - 2 * padding)
    layout = _caption(canvas, settings["caption"], caption_box, settings["foreground_color"], settings["font_size"])
    return canvas, layout, sources


def _video(image, output, plan):
    fps, frame_count = plan["video"]["fps"], plan["video"]["frame_count"]
    frame = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    height, width = frame.shape[:2]
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    _check(writer.isOpened(), "video_encoder_unavailable", "OpenCV could not open the mp4v writer")
    backend = writer.getBackendName()
    try:
        for _ in range(frame_count):
            writer.write(frame)
    finally:
        writer.release()
    _check(output.is_file() and output.stat().st_size > 0, "video_write_failed", "No video bytes were produced")
    capture = cv2.VideoCapture(str(output))
    _check(capture.isOpened(), "video_decode_failed", "Produced MP4 could not be reopened")
    decoded_count, maximum_error = 0, 0.0
    decoded_fps = capture.get(cv2.CAP_PROP_FPS)
    try:
        while True:
            ok, decoded = capture.read()
            if not ok:
                break
            _check(decoded.shape == frame.shape, "video_dimension_mismatch", "Decoded video dimensions differ from the plan")
            difference = np.abs(decoded.astype(np.int16) - frame.astype(np.int16))
            maximum_error = max(maximum_error, float(difference.mean()))
            decoded_count += 1
    finally:
        capture.release()
    _check(decoded_count == frame_count and abs(decoded_fps - fps) < .05, "video_timing_mismatch", "Decoded frame count or fps differs from the plan")
    _check(maximum_error <= 16, "video_pixel_mismatch", "Decoded frames differ excessively from the composited image")
    return {"codec_requested": "mp4v", "writer_backend": backend, "decoded_frames": decoded_count, "decoded_fps": decoded_fps,
            "decoded_duration_seconds": decoded_count / decoded_fps, "decoded_size": [width, height],
            "maximum_frame_mean_absolute_pixel_error": maximum_error, "motion_style": "static_card", "audio_muxed": False}


def _wav(source, output):
    digest = _sha(source)
    try:
        with wave.open(str(source), "rb") as audio:
            channels, width, rate, count = audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()
            _check(audio.getcomptype() == "NONE", "unsupported_source_audio", "Only uncompressed PCM WAV preservation is supported")
            _check(1 <= channels <= 8 and 1 <= width <= 4 and 8000 <= rate <= 192000 and count > 0, "invalid_source_audio", "Unsupported or empty WAV parameters")
            _check(count / rate <= 600, "source_audio_too_long", "WAV preservation is bounded to ten minutes")
            data = audio.readframes(count)
            _check(len(data) == count * channels * width, "source_audio_decode_failed", "WAV data is truncated")
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError("source_audio_decode_failed", "Source audio must be a readable PCM WAV") from exc
    shutil.copyfile(source, output)
    _check(_sha(source) == digest and _sha(output) == digest, "source_changed", "WAV bytes changed during preservation")
    with wave.open(str(output), "rb") as audio:
        _check(audio.getnframes() == count and len(audio.readframes(count)) == len(data), "audio_copy_decode_failed", "Preserved WAV failed readback")
    return {"source_path": str(source), "source_sha256": digest, "channels": channels, "sample_width_bytes": width,
            "sample_rate": rate, "decoded_frames": count, "duration_seconds": count / rate, "copied_byte_identically": True, "muxed_into_video": False}


def render_plan(plan, output_dir, *, project_root=None):
    """Return a manifest; per-artifact failures are classified and preserved.

    The caller owns experiment registration. Existing output directories are
    rejected rather than overwritten; source files are never modified.
    """
    root = Path(project_root or ROOT).resolve()
    output = Path(output_dir).resolve()
    _check(output.is_relative_to(root), "output_outside_project", "Output must be within the project")
    _check(not output.exists(), "output_exists", "Choose a new output directory")
    output.mkdir(parents=True)
    result = {"schema_version": "creation-artifacts-v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "status": "started", "renderer": "fixed_local_compositor_v1", "artifacts": {}, "source_assets": [],
              "renderer_versions": {"Pillow": PIL_VERSION, "OpenCV": cv2.__version__, "NumPy": np.__version__},
              "external_generation_api_calls": 0, "model_weights_trained_by_renderer": False,
              "supported_styles": sorted(STYLES), "audio_mux_supported": False,
              "scope": "UTF-8 body, composed card image, silent static-card video, optional byte-preserved PCM WAV; not general animation or native generative media synthesis"}
    try:
        normalized = _normalize(plan)
        result.update(plan_id=normalized["plan_id"], provenance=normalized["provenance"], plan_origin=normalized["plan_origin"],
                      declared_model_run_id=normalized.get("model_run_id"), model_run_verified_by_renderer=False,
                      original_plan_sha256=hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest())
        plan_path = output / "plan.json"
        plan_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        text_path = output / "body.txt"
        text_path.write_bytes(normalized["text"].encode("utf-8"))
        _check(text_path.read_bytes().decode("utf-8") == normalized["text"], "text_readback_failed", "UTF-8 body differs from the supplied plan")
        result["artifacts"]["text"] = {"path": "body.txt", "sha256": _sha(text_path), "mime": "text/plain; charset=utf-8", "verified": True}
        image, layout, sources = _image(normalized, root)
        result["source_assets"].extend(sources)
        image_path = output / "image.png"
        image.save(image_path, format="PNG", compress_level=9)
        with Image.open(image_path) as decoded:
            decoded.load()
            _check(decoded.format == "PNG" and decoded.size == image.size and ImageChops.difference(decoded.convert("RGB"), image).getbbox() is None,
                   "image_readback_failed", "Produced PNG pixels failed readback")
        result["artifacts"]["image"] = {"path": "image.png", "sha256": _sha(image_path), "mime": "image/png", "verified": True, "decoded_size": list(image.size), "layout": layout}
        video_path = output / "video.mp4"
        video_details = _video(image, video_path, normalized)
        result["artifacts"]["video"] = {"path": "video.mp4", "sha256": _sha(video_path), "mime": "video/mp4", "verified": True, **video_details}
        if normalized.get("source_audio_path"):
            source_audio = _project_file(normalized["source_audio_path"], root)
            audio_path = output / "source_audio.wav"
            audio_details = _wav(source_audio, audio_path)
            result["artifacts"]["audio"] = {"path": "source_audio.wav", "sha256": _sha(audio_path), "mime": "audio/wav", "verified": True, **audio_details}
            result["source_assets"].append({"kind": "source_audio", "path": str(source_audio), "sha256": audio_details["source_sha256"]})
        result["status"] = "succeeded"
        result["error"] = None
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = {"code": exc.code if isinstance(exc, RenderError) else "unexpected_renderer_error",
                           "message": str(exc) if isinstance(exc, RenderError) else type(exc).__name__}
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


render = render_plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
        result = render_plan(plan, args.output_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "succeeded" else 1
    except (RenderError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "code": exc.code if isinstance(exc, RenderError) else type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
