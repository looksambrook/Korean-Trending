"""AI fixture plans/media exercise the compositor, not meme/model quality."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import wave

import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from creation_artifact_renderer import ROOT, RenderError, render_plan


class CreationArtifactRendererTests(unittest.TestCase):
    def test_real_text_png_mp4_and_wav_decode_with_unchanged_sources(self):
        with tempfile.TemporaryDirectory(prefix="renderer-fixture-", dir=ROOT / "data") as directory:
            base = Path(directory)
            source_image = base / "synthetic_graphic.png"
            graphic = Image.new("RGB", (160, 90), "#2266CC")
            ImageDraw.Draw(graphic).rectangle((20, 15, 75, 65), fill="#FFAA33")
            graphic.save(source_image)
            source_audio = base / "synthetic_silence.wav"
            with wave.open(str(source_audio), "wb") as audio:
                audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\0\0" * 4000)
            original_image = source_image.read_bytes()
            original_audio = source_audio.read_bytes()
            plan = {"schema_version": "creation-plan-v1", "plan_id": "synthetic-test-only", "provenance": "ai_synthetic",
                    "plan_origin": "synthetic_fixture", "text": "연구용 합성 제작기 시험\n실제 밈·모델 결과가 아닙니다.",
                    "visual": {"style": "image_caption", "width": 384, "height": 216, "caption": "합성 자료로 제작기를 확인합니다", "font_size": 22},
                    "video": {"duration_seconds": 1, "fps": 6},
                    "source_image_path": str(source_image.relative_to(ROOT)), "source_audio_path": str(source_audio.relative_to(ROOT))}
            result = render_plan(plan, base / "output")
            self.assertEqual(result["status"], "succeeded", result.get("error"))
            self.assertEqual((base / "output/body.txt").read_text(encoding="utf-8"), plan["text"])
            with Image.open(base / "output/image.png") as image:
                self.assertEqual(image.size, (384, 216))
                self.assertGreater(float(np.asarray(image).std()), 20)
            video = cv2.VideoCapture(str(base / "output/video.mp4"))
            decoded_frames = 0
            while True:
                ok, frame = video.read()
                if not ok:
                    break
                self.assertEqual(frame.shape, (216, 384, 3))
                decoded_frames += 1
            video.release()
            self.assertEqual(decoded_frames, 6)
            self.assertFalse(result["artifacts"]["video"]["audio_muxed"])
            self.assertTrue(result["artifacts"]["audio"]["copied_byte_identically"])
            self.assertEqual(source_image.read_bytes(), original_image)
            self.assertEqual(source_audio.read_bytes(), original_audio)
            self.assertEqual((base / "output/source_audio.wav").read_bytes(), original_audio)
            again = render_plan(plan, base / "repeat")
            self.assertEqual(again["status"], "succeeded", again.get("error"))
            self.assertEqual(again["artifacts"]["image"]["sha256"], result["artifacts"]["image"]["sha256"])

    def test_unsupported_plan_is_classified_and_existing_outputs_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix="renderer-errors-", dir=ROOT / "data") as directory:
            base = Path(directory)
            plan = {"plan_id": "synthetic-invalid", "provenance": "ai_synthetic", "plan_origin": "synthetic_fixture",
                    "text": "synthetic fixture", "visual": {"style": "invented_dynamic_dance"}}
            result = render_plan(plan, base / "failed")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"]["code"], "unsupported_style")
            before = (base / "failed/manifest.json").read_bytes()
            with self.assertRaises(RenderError) as error:
                render_plan(plan, base / "failed")
            self.assertEqual(error.exception.code, "output_exists")
            self.assertEqual((base / "failed/manifest.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
