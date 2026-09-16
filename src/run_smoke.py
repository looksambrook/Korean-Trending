"""Produce explicitly synthetic offline workflow artifacts, never research results."""
import argparse
import sys
from pathlib import Path
import meme_pipeline as mp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_pipeline import fixture, mock_extraction_responses, mock_generation_responses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    mp.require(not out.exists(), "Choose a new output directory")
    cfg, families, supports, scenarios = fixture()
    requests = mp.make_extractions(families, supports, cfg)
    responses = mock_extraction_responses(requests)
    records, failures = mp.import_extractions(requests, responses, cfg)
    gen, gen_failures, audit = mp.generation_requests(families, supports, scenarios, records, cfg)
    gen_responses = mock_generation_responses(gen)
    for name, rows in (
        ("extraction.requests.jsonl", requests), ("SYNTHETIC_extraction.responses.jsonl", responses),
        ("SYNTHETIC_records.jsonl", records), ("extraction.failures.jsonl", failures),
        ("generation.requests.jsonl", gen), ("generation.failures.jsonl", gen_failures),
        ("B2_B3_content_audit.jsonl", audit), ("SYNTHETIC_generation.responses.jsonl", gen_responses),
    ):
        mp.write_jsonl(out / name, rows)
    mp.export_evaluation(gen, gen_responses, gen_failures, scenarios, families, supports, cfg, out / "evaluation")
    preview = ["# B1/B2/B3 입력 비교 — AI 합성 소프트웨어 fixture", "",
               "실제 유행어, 실제 자동 추출, 모델 생성 결과, 사람 평가가 아니다. 사실 대응과 파일 흐름 점검용이다.", ""]
    for scenario in scenarios:
        preview.extend(["## " + scenario["scenario_id"], ""])
        chosen = {r["condition"]: r for r in gen if r["scenario_id"] == scenario["scenario_id"]}
        for condition in cfg["conditions"]:
            preview.extend(["### " + condition, "", "~~~text", chosen[condition]["messages"]["user"], "~~~", ""])
    (out / "input_comparison.md").write_text("\n".join(preview), encoding="utf-8")
    files = [ROOT / p for p in ("configs/smoke.json", "prompts/extract_v1.txt", "prompts/generate_v1.txt",
                                "src/meme_pipeline.py", "src/run_smoke.py", "tests/test_pipeline.py",
                                "data/fixtures/families.jsonl", "data/fixtures/supports.jsonl",
                                "data/fixtures/scenarios.jsonl")]
    counts = {"synthetic_families": len(families), "synthetic_scenarios": len(scenarios),
              "prepared_generation_requests": len(gen), "actual_model_calls": 0,
              "actual_human_ratings": 0}
    mp.write_json(out / "manifest.json", mp.manifest(out, "run_smoke", files, cfg, counts))
    print(__import__("json").dumps({"out": str(out), **counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
