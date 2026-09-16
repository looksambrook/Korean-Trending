"""TEST_ONLY public-search HTML fixtures. No real API/HTML evidence is created."""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from research_log import DuplicateRunError, ResearchLog, hash_file
from youtube_web_search import SearchError, collect, fetch_public_page, parse_search_page, validate_config

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)
VID = "a" * 11
VID2 = "b" * 11
VID3 = "c" * 11
CID = "UC" + "a" * 22
URL = "https://www.youtube.com/results?search_query=TEST_ONLY"


def card(vid=VID, title="TEST_ONLY synthetic video title"):
    return {"videoRenderer": {"videoId": vid, "title": {"runs": [{"text": title}]},
            "descriptionSnippet": {"runs": [{"text": "TEST_ONLY synthetic snippet"}]},
            "publishedTimeText": {"simpleText": "3 days ago"},
            "ownerText": {"runs": [{"text": "TEST_ONLY synthetic channel",
                 "navigationEndpoint": {"browseEndpoint": {"browseId": CID}}}]}}}


def html(cards=None, declaration="var ytInitialData = "):
    data = {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {
          "sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": cards if cards is not None else [card()]}}]}}}}}
    return ("<html><body><script>" + declaration + json.dumps(data) + ";</script></body></html>").encode()


def cfg(**changes):
    return {"search_mode": "topic_query", "queries": [{"query": "TEST_ONLY synthetic topic"}],
            "selection_reason": "TEST_ONLY fixture", **changes}


