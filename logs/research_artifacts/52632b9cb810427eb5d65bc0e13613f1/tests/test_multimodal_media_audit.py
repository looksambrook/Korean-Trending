"""AI-authored synthetic media fixtures; never actual collected meme data."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import multimodal_media_audit as audit
from research_log import DuplicateRunError, ResearchLog, hash_file


class MediaAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="TEST_ONLY_SYNTHETIC_media_")
        self.base = Path(self.temporary.name)
        self.db = self.base / "research.sqlite3"
        self.input = self.base / "assets.jsonl"

    def tearDown(self):
        self.temporary.cleanup()

    def asset(self, path, modality, expected=True):
        return {"asset_id": "TEST_ONLY_" + path.name, "local_path": str(path), "modality": modality,
                "sha256": hash_file(path) if expected else None, "provenance": "ai_synthetic"}

    def run_audit(self, rows, name="audit", **kwargs):
        self.input.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf8")
        return audit.audit(self.input, output_dir=self.base/name, db=self.db,
                           asset_root=self.base, test_only=True, **kwargs)

    def test_actual_utf8_image_and_pcm_samples_decode_without_source_changes(self):
        from PIL import Image
        text = self.base / "fixture.txt"
        text.write_text("TEST_ONLY 합성 자료\n읽기 검사", encoding="utf8")
        image = self.base / "fixture.png"
        Image.new("RGB", (8, 5), (20, 40, 60)).save(image)
        sound = self.base / "fixture.wav"
        with wave.open(str(sound), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\0\0" * 800)
        rows = [self.asset(text,"text"), self.asset(image,"image"), self.asset(sound,"audio")]
        result = self.run_audit([{"assets": rows}])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["readability_verified_assets"], 3)
        for row in result["results"]:
            self.assertEqual(row["status"], "decoded_full")
            self.assertTrue(row["source_bytes_unchanged"])
            self.assertEqual(row["expected_sha256"], hash_file(row["resolved_local_path"]))
        audio = result["results"][2]
        self.assertEqual(audio["audio_stream_status"], "present")
        self.assertTrue(audio["decoded_samples_exact_digital_silence"])
        self.assertAlmostEqual(audio["decoded_duration_seconds"], .1)

    def test_false_hash_and_corrupt_image_cannot_be_read_success(self):
        source = self.base / "wrong.png"
        source.write_bytes(b"TEST_ONLY_not_png_pixels")
        wrong = self.asset(source,"image")
        wrong["sha256"] = "0"*64
        result = self.run_audit([wrong, self.asset(source,"image")])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["results"][0]["status"], "hash_mismatch")
        self.assertEqual(result["results"][1]["status"], "decode_failed")
        self.assertEqual(result["readability_verified_assets"], 0)

    def test_animation_first_frame_does_not_claim_full_decode(self):
        from PIL import Image
        path = self.base / "TEST_ONLY_animation.gif"
        frames = [Image.new("RGB", (8, 5), color) for color in ("red", "green", "blue")]
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
        result = self.run_audit([self.asset(path, "image")], limits={"max_image_frames": 1})
        row = result["results"][0]
        self.assertEqual(row["status"], "decoded_sample")
        self.assertEqual(row["frame_count_declared"], 3)
        self.assertEqual(row["decoded_frame_indices"], [0])
        self.assertEqual(row["coverage"], "selected_image_frames")
        self.assertTrue(row["source_bytes_unchanged"])

    def test_unsupported_audio_and_duplicate_failure_stay_unavailable(self):
        source = self.base / "fake.mp3"
        source.write_bytes(b"TEST_ONLY_unsupported_audio_fixture")
        item = self.asset(source,"audio")
        result = self.run_audit([item, item])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["results"][0]["status"], "decoder_unavailable")
        self.assertEqual(result["results"][1]["status"], "duplicate_of_unreadable_asset")
        self.assertNotIn("duration_seconds_declared", result["results"][0])

    def test_byte_and_root_guards_skip_before_decode(self):
        source = self.base / "large.txt"
        source.write_bytes(b"x"*20)
        result = self.run_audit([self.asset(source,"text")], limits={"max_file_bytes": 10})
        self.assertEqual(result["results"][0]["status"], "skipped_limit")
        self.assertNotIn("measured_sha256", result["results"][0])
        foreign = self.asset(ROOT / "README.md", "text")
        result = self.run_audit([foreign], name="outside")
        self.assertEqual(result["results"][0]["reason"], "asset_outside_declared_root")

    def test_video_pixels_decode_but_audio_absence_is_never_inferred(self):
        import cv2
        import numpy as np
        path = self.base / "TEST_ONLY_video.avi"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5, (32, 24))
        if not writer.isOpened():
            self.skipTest("Installed OpenCV cannot encode the synthetic MJPG fixture")
        for value in range(10):
            writer.write(np.full((24,32,3), value*10, dtype=np.uint8))
        writer.release()
        result = self.run_audit([self.asset(path,"video")], limits={"max_video_frames": 3})
        row = result["results"][0]
        self.assertEqual(row["status"], "decoded_sample")
        self.assertEqual(len(row["decoded_frames"]), 3)
        self.assertEqual(row["coverage"], "selected_video_frames")
        self.assertEqual(row["audio_stream_status"], "uninspected")
        self.assertFalse(row["silent_video_confirmed"])
        self.assertTrue(row["source_bytes_unchanged"])

    def test_duplicate_runs_require_explicit_linked_retry(self):
        path=self.base/"text.txt"
        path.write_text("TEST_ONLY",encoding="utf8")
        rows=[self.asset(path,"text")]
        first=self.run_audit(rows)
        with self.assertRaises(DuplicateRunError):
            self.run_audit(rows,name="duplicate")
        self.assertFalse((self.base/"duplicate").exists())
        retried=self.run_audit(rows,name="retry",retry_of=first["run_id"],retry_reason="TEST_ONLY explicit linked retry")
        self.assertEqual(retried["status"],"completed")
        self.assertEqual(ResearchLog(self.db).status(retried["run_id"])["retry_of"],first["run_id"])

    def test_asset_byte_change_is_new_condition_and_empty_input_fails(self):
        path = self.base / "mutable.txt"
        path.write_text("TEST_ONLY_before", encoding="utf8")
        rows = [self.asset(path, "text", expected=False)]
        first = self.run_audit(rows)
        path.write_text("TEST_ONLY_after", encoding="utf8")
        second = self.run_audit(rows, name="changed")
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(second["status"], "completed")
        self.assertNotEqual(first["registered_asset_snapshots"], second["registered_asset_snapshots"])
        self.assertEqual(second["registered_asset_snapshots"][0]["sha256"], hash_file(path))
        empty = self.run_audit([], name="empty")
        self.assertEqual(empty["status"], "failed")
        self.assertIn({"reason": "empty_input"}, empty["issues"])


if __name__ == "__main__":
    unittest.main()
