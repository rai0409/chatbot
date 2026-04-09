from __future__ import annotations

import importlib
import json
from pathlib import Path

runner = importlib.import_module("eval.runner")


def _write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_load_cases_parses_fixture_rows(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "c1",
                "category": "quoted term",
                "query": "「請求書ID」 の確認方法",
                "intent_override": "other",
                "expected_top_chunk_id": "g1",
                "expected_guard_reason": None,
                "expected_used_fallback": False,
                "answer_must_contain": ["請求書"],
                "answer_must_not_contain": ["推測"],
            }
        ],
    )

    cases = runner.load_cases(cases_path)
    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "c1"
    assert case.intent_override == "other"
    assert "expected_top_chunk_id" in case.expectation_keys
    assert "expected_guard_reason" in case.expectation_keys
    assert "answer_must_contain" in case.expectation_keys


def test_evaluate_expectations_checks_core_fields(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "c2",
                "category": "ambiguous",
                "query": "これは？",
                "expected_top_chunk_id": "x1",
                "expected_guard_reason": "too_general",
                "expected_used_fallback": True,
                "answer_must_contain": ["関連情報"],
            }
        ],
    )
    case = runner.load_cases(cases_path)[0]

    checks = runner.evaluate_expectations(
        case,
        after_rerank_top=[{"chunk_id": "x1", "source_doc": "doc.pdf"}],
        guard_reason="too_general",
        used_fallback=True,
        answer_text="関連情報が見つかりませんでした。",
        selected_context_preview=[],
    )

    assert checks["expected_top_chunk_id"]["pass"] is True
    assert checks["expected_guard_reason"]["pass"] is True
    assert checks["expected_used_fallback"]["pass"] is True
    assert checks["answer_must_contain"]["pass"] is True


def test_run_eval_outputs_before_after_and_json_keys(tmp_path):
    chunks_path = tmp_path / "chunks.jsonl"
    _write_jsonl(
        chunks_path,
        [
            {
                "id": "g1",
                "text": "請求書ID は帳票ヘッダーで確認できます。",
                "source_doc": "glossary.pdf",
                "source_pages": [1],
                "doc_id": "glossary.pdf",
                "chunk_index": 1,
                "searchable": 1,
                "type": "pdf",
                "quality": "high",
            },
            {
                "id": "g2",
                "text": "請求書番号の一般説明です。",
                "source_doc": "glossary.pdf",
                "source_pages": [2],
                "doc_id": "glossary.pdf",
                "chunk_index": 2,
                "searchable": 1,
                "type": "pdf",
                "quality": "high",
            },
        ],
    )

    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "quoted",
                "category": "quoted term",
                "query": "「請求書ID」 の確認方法",
                "expected_top_chunk_id": "g1",
                "expected_used_fallback": False,
            }
        ],
    )

    out_path = tmp_path / "results.json"
    payload = runner.run_eval(
        cases_path=cases_path,
        chunks_jsonl=chunks_path,
        output_path=out_path,
        top_k=5,
        max_context_chars=2000,
        top_n=3,
        real_vector=False,
        real_generation=False,
        quiet=True,
    )

    assert out_path.exists()
    assert payload["summary"]["total_cases"] == 1
    assert set(payload["summary"].keys()) >= {
        "schema_version",
        "runner_version",
        "generated_at",
        "total_cases",
        "passed_cases",
        "failed_cases",
    }

    case = payload["cases"][0]
    assert set(case.keys()) >= {
        "case_id",
        "category",
        "query",
        "intent",
        "before_rerank_top",
        "after_rerank_top",
        "final_guard_reason",
        "final_used_fallback",
        "expectations",
        "checks",
        "overall_pass",
        "evaluation_mode",
        "selected_context_preview",
    }
    assert case["before_rerank_top"]
    assert case["after_rerank_top"]


def test_runner_help_explains_deterministic_smoke_positioning():
    parser = runner._build_parser()
    help_text = parser.format_help()
    help_text_l = help_text.lower()
    help_text_flat = " ".join(help_text_l.split())
    assert "Lightweight repo-native smoke evaluator" in help_text
    assert "generation is stubbed" in help_text
    assert "retrieval is stubbed empty" in help_text
    assert "not a full live end-to-end answer quality benchmark" in help_text_flat


def test_repo_smoke_cases_run_and_include_rerank_movement(tmp_path):
    out_path = tmp_path / "smoke_results.json"
    payload = runner.run_eval(
        cases_path=runner._default_cases_path(),
        chunks_jsonl=runner._default_chunks_path(),
        output_path=out_path,
        top_k=20,
        max_context_chars=8000,
        top_n=3,
        real_vector=False,
        real_generation=False,
        quiet=True,
    )

    assert out_path.exists()
    assert payload["summary"]["total_cases"] >= 13
    assert payload["summary"]["failed_cases"] == 0

    case_map = {c["case_id"]: c for c in payload["cases"]}
    assert "rerank_movement_01" in case_map
    assert "guard_trigger_02" in case_map

    movement = case_map["rerank_movement_01"]
    before_ids = [x["chunk_id"] for x in movement["before_rerank_top"]]
    after_ids = [x["chunk_id"] for x in movement["after_rerank_top"]]
    assert before_ids != after_ids
    assert movement["checks"]["expected_top_chunk_id"]["pass"] is True

    guard_case = case_map["guard_trigger_02"]
    assert guard_case["checks"]["expected_guard_reason"]["pass"] is True