class SearchParserTest(unittest.TestCase):
    def parse(self, body, **kwargs):
        return parse_search_page(body, query="TEST_ONLY", source_search_url=URL, received_at=NOW,
                                 provenance="ai_synthetic", **kwargs)

    def test_video_renderer_exact_text_channel_and_unknown_publication_time(self):
        rows, page = self.parse(html())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["video_id"], VID)
        self.assertEqual(row["channel_id"], CID)
        self.assertEqual(row["title"], "TEST_ONLY synthetic video title")
        self.assertEqual(row["description_snippet"], "TEST_ONLY synthetic snippet")
        self.assertIsNone(row["posted_at"])
        self.assertEqual(row["publication_label"], "3 days ago")
        self.assertFalse(row["temporal_eligibility"])
        self.assertEqual(row["available_at"], NOW.isoformat())
        self.assertEqual(row["source_search_url"], URL)
        self.assertFalse(page["pagination_attempted"])

    def test_window_initial_data_and_modern_and_legacy_shorts(self):
        cards = [{"reelItemRenderer": {"videoId": VID2, "headline": {"simpleText": "TEST_ONLY legacy short"}}},
                 {"shortsLockupViewModel": {"onTap": {"innertubeCommand": {"reelWatchEndpoint": {"videoId": VID3}}},
                     "overlayMetadata": {"primaryText": {"content": "TEST_ONLY modern short"}}}}]
        rows, _ = self.parse(html(cards, 'window["ytInitialData"] = '))
        self.assertEqual([row["video_id"] for row in rows], [VID2, VID3])
        self.assertEqual(rows[1]["title"], "TEST_ONLY modern short")
        self.assertTrue(all(row["channel_id"] is None for row in rows))

    def test_ads_other_renderers_and_duplicate_cards_do_not_become_new_documents(self):
        rows, page = self.parse(html([{"adSlotRenderer": card(VID2)}, card(), card(), {"channelRenderer": {"channelId": CID}}]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(page["exclusions"][0]["reason"], "duplicate_video_card")

    def test_max_results_stops_initial_page_and_never_follows_continuation(self):
        rows, page = self.parse(html([card(), card(VID2), {"continuationItemRenderer": {"token": "TEST_ONLY_ignore"}}]), max_results=1)
        self.assertEqual(len(rows), 1)
        self.assertFalse(page["pagination_attempted"])

    def test_consent_captcha_login_are_failures_but_generic_login_link_is_not(self):
        for body, reason in [(b'<form action="https://consent.youtube.com/save">', "consent_required"),
                             (b'Our systems have detected unusual traffic', "captcha_or_traffic_block"),
                             (b'<title>Sign in - Google Accounts</title>', "login_required")]:
            with self.subTest(reason=reason), self.assertRaises(SearchError) as caught:
                self.parse(body)
            self.assertEqual(caught.exception.reason, reason)
        rows, _ = self.parse(b'<a href="https://accounts.google.com/login">Sign in</a>' + html())
        self.assertEqual(len(rows), 1)

    def test_missing_noresults_and_malformed_structure_are_not_success(self):
        cases = [(b'<html>TEST_ONLY no script</html>', "initial_data_missing_or_invalid"),
                 (html([]), "no_video_results"),
                 (b'<script>var ytInitialData = {"TEST_ONLY":[]};</script>', "unsupported_search_structure")]
        for body, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(SearchError) as caught:
                self.parse(body)
            self.assertEqual(caught.exception.reason, reason)

    def test_raw_json_decoder_handles_braces_inside_titles(self):
        rows, _ = self.parse(html([card(title='TEST_ONLY { bracket } " quoted ')]))
        self.assertIn("{ bracket }", rows[0]["title"])


class SearchCollectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TEST_ONLY_search_")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = self.base / "TEST_ONLY.sqlite3"
        self.log = ResearchLog(self.db)

    def run_collect(self, config, response=None, **kwargs):
        calls = []
        def transport(url, timeout, max_bytes):
            self.assertEqual(self.log.list()[-1]["status"], "started")
            calls.append(url)
            if isinstance(response, Exception):
                raise response
            return html() if response is None else response
        result = collect(config, output_dir=self.base / "output", logdb=self.db, transport=transport,
                         now=lambda: NOW, test_only=True, **kwargs)
        return result, calls

    def rows(self, result):
        path = next(item["path"] for item in result["artifacts"] if item["path"].endswith("observations.jsonl"))
        return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]

    def test_registers_before_http_synthetic_provenance_and_artifact_hashes(self):
        result, calls = self.run_collect(cfg())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["http_attempts"], 1)
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["discovery_evaluation_eligible"])
        self.assertEqual(result["temporal_eligible_observations"], 0)
        row = self.rows(result)[0]
        self.assertEqual(row["provenance"], "ai_synthetic")
        self.assertFalse(row["sample_method"]["unseeded_discovery"])
        self.assertEqual(row["sample_method"]["search_mode"], "topic_query")
        for artifact in self.log.list()[0]["finish"]["artifacts"]:
            self.assertEqual(artifact["sha256"], hash_file(artifact["path"]))

    def test_duplicate_run_blocks_and_explicit_retry_preserves_old_raw(self):
        first, _ = self.run_collect(cfg())
        with self.assertRaises(DuplicateRunError):
            self.run_collect(cfg())
        second, _ = self.run_collect(cfg(), retry_of=first["run_id"], retry_reason="TEST_ONLY retry")
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(self.rows(first)[0]["observation_id"], self.rows(second)[0]["observation_id"])

    def test_rate_limit_and_consent_stop_remaining_queries_without_retry(self):
        config = cfg(queries=[{"query": "TEST_ONLY first"}, {"query": "TEST_ONLY second"}])
        result, calls = self.run_collect(config, SearchError("rate_limited", 429))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["requests"][1]["status"], "not_requested")
        self.assertEqual(result["http_attempts"], 1)

    def test_empty_results_are_logged_failure_with_raw_not_fabricated_rows(self):
        result, _ = self.run_collect(cfg(), html([]))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["observations"], 0)
        self.assertEqual(result["issues"][0]["reason"], "no_video_results")
        self.assertTrue(any(item["path"].endswith(".html") for item in result["artifacts"]))

    def test_hard_cap_and_dedup_across_queries(self):
        config = cfg(queries=[{"query": "TEST_ONLY one"}, {"query": "TEST_ONLY two"}])
        result, calls = self.run_collect(config)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["observations"], 1)
        self.assertEqual(len(self.rows(result)[0]["search_references"]), 2)
        capped, calls = self.run_collect({**config, "max_results_total": 1})
        self.assertEqual(len(calls), 1)
        self.assertEqual(capped["requests"][1]["status"], "not_requested")

    def expansion_config(self):
        observations = self.base / "TEST_ONLY_source.jsonl"
        observations.write_text(json.dumps({"observation_id": "TEST_ONLY_observation", "text": "TEST_ONLY observed phrase is in this source",
             "provenance": "ai_synthetic", "available_at": "2026-09-14T00:00:00Z"}) + "\n", encoding="utf-8")
        manifest = self.base / "TEST_ONLY_manifest.json"
        manifest.write_text(json.dumps({"run_id": "TEST_ONLY_origin_run"}), encoding="utf-8")
        return cfg(search_mode="discovered_phrase_expansion", queries=[{"query": "TEST_ONLY observed phrase", "evidence_ids": ["TEST_ONLY_observation"]}],
                   discovery_manifest=str(manifest), discovery_observations=str(observations), discovery_run_id="TEST_ONLY_origin_run",
                   discovery_method="TEST_ONLY observed text selection")

    def test_expansion_verifies_actual_origin_run_text_ids_and_input_hashes(self):
        result, _ = self.run_collect(self.expansion_config())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["input_snapshots"]), 2)
        row = self.rows(result)[0]
        self.assertEqual(row["discovery_evidence_ids"], ["TEST_ONLY_observation"])
        self.assertEqual(row["sample_method"]["discovery_run_id"], "TEST_ONLY_origin_run")
        self.assertFalse(row["discovery_evaluation_eligible"])

    def test_false_origin_or_posthoc_unseen_phrase_never_requests_http(self):
        config = self.expansion_config()
        config["queries"] = [{"query": "TEST_ONLY not observed", "evidence_ids": ["TEST_ONLY_observation"]}]
        result, calls = self.run_collect(config)
        self.assertEqual(calls, [])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["issues"][0]["reason"], "evidence_validation_failed")

    def test_query_mode_and_bounded_configuration_constraints(self):
        invalid = [cfg(search_mode="no_named_seed"), cfg(max_results_total=100), cfg(queries=[]),
                   cfg(search_mode="known_name_lookup"), cfg(search_mode="discovered_phrase_expansion"),
                   cfg(known_meme_seed_names=["TEST_ONLY known meme"])]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_config(config)
        good = validate_config(cfg(search_mode="known_name_lookup", known_meme_seed_names=["TEST_ONLY known meme"]))
        self.assertEqual(good["search_mode"], "known_name_lookup")

    def test_injected_transport_requires_synthetic_marker(self):
        with self.assertRaises(ValueError):
            collect(cfg(), output_dir=self.base, logdb=self.db, transport=lambda *args: html())

    def test_public_fetch_rejects_other_hosts_and_does_not_follow_rate_limit_or_redirect(self):
        for url in ("http://www.youtube.com/results", "https://evil.example/results", "https://www.youtube.com/youtubei/v1/search"):
            with self.assertRaises(SearchError):
                fetch_public_page(url, 2, 2000)
        for code, reason in ((429, "rate_limited"), (302, "redirect_blocked")):
            error = urllib.error.HTTPError(URL, code, "TEST_ONLY private error text", {}, io.BytesIO(b""))
            with patch("urllib.request.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(SearchError) as caught:
                    fetch_public_page(URL, 2, 2000)
                self.assertEqual(caught.exception.reason, reason)
                request = opener.return_value.open.call_args.args[0]
                self.assertNotIn("Cookie", request.headers)
                self.assertNotIn("Authorization", request.headers)
                self.assertEqual(opener.return_value.open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
