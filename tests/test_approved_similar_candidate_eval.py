from __future__ import annotations

import importlib
import json
from pathlib import Path


runner = importlib.import_module("eval.approved_similar_candidate_runner")


def _write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _candidate(qa_id: str, **overrides):
    data = {
        "qa_id": qa_id,
        "question_text": f"question {qa_id}",
        "semantic_score": 0.9,
        "semantic_distance": 0.1,
        "keyword_score": 0.8,
        "hybrid_score": 0.86,
        "top1_top2_margin": 0.2,
        "margin_score_basis": "hybrid_score",
        "numeric_conflict": False,
        "negation_conflict": False,
        "matched_terms": [f"term-{i}" for i in range(12)],
        "matched_fields": [f"field-{i}" for i in range(12)],
        "synonym_matches": [
            {"query_term": f"q{i}", "matched_synonym": f"s{i}", "field": "question_text"}
            for i in range(8)
        ],
    }
    data.update(overrides)
    return data


def test_run_eval_counts_passes_and_failures(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {"query": "pass query", "expected_top_qa_id": "qa_pass"},
            {"query": "fail query", "expected_top_qa_id": "qa_expected"},
        ],
    )

    def fake_search(query, **kwargs):
        if query == "pass query":
            return [_candidate("qa_pass")]
        return [_candidate("qa_actual")]

    payload = runner.run_eval(cases=cases_path, top_k=3, search_fn=fake_search)

    assert payload["total"] == 2
    assert payload["passed"] == 1
    assert payload["failed"] == 1
    assert payload["pass_rate"] == 0.5
    assert payload["per_case"][0]["passed"] is True
    assert payload["per_case"][1]["failure_reasons"] == [
        "expected top qa_id qa_expected, got qa_actual"
    ]


def test_expected_top_qa_id_match_is_checked(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(cases_path, [{"query": "q", "expected_top_qa_id": "qa_top"}])

    payload = runner.run_eval(
        cases=cases_path,
        top_k=2,
        search_fn=lambda query, **kwargs: [_candidate("qa_top"), _candidate("qa_second")],
    )

    result = payload["per_case"][0]
    assert result["passed"] is True
    assert result["expected_top_qa_id"] == "qa_top"
    assert result["actual_top_qa_id"] == "qa_top"


def test_numeric_conflict_expectation_is_checked_on_top_candidate(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [{"query": "17問に自由回答は含まれますか？", "expected_top_qa_id": "qa_free", "expected_numeric_conflict": True}],
    )

    passing = runner.run_eval(
        cases=cases_path,
        search_fn=lambda query, **kwargs: [_candidate("qa_free", numeric_conflict=True)],
    )
    failing = runner.run_eval(
        cases=cases_path,
        search_fn=lambda query, **kwargs: [_candidate("qa_free", numeric_conflict=False)],
    )

    assert passing["per_case"][0]["passed"] is True
    assert failing["per_case"][0]["passed"] is False
    assert failing["per_case"][0]["failure_reasons"] == [
        "expected top numeric_conflict True, got False"
    ]


def test_json_report_shape_is_bounded_and_inspectable(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    output_path = tmp_path / "report.json"
    _write_jsonl(cases_path, [{"query": "q", "expected_top_qa_id": "qa_top"}])

    payload = runner.run_eval(
        cases=cases_path,
        output=output_path,
        collection="approved_qa_pair_pr_test",
        top_k=1,
        search_fn=lambda query, **kwargs: [_candidate("qa_top"), _candidate("qa_extra")],
    )

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written == payload
    assert set(payload.keys()) == {
        "total",
        "passed",
        "failed",
        "pass_rate",
        "collection",
        "top_k",
        "per_case",
    }

    result = payload["per_case"][0]
    assert set(result.keys()) == {
        "query",
        "expected_top_qa_id",
        "actual_top_qa_id",
        "passed",
        "failure_reasons",
        "expected_numeric_conflict",
        "expected_negation_conflict",
        "top_candidate",
        "candidates",
    }
    assert len(result["candidates"]) == 1
    assert len(result["top_candidate"]["matched_terms"]) == 8
    assert len(result["top_candidate"]["matched_fields"]) == 8
    assert len(result["top_candidate"]["synonym_matches"]) == 5
