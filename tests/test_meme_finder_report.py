"""Report behavior with synthetic fixtures; never counted as collected evidence."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from meme_finder_report import markdown_report, render_report


class MemeFinderReportTests(unittest.TestCase):
    def test_untrusted_titles_escape_and_non_youtube_links_stay_plain(self):
        result = {"families": [{"label": '<img src=x onerror="alert(1)">',
                  "examples": [
                      {"title": '<script>alert("title")</script>', "source_url": "https://www.youtube.com/watch?v=valid"},
                      {"title": "hostile link", "source_url": "javascript:alert(1)"},
                      {"title": "lookalike host", "source_url": "https://youtube.com.attacker.invalid/watch?v=x"},
                      {"title": "userinfo", "source_url": "https://attacker@youtube.com/watch?v=x"},
                  ], "knowledge": {"meaning": "</script><script>alert(2)</script>"}}]}
        with tempfile.TemporaryDirectory() as directory:
            path = render_report(result, Path(directory) / "report.html")
            html = path.read_text(encoding="utf-8")
        self.assertIn('&lt;script&gt;alert(&quot;title&quot;)&lt;/script&gt;', html)
        self.assertNotIn('<img src=x', html)
        self.assertNotIn('<script>alert', html)
        self.assertIn('href="https://www.youtube.com/watch?v=valid"', html)
        self.assertNotIn('href="javascript:', html)
        self.assertNotIn('href="https://youtube.com.attacker.invalid', html)
        self.assertNotIn('href="https://attacker@youtube.com', html)
        markdown = markdown_report(result)
        self.assertNotIn('<script>', markdown)
        self.assertNotIn('](javascript:', markdown)
        self.assertNotIn('](https://youtube.com.attacker.invalid', markdown)

    def test_empty_results_are_readable_and_keep_failure_visible(self):
        result = {"families": [], "failures": [{"query": "bounded discovery", "error": "HTTP 429"}]}
        with tempfile.TemporaryDirectory() as directory:
            html = render_report(result, Path(directory) / "nested" / "report.html").read_text(encoding="utf-8")
        self.assertIn("아직 보고할 밈 계열이 없습니다", html)
        self.assertIn("HTTP 429", html)
        self.assertIn('<details class="supplement" open><summary>수집·처리 실패', html)
        self.assertIn("현재 수집 범위에서 보고할 밈 계열을 찾지 못했습니다", markdown_report(result))
        self.assertNotIn('<script src=', html)


if __name__ == "__main__":
    unittest.main()
