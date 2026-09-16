"""TEST_ONLY synthetic HTTP responses. Never evidence of live API access/memes."""
import copy
import io
import json
import os
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
from youtube_collect import APIClient, CollectionError, collect, official_transport, validate_config

NOW = datetime(2026, 9, 15, 1, tzinfo=timezone.utc)
OLD = "2026-09-01T00:00:00Z"
CID = "UC" + "a" * 22
VID = "a" * 11
VID2 = "b" * 11
SECRET = "TEST_ONLY_NOT_A_REAL_CREDENTIAL"


def video(vid=VID, title="TEST_ONLY synthetic text"):
    return {"id": vid, "snippet": {"channelId": CID, "title": title,
            "description": "TEST_ONLY AI synthetic fixture description", "publishedAt": OLD},
            "statistics": {"viewCount": "17", "likeCount": "3", "commentCount": "2"}}


def comment(cid="TEST_ONLY_comment", parent=None):
    snippet = {"textDisplay": "TEST_ONLY synthetic comment", "publishedAt": OLD,
               "updatedAt": OLD, "likeCount": 1,
               "authorDisplayName": "TEST_ONLY_DO_NOT_PERSIST_AUTHOR",
               "authorChannelId": {"value": "TEST_ONLY_DO_NOT_PERSIST_AUTHOR"}}
    if parent:
        snippet["parentId"] = parent
    return {"id": cid, "snippet": snippet}


def thread(cid="TEST_ONLY_comment", reply_count=0):
    return {"id": "TEST_ONLY_thread_" + cid,
            "snippet": {"topLevelComment": comment(cid), "totalReplyCount": reply_count}}


def config(**changes):
    return {"mode": "videos", "video_ids": [VID], "query_kind": "diagnostic",
            "selection_reason": "TEST_ONLY synthetic collection contract", **changes}


class FakeTransport:
    def __init__(self, responses, log=None):
        self.responses = list(responses)
        self.calls = []
        self.log = log

    def __call__(self, endpoint, params, headers, timeout):
        if self.log:
            assert self.log.list()[-1]["status"] == "started", "must begin before HTTP"
        self.calls.append((endpoint, params, headers, timeout))
        expected, response = self.responses.pop(0)
        assert endpoint == expected, (expected, endpoint)
        assert "key" not in params
        assert headers["X-Goog-Api-Key"] == SECRET
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


class YouTubeCollectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TEST_ONLY_youtube_collect_")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = self.base / "TEST_ONLY.sqlite3"
        self.log = ResearchLog(self.db)

    def run_collect(self, cfg, responses, **kwargs):
        transport = FakeTransport(responses, self.log)
        result = collect(cfg, output_dir=self.base / "output", logdb=self.db,
                         api_key=SECRET, transport=transport, now=lambda: NOW,
                         test_only=True, **kwargs)
        return result, transport

    def records(self, manifest):
        path = next(Path(item["path"]) for item in manifest["artifacts"] if item["path"].endswith("observations.jsonl"))
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_channel_uploads_video_comment_reply_context_and_artifact_hashes(self):
        cfg = {"mode": "channels", "channels": [{"handle": "@TEST_ONLY_channel"}],
               "selection_reason": "TEST_ONLY unseeded channel scope"}
        result, transport = self.run_collect(cfg, [
            ("channels", {"items": [{"id": CID, "contentDetails": {"relatedPlaylists": {"uploads": "UUtest"}}}]}),
            ("playlistItems", {"items": [{"contentDetails": {"videoId": VID}}]}),
            ("videos", {"items": [video()]}),
            ("commentThreads", {"items": [thread(reply_count=1)]}),
            ("comments", {"items": [comment("TEST_ONLY_reply", "TEST_ONLY_comment")]}),
        ])
        self.assertEqual(result["status"], "completed")
        self.assertEqual((result["videos"], result["comments"]), (1, 2))
        rows = self.records(result)
        self.assertEqual(rows[2]["parent_id"], rows[1]["source_id"])
        self.assertEqual(rows[2]["context"]["parent_text"], rows[1]["text"])
        self.assertTrue(all(row["video_id"] == VID and row["channel_id"] == CID for row in rows))
        self.assertTrue(all(row["provenance"] == "ai_synthetic" and row["test_only"] for row in rows))
        self.assertTrue(all(row["available_at"] == NOW.isoformat() for row in rows))
        self.assertTrue(all(row["posted_at"] < row["available_at"] for row in rows))
        self.assertEqual(transport.calls[0][1]["forHandle"], "@TEST_ONLY_channel")
        self.assertFalse(result["history_reconstruction"])
        self.assertEqual(self.log.list()[0]["status"], "succeeded")
        for artifact in self.log.list()[0]["finish"]["artifacts"]:
            self.assertEqual(artifact["sha256"], hash_file(artifact["path"]))
            content = Path(artifact["path"]).read_text(encoding="utf-8")
            self.assertNotIn("TEST_ONLY_DO_NOT_PERSIST_AUTHOR", content)
            self.assertNotIn(SECRET, content)

    def test_missing_key_is_logged_failure_zero_network(self):
        with patch.dict(os.environ, {}, clear=True):
            result = collect(config(), output_dir=self.base / "output", logdb=self.db)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["fatal_reason"], "missing_credential")
        self.assertEqual(result["http_attempts"], {"search": 0, "other": 0})
        self.assertEqual(result["observations"], 0)
        self.assertEqual(self.log.list()[0]["status"], "failed")

    def test_duplicate_blocks_before_transport_and_retry_is_explicit(self):
        cfg = config(limits={"max_comments_total": 0})
        first, _ = self.run_collect(cfg, [("videos", {"items": [video()]})])
        with self.assertRaises(DuplicateRunError):
            self.run_collect(cfg, [])
        second, _ = self.run_collect(cfg, [("videos", {"items": [video()]})],
                                    retry_of=first["run_id"], retry_reason="TEST_ONLY intentional retry")
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(self.records(first)[0]["observation_id"], self.records(second)[0]["observation_id"])
        self.assertEqual(self.log.list()[1]["retry_of"], first["run_id"])

    def test_content_version_changes_observation_id(self):
        cfg = config(limits={"max_comments_total": 0})
        first, _ = self.run_collect(cfg, [("videos", {"items": [video()]})])
        second, _ = self.run_collect(cfg, [("videos", {"items": [video(title="TEST_ONLY revised text")]})],
                                    retry_of=first["run_id"], retry_reason="TEST_ONLY edited source")
        self.assertNotEqual(self.records(first)[0]["observation_id"], self.records(second)[0]["observation_id"])

    def test_comments_disabled_is_partial_not_empty_or_success(self):
        result, transport = self.run_collect(config(), [
            ("videos", {"items": [video()]}),
            ("commentThreads", {"error": {"errors": [{"reason": "commentsDisabled"}], "message": SECRET}}),
        ])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["comments"], 0)
        self.assertEqual(result["issues"][0]["reason"], "commentsDisabled")
        self.assertEqual(self.log.list()[0]["status"], "failed")
        self.assertEqual(len(transport.calls), 2)
        self.assertNotIn(SECRET, json.dumps(result))

    def test_legitimate_empty_comments_are_exhausted(self):
        result, _ = self.run_collect(config(), [("videos", {"items": [video()]}), ("commentThreads", {"items": []})])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"][0]["status"], "exhausted")
        self.assertEqual(result["issues"], [])

    def test_missing_requested_video_is_explicit(self):
        result, _ = self.run_collect(config(video_ids=[VID, VID2], limits={"max_comments_total": 0}),
                                     [("videos", {"items": [video()]})])
        self.assertEqual(result["status"], "partial")
        self.assertIn({"scope": "video", "reason": "requested_video_not_returned", "source_id": VID2, "http_status": None}, result["issues"])

    def test_repeated_pagination_token_fails_and_deduplicates(self):
        result, transport = self.run_collect(config(limits={"max_comment_pages_per_video": 3}), [
            ("videos", {"items": [video()]}),
            ("commentThreads", {"items": [thread()], "nextPageToken": "TEST_ONLY_page"}),
            ("commentThreads", {"items": [thread()], "nextPageToken": "TEST_ONLY_page"}),
        ])
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(result["comments"], 1)
        self.assertEqual(result["status"], "partial")
        self.assertIn("repeated_or_invalid_page_token", [issue["reason"] for issue in result["issues"]])

    def test_independent_buckets_and_local_cap_prevent_extra_http(self):
        cfg = {"mode": "search", "query_kind": "no_named_seed", "selection_reason": "TEST_ONLY no query",
               "limits": {"max_other_calls": 1, "max_search_calls": 1}}
        result, transport = self.run_collect(cfg, [
            ("search", {"items": [{"id": {"videoId": VID}}]}),
            ("videos", {"items": [video()]}),
        ])
        self.assertEqual(result["http_attempts"], {"search": 1, "other": 1})
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(result["status"], "partial")
        self.assertIn("local_call_cap", [item["reason"] for item in result["issues"]])
        self.assertNotIn("q", transport.calls[0][1])

    def test_search_zero_matches_is_empty_and_no_video_fetch(self):
        result, transport = self.run_collect({"mode": "search", "query_kind": "topic", "query": "TEST_ONLY topic",
                 "selection_reason": "TEST_ONLY topic-based sampling"}, [("search", {"items": []})])
        self.assertEqual(result["status"], "empty")
        self.assertEqual(len(transport.calls), 1)

    def test_budget_caps_have_explicit_bounded_coverage(self):
        result, transport = self.run_collect(config(limits={"max_comments_total": 1}), [
            ("videos", {"items": [video()]}),
            ("commentThreads", {"items": [thread(reply_count=2)], "nextPageToken": "TEST_ONLY_more"}),
        ])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["comments"], 1)
        self.assertTrue(all(item["status"] == "bounded" for item in result["coverage"]))
        self.assertEqual(transport.calls[1][1]["maxResults"], 1)

    def test_future_posts_and_updates_are_excluded(self):
        post = video()
        post["snippet"]["publishedAt"] = "2030-01-01T00:00:00Z"
        result, _ = self.run_collect(config(), [("videos", {"items": [post]})])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["observations"], 0)
        self.assertEqual(result["excluded"][0]["reason"], "future_dated")
        changed = config(selection_reason="TEST_ONLY future update")
        updated_thread = thread()
        updated_thread["snippet"]["topLevelComment"]["snippet"]["updatedAt"] = "2030-01-01T00:00:00Z"
        result, _ = self.run_collect(changed, [("videos", {"items": [video()]}), ("commentThreads", {"items": [updated_thread]})])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["comments"], 0)

    def test_publication_window_is_not_historical_availability(self):
        result, _ = self.run_collect(config(publication_start="2026-08-01T00:00:00Z", publication_end="2026-09-02T00:00:00Z",
                                             limits={"max_comments_total": 0}), [("videos", {"items": [video()]})])
        self.assertGreater(self.records(result)[0]["available_at"], result["config"]["publication_end"])
        self.assertFalse(result["history_reconstruction"])

    def test_transport_error_does_not_leak_secrets_or_retry(self):
        result, transport = self.run_collect(config(), [("videos", RuntimeError("header " + SECRET))])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn(SECRET, json.dumps(self.log.list()))

    def test_partial_result_survives_later_video_error(self):
        result, _ = self.run_collect(config(video_ids=[VID, VID2]), [
            ("videos", {"items": [video(), video(VID2)]}),
            ("commentThreads", {"items": [thread()]}),
            ("commentThreads", CollectionError("quotaExceeded", 403)),
        ])
        self.assertEqual((result["videos"], result["comments"]), (2, 1))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(self.records(result)), 3)

    def test_reply_parent_mismatch_is_not_linked(self):
        result, _ = self.run_collect(config(), [("videos", {"items": [video()]}),
                  ("commentThreads", {"items": [thread(reply_count=1)]}),
                  ("comments", {"items": [comment("TEST_ONLY_reply", "TEST_ONLY_wrong_parent")]})])
        self.assertEqual(result["comments"], 1)
        self.assertIn("reply_parent_mismatch", [issue["reason"] for issue in result["issues"]])

    def test_injection_requires_explicit_synthetic_marker(self):
        with self.assertRaises(ValueError):
            collect(config(), output_dir=self.base, logdb=self.db, transport=lambda *args: {})

    def test_named_seed_topic_and_unseeded_labels_are_distinct(self):
        with self.assertRaises(ValueError):
            validate_config({"mode": "search", "query_kind": "no_named_seed", "query": "TEST_ONLY named meme",
                             "selection_reason": "TEST_ONLY"})
        with self.assertRaises(ValueError):
            validate_config({"mode": "search", "query_kind": "known_name_lookup", "query": "TEST_ONLY named meme",
                             "selection_reason": "TEST_ONLY"})
        good = validate_config({"mode": "search", "query_kind": "known_name_lookup", "query": "TEST_ONLY named meme",
                                "known_meme_seed_names": ["TEST_ONLY named meme"], "selection_reason": "TEST_ONLY"})
        self.assertEqual(good["query_kind"], "known_name_lookup")

    def test_unknown_secrets_or_unbounded_config_rejected_before_http(self):
        for invalid in (config(api_key=SECRET), config(limits={"max_videos": 10000}),
                        config(limits={"max_other_calls": True}), config(retention_days=31)):
            with self.assertRaises(ValueError):
                validate_config(invalid)

    def test_official_transport_header_only_and_redirects_blocked(self):
        error = urllib.error.HTTPError("https://www.googleapis.com/youtube/v3/videos", 302, SECRET,
                                       {"Location": "https://evil.example/"}, io.BytesIO(b""))
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(CollectionError) as caught:
                official_transport("videos", {"part": "snippet", "id": VID}, {"X-Goog-Api-Key": SECRET}, 3)
            self.assertEqual(caught.exception.reason, "redirect_blocked")
            self.assertNotIn(SECRET, str(caught.exception))
            request = opener.return_value.open.call_args.args[0]
            self.assertTrue(request.full_url.startswith("https://www.googleapis.com/youtube/v3/videos?"))
            self.assertNotIn(SECRET, request.full_url)
            self.assertEqual(opener.return_value.open.call_count, 1)

    def test_official_transport_errors_are_reduced_to_allowlisted_reasons(self):
        error = urllib.error.HTTPError("https://www.googleapis.com/youtube/v3/videos", 403, SECRET, {},
                     io.BytesIO(json.dumps({"error": {"message": SECRET, "errors": [{"reason": SECRET}]}}).encode()))
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(CollectionError) as caught:
                official_transport("videos", {}, {"X-Goog-Api-Key": SECRET}, 3)
            self.assertEqual(str(caught.exception), "api_error")
        with self.assertRaises(CollectionError):
            official_transport("https://evil.example/", {}, {}, 3)

    def test_credentials_cannot_be_query_params_or_exception_reasons(self):
        self.assertNotIn(SECRET, str(CollectionError(SECRET, SECRET)))
        client = APIClient(api_key=SECRET, limits={"max_search_calls": 1, "max_other_calls": 1},
                           transport=lambda *args: self.fail("No HTTP is allowed"))
        for endpoint, params in (("videos", {"key": SECRET}), ("search", {"q": SECRET})):
            with self.assertRaises(CollectionError):
                client.get(endpoint, **params)
        self.assertEqual(sum(client.calls.values()), 0)
        with self.assertRaises(CollectionError):
            official_transport("videos", {"key": SECRET}, {}, 3)

    def test_quota_failure_stops_more_calls_in_that_bucket(self):
        transport = FakeTransport([("videos", CollectionError("quotaExceeded", 403)),
                                   ("search", {"items": []})])
        client = APIClient(api_key=SECRET, limits={"max_search_calls": 1, "max_other_calls": 10}, transport=transport)
        with self.assertRaises(CollectionError):
            client.get("videos", part="snippet", id=VID)
        with self.assertRaises(CollectionError):
            client.get("comments", part="snippet", parentId="TEST_ONLY_comment")
        self.assertEqual(client.calls["other"], 1)
        self.assertEqual(client.get("search", part="snippet", type="video")[0]["items"], [])
        self.assertEqual(len(transport.calls), 2)


if __name__ == "__main__":
    unittest.main()
