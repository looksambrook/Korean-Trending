"""AI-authored XML fixtures; neither real trends nor evidence of meme accuracy."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import google_trends_collect as gt
from research_log import DuplicateRunError, ResearchLog

RECEIVED = "2026-09-15T05:00:00Z"
ITEM = """<item><title>합성 검색 주제</title><link>https://trends.google.com/trending?geo=KR</link>
<pubDate>Tue, 15 Sep 2026 10:00:00 +0900</pubDate><ht:approx_traffic>1천+</ht:approx_traffic>
<description>합성 메타데이터</description><ht:news_item><ht:news_item_title>합성 기사 제목</ht:news_item_title>
<ht:news_item_url>https://example.test/article</ht:news_item_url><ht:news_item_source>합성 출처</ht:news_item_source>
<ht:news_item_snippet>본문을 읽은 것이 아닌 RSS 합성 요약</ht:news_item_snippet></ht:news_item></item>"""


def xml(items=ITEM):
    return ('<rss version="2.0" xmlns:ht="https://trends.google.com/trending/rss"><channel>'
            '<title>AI synthetic fixture</title>' + items + '</channel></rss>').encode("utf-8")


def config():
    return {"snapshot_id": "synthetic_mechanics_test", "geo": "KR", "max_requests": 1,
            "timeout_seconds": 15, "max_response_bytes": 2097152, "max_entries": 200}


class TrendsRSSCollectionTest(unittest.TestCase):
    def test_parser_preserves_bucket_news_metadata_and_real_availability(self):
        result = gt.parse_feed(xml(), RECEIVED, provenance="ai_synthetic")
        self.assertEqual(len(result["observations"]), 1)
        row = result["observations"][0]
        self.assertEqual(row["platform"], "google_trends")
        self.assertEqual(row["approx_traffic_raw"], "1천+")
        self.assertIsNone(row["approx_traffic_numeric"])
        self.assertEqual(row["posted_at"], "2026-09-15T01:00:00+00:00")
        self.assertEqual(row["available_at"], "2026-09-15T05:00:00+00:00")
        self.assertFalse(row["verified_meme"])
        self.assertEqual(row["provenance"], "ai_synthetic")
        self.assertEqual(row["context"]["news_items_metadata"][0]["url"], "https://example.test/article")
        self.assertFalse(row["context"]["news_article_bodies_read"])

    def test_future_or_timezone_missing_dates_duplicates_and_dtd_are_rejected(self):
        missing = ITEM.replace(" +0900", "").replace("합성 검색 주제", "날짜 미상")
        future = ITEM.replace("15 Sep", "16 Sep").replace("합성 검색 주제", "미래 자료")
        result = gt.parse_feed(xml(ITEM + ITEM + missing + future), RECEIVED, provenance="ai_synthetic")
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual({row["reason"] for row in result["exclusions"]},
                         {"duplicate_feed_item", "missing_or_invalid_timezone_pubDate", "future_pubDate"})
        with self.assertRaises(ValueError):
            gt.parse_feed(b'<!DOCTYPE rss><rss><channel/></rss>', RECEIVED)
        with self.assertRaises(ValueError):
            gt.validate_config({**config(), "geo": "US"})
        with self.assertRaises(ValueError):
            gt.validate_config({**config(), "max_requests": 2})

    @patch.object(gt, "_now", return_value=RECEIVED)
    def test_fixture_is_labelled_synthetic_and_duplicate_blocks_before_any_output(self, _):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "config.json"
            cfg.write_text(json.dumps(config()), encoding="utf-8")
            with patch.object(gt, "http_get", side_effect=AssertionError("No network for fixture")):
                manifest = gt.run(cfg, root / "out", db=root / "log.sqlite3", fixture_body=xml())
                self.assertEqual(manifest["provenance"], "ai_synthetic")
                self.assertEqual(manifest["http_requests_attempted"], 0)
                self.assertEqual(manifest["observation_count"], 1)
                self.assertEqual((root / "out/raw.xml").read_bytes(), xml())
                entry = ResearchLog(root / "log.sqlite3").status(manifest["run_id"])
                self.assertEqual(entry["status"], "succeeded")
                self.assertEqual(len(entry["finish"]["artifacts"]), 4)
                with self.assertRaises(DuplicateRunError):
                    gt.run(cfg, root / "out2", db=root / "log.sqlite3", fixture_body=xml())
                self.assertFalse((root / "out2").exists())

    def test_failed_request_was_logged_before_http_and_never_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "config.json"
            cfg.write_text(json.dumps(config()), encoding="utf-8")
            calls = []
            def fail_transport(url, timeout, maximum):
                calls.append(url)
                self.assertEqual(url, gt.ENDPOINT)
                self.assertEqual(ResearchLog(root / "log.sqlite3").list()[0]["status"], "started")
                raise urllib.error.URLError("synthetic network failure")
            with patch.object(gt, "http_get", side_effect=fail_transport):
                with self.assertRaises(urllib.error.URLError):
                    gt.run(cfg, root / "out", db=root / "log.sqlite3")
            self.assertEqual(len(calls), 1)
            entry = ResearchLog(root / "log.sqlite3").list()[0]
            self.assertEqual(entry["status"], "failed")
            manifest = json.loads((root / "out/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["http_requests_attempted"], 1)
            self.assertFalse((root / "out/observations.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
