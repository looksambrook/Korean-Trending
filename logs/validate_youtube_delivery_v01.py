"""One bounded local check of the new collection path and actual saved feeds."""
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from research_log import ResearchLog, hash_file

MODULES = ["test_youtube_feed_collect", "test_youtube_collect", "test_youtube_candidates",
           "test_youtube_context_bundle", "test_youtube_web_search"]
collection = ROOT / "data/collected/youtube_feed_20260915_v02"
report = ROOT / "data/derived/youtube_observatory_20260915_v01.html"
sources = [ROOT / "src" / (name.removeprefix("test_") + ".py") for name in MODULES]
sources += [ROOT / "tests" / (name + ".py") for name in MODULES]
sources += [Path(__file__), ROOT / "src/youtube_watch.py"]
log = ResearchLog(ROOT / "logs/research.sqlite3")
spec = {"objective": "Check implemented YouTube collection behavior against fixtures and actual saved feed XML",
        "stage": "offline_validation", "parameters": {"test_modules": MODULES, "network_requests": 0,
        "actual_check": "All observed IDs, titles and descriptions match saved raw feed entries; report JavaScript parses"},
        "data_snapshots": [{"id": "observations", "sha256": hash_file(collection / "observations.jsonl")},
                           {"id": "local_report", "sha256": hash_file(report)}],
        "code_hashes": {str(path.relative_to(ROOT)): hash_file(path) for path in sources},
        "prompt_hashes": {}, "config_hashes": {}, "modality": ["text", "metadata"], "provenance": "mixed"}
record = log.begin(spec)
artifacts = []
try:
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(MODULES))
    output = ROOT / "logs/youtube_delivery_tests_v01.txt"
    with output.open("x", encoding="utf-8") as handle:
        handle.write(stream.getvalue())
    artifacts.append(output)
    assert result.wasSuccessful(), "Focused software checks failed"
    observations = [json.loads(line) for line in (collection / "observations.jsonl").read_text(encoding="utf-8").splitlines()]
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "m": "http://search.yahoo.com/mrss/"}
    original = {}
    for raw in (collection / "raw").glob("*.xml"):
        for entry in ET.parse(raw).getroot().findall("a:entry", ns):
            original[entry.findtext("yt:videoId", namespaces=ns)] = entry
    assert len(original) == len(observations) == 45
    for row in observations:
        entry = original[row["video_id"]]
        assert row["context"]["title"] == entry.findtext("a:title", namespaces=ns)
        assert row["context"]["description"] == entry.findtext("m:group/m:description", default="", namespaces=ns)
        assert row["channel_id"] == entry.findtext("yt:channelId", namespaces=ns)
        assert row["provenance"] == "actual_collected"
    html = report.read_text(encoding="utf-8")
    payload = json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>', html, re.S).group(1))
    assert len(payload["observations"]) == 45 and len(payload["expansion"]) == 20
    javascript = re.search(r'<script>\s*(.*?)</script>', html, re.S).group(1)
    with tempfile.TemporaryDirectory(prefix="youtube-report-check-") as directory:
        script_path = Path(directory) / "report.js"
        script_path.write_text(javascript, encoding="utf-8")
        checked = subprocess.run(["node", "--check", str(script_path)], capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr
    audit = {"run_id": record["run_id"], "software_tests": result.testsRun,
             "software_tests_passed": result.wasSuccessful(), "actual_feed_rows_matched_to_raw": len(observations),
             "report_feed_documents": 45, "report_expansion_documents": 20,
             "javascript_syntax": "passed", "visual_browser_review": "not_performed",
             "meme_accuracy": None, "model_understanding_score": None, "network_requests": 0}
    audit_path = ROOT / "logs/youtube_delivery_validation_v01.json"
    with audit_path.open("x", encoding="utf-8") as handle:
        json.dump(audit, handle, ensure_ascii=False, indent=2)
    artifacts.append(audit_path)
    log.finish(record["run_id"], status="succeeded", notes=f"{result.testsRun} focused synthetic software tests passed; all 45 actual metadata rows matched saved original feed XML; local report payload and JavaScript syntax checked. No meme accuracy or understanding claim.", artifacts=artifacts,
               actual_cost={"amount": 0, "currency": "USD", "human_minutes": 0})
    print(json.dumps(audit, indent=2))
except BaseException as exc:
    log.finish(record["run_id"], status="failed", notes="Delivery check failed: " + type(exc).__name__, artifacts=artifacts)
    raise
