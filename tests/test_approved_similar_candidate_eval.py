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
        "approved_answer_preview": f"answer {qa_id}",
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


def test_case_metadata_and_ambiguous_flag_are_preserved(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "id": "ambiguous-1",
                "category": "multi-topic",
                "source_question_no": 15,
                "query": "15問に自由回答と個人情報は含まれますか？",
                "expected_top_qa_id": "qa_free",
                "ambiguous": True,
            }
        ],
    )

    payload = runner.run_eval(
        cases=cases_path,
        search_fn=lambda query, **kwargs: [_candidate("qa_free")],
    )

    result = payload["per_case"][0]
    assert result["id"] == "ambiguous-1"
    assert result["category"] == "multi-topic"
    assert result["source_question_no"] == 15
    assert result["ambiguous"] is True


def test_expected_any_qa_ids_allows_alternate_top_candidate(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "query": "15問に自由回答と個人情報は含まれますか？",
                "expected_top_qa_id": "qa_free",
                "expected_any_qa_ids": ["qa_free", "qa_personal_info"],
            }
        ],
    )

    payload = runner.run_eval(
        cases=cases_path,
        search_fn=lambda query, **kwargs: [_candidate("qa_personal_info")],
    )

    result = payload["per_case"][0]
    assert result["passed"] is True
    assert result["expected_top_qa_id"] == "qa_free"
    assert result["expected_any_qa_ids"] == ["qa_free", "qa_personal_info"]
    assert result["actual_top_qa_id"] == "qa_personal_info"


def test_expected_any_qa_ids_can_be_used_without_expected_top_qa_id(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "query": "ambiguous",
                "expected_any_qa_ids": ["qa_a", "qa_b"],
            }
        ],
    )

    payload = runner.run_eval(
        cases=cases_path,
        search_fn=lambda query, **kwargs: [_candidate("qa_b")],
    )

    assert payload["per_case"][0]["passed"] is True
    assert payload["per_case"][0]["expected_top_qa_id"] is None


def test_top_answer_preview_uses_first_available_answer_key(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(cases_path, [{"query": "q", "expected_top_qa_id": "qa_top"}])

    payload = runner.run_eval(
        cases=cases_path,
        search_fn=lambda query, **kwargs: [
            _candidate(
                "qa_top",
                approved_answer_preview="",
                answer_text_preview="answer text preview",
                approved_answer="approved answer",
                answer_text="answer text",
            )
        ],
    )

    result = payload["per_case"][0]
    assert result["top_answer_preview"] == "answer text preview"
    assert result["top_candidate"]["answer_preview"] == "answer text preview"


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
        "id",
        "category",
        "source_question_no",
        "ambiguous",
        "query",
        "expected_top_qa_id",
        "expected_any_qa_ids",
        "actual_top_qa_id",
        "top_answer_preview",
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
    assert result["top_candidate"]["answer_preview"] == "answer qa_top"
