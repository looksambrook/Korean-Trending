"""AI_SYNTHETIC offline collector checks; these fixtures are not YouTube evidence."""
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from xml.etree.ElementTree import ParseError
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import youtube_feed_collect as yf
from research_log import DuplicateRunError, ResearchLog


CHANNEL = "UC" + "a" * 22
OTHER_CHANNEL = "UC" + "b" * 22
VIDEO = "testvideo01"
RECEIVED = "2026-09-15T03:00:00+00:00"
PUBLISHED = "2026-09-14T10:00:00+00:00"
UPDATED = "2026-09-14T11:00:00+00:00"


def synthetic_entry(video_id=VIDEO, channel_id=CHANNEL, published=PUBLISHED,
                    updated=UPDATED, title="AI_SYNTHETIC 테스트 제목", description="AI_SYNTHETIC 설명"):
    def element(name, value):
        return "" if value is None else f"<{name}>{escape(value)}</{name}>"
    return (
        "<entry>" + element("id", "yt:video:" + video_id)
        + element("yt:videoId", video_id) + element("yt:channelId", channel_id)
        + element("title", title)
        + f'<link rel="alternate" href="https://www.youtube.com/watch?v={escape(video_id)}"/>'
        + "<author><name>AI_SYNTHETIC 테스트 채널</name>"
        + element("uri", "https://www.youtube.com/channel/" + channel_id) + "</author>"
        + element("published", published) + element("updated", updated)
        + "<media:group>" + element("media:title", title)
        + element("media:description", description)
        + '<media:community><media:statistics views="123"/></media:community>'
        + "</media:group></entry>"
    )


def synthetic_feed(*entries, channel_id=CHANNEL):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
        'xmlns:media="http://search.yahoo.com/mrss/">'
        f"<id>yt:channel:{channel_id}</id><yt:channelId>{channel_id}</yt:channelId>"
        "<title>AI_SYNTHETIC 테스트 채널</title>"
        f'<link rel="self" href="https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"/>'
        + "".join(entries) + "</feed>"
    ).encode("utf-8")


def config():
    return {
        "snapshot_id": "TEST_ONLY_ai_synthetic_snapshot",
        "channels": [{"channel_id": CHANNEL, "label": "AI_SYNTHETIC 테스트 채널",
                      "source_url": "https://www.youtube.com/channel/" + CHANNEL}],
        "max_entries_per_channel": 20, "timeout_seconds": 15,
        "max_response_bytes": 2000000, "retention_days": 30,
    }


def fixed_now():
    return datetime.fromisoformat(RECEIVED)


class FeedParserTest(unittest.TestCase):
    def parse(self, body, **kwargs):
        return yf.parse_feed(body, CHANNEL, RECEIVED, provenance="ai_synthetic", **kwargs)

    def test_valid_metadata_keeps_korean_unicode_and_explicit_provenance(self):
        result = self.parse(synthetic_feed(synthetic_entry(title="AI_SYNTHETIC 한글 & <문맥>")))
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["exclusions"], [])
        self.assertEqual(result["feed"]["entry_count"], 1)
        row = result["observations"][0]
        self.assertEqual(row["channel_id"], CHANNEL)
        self.assertEqual(row["video_id"], VIDEO)
        self.assertEqual(row["context"]["title"], "AI_SYNTHETIC 한글 & <문맥>")
        self.assertEqual(row["context"]["description"], "AI_SYNTHETIC 설명")
        self.assertEqual(row["provenance"], "ai_synthetic")
        self.assertEqual(datetime.fromisoformat(row["posted_at"].replace("Z", "+00:00")),
                         datetime.fromisoformat(PUBLISHED))
        self.assertEqual(datetime.fromisoformat(row["collected_at"].replace("Z", "+00:00")), fixed_now())
        self.assertEqual(datetime.fromisoformat(row["available_at"].replace("Z", "+00:00")), fixed_now())
        self.assertEqual(row["modality"], "video_metadata")
        self.assertEqual(row["source_url"], "https://www.youtube.com/watch?v=" + VIDEO)
        due = datetime.fromisoformat(row["retention_due_at"].replace("Z", "+00:00"))
        self.assertEqual((due - fixed_now()).days, 30)

    def test_empty_feed_is_empty_observation_not_successful_meme_detection(self):
        result = self.parse(synthetic_feed())
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["feed"]["entry_count"], 0)

    def test_invalid_xml_and_non_atom_root_rejected(self):
        for body in (b"<feed>", b"<html><body>AI_SYNTHETIC consent page</body></html>", b"{}"):
            with self.subTest(body=body), self.assertRaises((ValueError, ParseError)):
                self.parse(body)

    def test_dtd_and_entities_rejected_without_expansion(self):
        body = synthetic_feed(synthetic_entry()).replace(
            b'<?xml version="1.0" encoding="UTF-8"?>',
            b'<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE feed [<!ENTITY demo "AI_SYNTHETIC">]>',
        )
        with self.assertRaises(ValueError):
            self.parse(body)

    def test_feed_channel_mismatch_cannot_import_another_channel(self):
        with self.assertRaises(ValueError):
            self.parse(synthetic_feed(synthetic_entry(channel_id=OTHER_CHANNEL), channel_id=OTHER_CHANNEL))

    def test_duplicate_video_ids_have_one_observation_and_recorded_exclusion(self):
        item = synthetic_entry()
        result = self.parse(synthetic_feed(item, item))
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(len(result["exclusions"]), 1)
        self.assertEqual(result["feed"]["entry_count"], 2)

    def test_bad_entry_ids_are_excluded_and_later_valid_entry_survives(self):
        for bad in (synthetic_entry(video_id="bad"), synthetic_entry(channel_id=OTHER_CHANNEL),
                    synthetic_entry(channel_id="not_a_channel")):
            with self.subTest(bad=bad):
                result = self.parse(synthetic_feed(bad, synthetic_entry()))
                self.assertEqual(len(result["observations"]), 1)
                self.assertEqual(result["observations"][0]["video_id"], VIDEO)
                self.assertEqual(len(result["exclusions"]), 1)

    def test_missing_invalid_naive_and_future_dates_do_not_become_evidence(self):
        variations = [
            {"published": None}, {"updated": None}, {"published": "not_a_timestamp"},
            {"published": "2026-09-14T10:00:00"},
            {"published": "2026-09-16T00:00:00Z", "updated": "2026-09-16T01:00:00Z"},
            {"updated": "2026-09-16T00:00:00Z"},
        ]
        for variation in variations:
            with self.subTest(variation=variation):
                result = self.parse(synthetic_feed(synthetic_entry(**variation)))
                self.assertEqual(result["observations"], [])
                self.assertEqual(len(result["exclusions"]), 1)

    def test_received_time_requires_timezone(self):
        with self.assertRaises(ValueError):
            yf.parse_feed(synthetic_feed(synthetic_entry()), CHANNEL, "2026-09-15T03:00:00",
                          provenance="ai_synthetic")

    def test_entry_cap_limits_observations_without_altering_reported_feed_count(self):
        body = synthetic_feed(synthetic_entry(video_id="testvideo01"), synthetic_entry(video_id="testvideo02"))
        result = self.parse(body, max_entries=1)
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["feed"]["entry_count"], 2)


class FeedCollectorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="TEST_ONLY_youtube_feed_")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db = self.base / "research.sqlite3"
        self.calls = []

    def transport(self, url, timeout, max_bytes):
        self.calls.append((url, timeout, max_bytes))
        channel_id = parse_qs(urlparse(url).query)["channel_id"][0]
        return {"body": synthetic_feed(synthetic_entry(channel_id=channel_id), channel_id=channel_id),
                "status": 200, "content_type": "application/atom+xml"}

    def collect(self, cfg=None, output="first", transport=None):
        return yf.collect(config() if cfg is None else cfg, output_dir=self.base / output,
                          logdb=self.db, transport=transport or self.transport, now=fixed_now)

    def test_injected_transport_produces_synthetic_records_and_terminal_log(self):
        manifest = self.collect()
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["counts"]["observations"], 1)
        self.assertEqual(manifest["counts"]["channels_ok"], 1)
        self.assertEqual(manifest["counts"]["channels_failed"], 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1:], (15, 2000000))
        self.assertEqual(parse_qs(urlparse(self.calls[0][0]).query)["channel_id"], [CHANNEL])
        run = ResearchLog(self.db).status(manifest["run_id"])
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["spec"]["provenance"], "ai_synthetic")
        text_files = [p for p in (self.base / "first").rglob("*") if p.suffix in (".json", ".jsonl")]
        self.assertTrue(text_files)
        serialized = "\n".join(p.read_text(encoding="utf-8") for p in text_files)
        self.assertIn("ai_synthetic", serialized)
        self.assertNotIn('"provenance": "actual_collected"', serialized)

    def test_duplicate_guard_checks_registry_before_second_network_call(self):
        first = self.collect()
        with self.assertRaises(DuplicateRunError):
            self.collect(output="second")
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.base / "second").exists())
        self.assertEqual(len(ResearchLog(self.db).list()), 1)
        self.assertEqual(ResearchLog(self.db).status(first["run_id"])["status"], "succeeded")

    def test_existing_output_contents_are_preserved_without_network(self):
        out = self.base / "first"
        out.mkdir()
        marker = out / "user_data.txt"
        marker.write_bytes(b"TEST_ONLY_user_existing_content")
        with self.assertRaises((FileExistsError, ValueError)):
            self.collect()
        self.assertEqual(marker.read_bytes(), b"TEST_ONLY_user_existing_content")
        self.assertEqual(self.calls, [])
        self.assertEqual(list(out.iterdir()), [marker])

    def test_one_http_failure_does_not_erase_other_channel_data_or_claim_full_success(self):
        cfg = config()
        cfg["channels"].append({"channel_id": OTHER_CHANNEL, "label": "AI_SYNTHETIC second channel",
                               "source_url": "https://www.youtube.com/channel/" + OTHER_CHANNEL})

        def partly_failing(url, timeout, max_bytes):
            if OTHER_CHANNEL in url:
                self.calls.append((url, timeout, max_bytes))
                raise HTTPError(url, 503, "AI_SYNTHETIC simulated failure", {}, None)
            return self.transport(url, timeout, max_bytes)

        manifest = self.collect(cfg, transport=partly_failing)
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["counts"]["channels_ok"], 1)
        self.assertEqual(manifest["counts"]["channels_failed"], 1)
        self.assertEqual(manifest["counts"]["observations"], 1)
        self.assertEqual(len(self.calls), 2)
        run = ResearchLog(self.db).status(manifest["run_id"])
        self.assertEqual(run["status"], "failed")
        serialized = "\n".join(p.read_text(encoding="utf-8") for p in (self.base / "first").rglob("*")
                               if p.suffix in (".json", ".jsonl"))
        self.assertIn(VIDEO, serialized)
        self.assertIn("503", serialized)


if __name__ == "__main__":
    unittest.main()
