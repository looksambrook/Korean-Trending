"""TEST_ONLY synthetic watch HTML; no videos or real metadata are fetched."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from research_log import DuplicateRunError, ResearchLog
from youtube_video_metadata import MetadataError, collect, parse_watch_metadata

VID = "a" * 11
CID = "UC" + "a" * 22
NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)


def html(published="2026-09-13"):
    player = {"videoDetails": {"videoId": VID, "title": "TEST_ONLY title", "shortDescription": "TEST_ONLY description",
              "channelId": CID, "author": "TEST_ONLY channel"},
              "microformat": {"playerMicroformatRenderer": {"publishDate": published, "uploadDate": "2026-09-12", "category": "Comedy"}}}
    return ('<script>var ytInitialPlayerResponse = ' + json.dumps(player) + ';</script>').encode()


class WatchMetadataTest(unittest.TestCase):
    def test_metadata_fields_date_only_and_exact_time_are_distinct(self):
        row = parse_watch_metadata(html(), VID, NOW, provenance="ai_synthetic")
        self.assertEqual((row["title"], row["channel_id"], row["author"]), ("TEST_ONLY title", CID, "TEST_ONLY channel"))
        self.assertEqual(row["description"], "TEST_ONLY description")
        self.assertIsNone(row["posted_at"])
        self.assertEqual((row["posted_date"], row["posted_precision"]), ("2026-09-13", "date"))
        self.assertFalse(row["temporal_eligibility"])
        self.assertFalse(row["discovery_evaluation_eligible"])
        exact = parse_watch_metadata(html("2026-09-13T10:00:00+09:00"), VID, NOW)
        self.assertEqual(exact["posted_at"], "2026-09-13T01:00:00+00:00")
        self.assertEqual(exact["available_at"], NOW.isoformat())

    def test_jsonld_needs_target_identity_and_upload_date_is_not_publication_time(self):
        obj = {"@type": "VideoObject", "name": "TEST_ONLY ld title", "description": "TEST_ONLY ld description",
               "embedUrl": "https://www.youtube.com/embed/" + VID, "uploadDate": "2026-09-11"}
        body = '<script type="application/ld+json">' + json.dumps(obj) + '</script>'
        row = parse_watch_metadata(body, VID, NOW)
        self.assertIsNone(row["posted_at"])
        self.assertEqual(row["uploaded_date"], "2026-09-11")
        self.assertIsNone(row["channel_id"])
        with self.assertRaises(MetadataError):
            parse_watch_metadata(body, "b" * 11, NOW)

    def test_challenges_and_wrong_video_fail_without_making_up_metadata(self):
        for body, reason in [(b'<form action="https://consent.youtube.com/save">', "consent_required"),
                             (b'Our systems have detected unusual traffic', "captcha_or_traffic_block"),
                             (b'<title>Sign in - Google Accounts</title>', "login_required")]:
            with self.subTest(reason=reason), self.assertRaises(MetadataError) as caught:
                parse_watch_metadata(body, VID, NOW)
            self.assertEqual(caught.exception.reason, reason)
        with self.assertRaises(MetadataError) as caught:
            parse_watch_metadata(html(), "b" * 11, NOW)
        self.assertEqual(caught.exception.reason, "video_identity_mismatch")

    def test_logged_execution_source_link_and_duplicate_prevention(self):
        with tempfile.TemporaryDirectory(prefix="TEST_ONLY_watch_") as tmp:
            base = Path(tmp)
            manifest = base / "source_manifest.json"
            manifest.write_text(json.dumps({"run_id": "TEST_ONLY_source_run"}), encoding="utf-8")
            source = base / "source.jsonl"
            source.write_text(json.dumps({"video_id": VID, "observation_id": "TEST_ONLY_search_observation", "provenance": "ai_synthetic",
                                          "available_at": "2026-09-14T00:00:00Z"}) + "\n", encoding="utf-8")
            cfg = {"mode": "posthoc_search_expansion", "video_ids": [VID], "source_run_id": "TEST_ONLY_source_run",
                   "source_manifest": str(manifest), "source_observations": str(source), "selection_reason": "TEST_ONLY fixture"}
            db = base / "TEST_ONLY.sqlite3"
            calls = []
            def transport(url, timeout, max_bytes):
                self.assertEqual(ResearchLog(db).list()[-1]["status"], "started")
                calls.append(url)
                return html()
            kwargs = {"output_dir": base / "output", "logdb": db, "transport": transport, "test_only": True, "now": lambda: NOW}
            result = collect(cfg, **kwargs)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["http_attempts"], 1)
            self.assertEqual(result["channel_ids_available"], 1)
            rows = next(item["path"] for item in result["artifacts"] if item["path"].endswith("observations.jsonl"))
            row = json.loads(Path(rows).read_text(encoding="utf-8"))
            self.assertEqual(row["source_observation_ids"], ["TEST_ONLY_search_observation"])
            self.assertEqual(row["provenance"], "ai_synthetic")
            with self.assertRaises(DuplicateRunError):
                collect(cfg, **kwargs)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
