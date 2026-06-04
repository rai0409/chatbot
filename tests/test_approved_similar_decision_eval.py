from __future__ import annotations

import importlib
import json
import os
from pathlib import Path


runner = importlib.import_module("eval.approved_similar_decision_eval")


def _write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _candidate(qa_id: str, **overrides):
    data = {
        "qa_id": qa_id,
        "question_text": f"question {qa_id}",
        "approved_answer_preview": f"answer {qa_id}",
        "hybrid_score": 0.9,
        "semantic_score": 0.8,
        "keyword_score": 0.7,
        "weighted_keyword_score": 0.75,
        "top1_top2_margin": 0.12,
        "numeric_conflict": False,
        "negation_conflict": False,
        "generic_matched_terms": ["設問"],
        "specific_matched_terms": ["自由回答"],
        "matched_terms": ["設問", "自由回答"],
        "matched_fields": ["question_text"],
    }
    data.update(overrides)
    return data


def test_decision_eval_route_counts_and_report_files(tmp_path, monkeypatch):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {"id": "pass_high", "category": "direct", "query": "q1", "expected_top_qa_id": "qa_1"},
            {"id": "fail_high", "category": "direct", "query": "q2", "expected_top_qa_id": "qa_2"},
            {"id": "numeric", "category": "numeric", "query": "q3", "expected_top_qa_id": "qa_3"},
            {"id": "candidate", "category": "margin", "query": "q4", "expected_top_qa_id": "qa_4"},
            {"id": "low", "category": "low", "query": "q5", "expected_top_qa_id": "qa_5"},
            {
                "id": "ambiguous",
                "category": "multi",
                "query": "q6",
                "expected_top_qa_id": "qa_6",
                "expected_any_qa_ids": ["qa_6", "qa_7"],
                "ambiguous": True,
            },
        ],
    )

    def fake_search(query, **kwargs):
        by_query = {
            "q1": [_candidate("qa_1")],
            "q2": [_candidate("qa_wrong")],
            "q3": [_candidate("qa_3", numeric_conflict=True)],
            "q4": [_candidate("qa_4", top1_top2_margin=0.01)],
            "q5": [_candidate("qa_5", hybrid_score=0.2)],
            "q6": [_candidate("qa_7")],
        }
        return by_query[query]

    monkeypatch.setenv("APPROVED_SIMILAR_KEYWORD_WEIGHTS", "original.json")
    profile_path = tmp_path / "weights.json"
    profile_path.write_text("{}", encoding="utf-8")
    output_json = tmp_path / "decision.json"
    output_md = tmp_path / "decision.md"

    report = runner.run_decision_eval(
        cases=cases_path,
        collection="approved_qa_pdf45",
        profile=str(profile_path),
        top_k=2,
        output_json=output_json,
        output_md=output_md,
        search_fn=fake_search,
    )

    assert os.environ["APPROVED_SIMILAR_KEYWORD_WEIGHTS"] == "original.json"
    assert json.loads(output_json.read_text(encoding="utf-8")) == report
    assert "# approved_similar_candidate Decision Eval" in output_md.read_text(encoding="utf-8")

    summary = report["summary"]
    assert summary["total"] == 6
    assert summary["passed"] == 5
    assert summary["failed"] == 1
    assert summary["route_counts"] == {
        "high_confidence_answer": 2,
        "numeric_conflict_blocked": 1,
        "candidate_only": 1,
        "low_confidence_no_answer": 1,
        "ambiguous_multi_topic": 1,
    }
    assert summary["high_confidence_count"] == 2
    assert summary["blocked_or_review_count"] == 2
    assert summary["candidate_only_count"] == 1
    assert summary["low_confidence_count"] == 1
    assert summary["route_counts_by_passed_failed"]["failed"] == {"high_confidence_answer": 1}
    assert summary["failed_high_confidence_cases"][0]["id"] == "fail_high"
    assert summary["passed_blocked_or_reviewed_cases"][0]["id"] == "numeric"
    assert report["per_case"][0]["decision"]["route"] == "high_confidence_answer"
    assert report["per_case"][0]["top_candidate_summary"]["specific_matched_terms"] == ["自由回答"]


def test_decision_eval_safety_highlights_for_numeric_and_negation_routes():
    records = [
        {
            "id": "numeric_bad",
            "category": "numeric",
            "query": "q1",
            "passed": False,
            "actual_top_qa_id": "qa_1",
            "expected_top_qa_id": "qa_2",
            "expected_any_qa_ids": [],
            "numeric_conflict": True,
            "negation_conflict": False,
            "decision": {"route": "candidate_only", "reasons": [], "score_snapshot": {}},
            "top_candidate_summary": {"qa_id": "qa_1"},
        },
        {
            "id": "negation_bad",
            "category": "negation",
            "query": "q2",
            "passed": False,
            "actual_top_qa_id": "qa_3",
            "expected_top_qa_id": "qa_4",
            "expected_any_qa_ids": [],
            "numeric_conflict": False,
            "negation_conflict": True,
            "decision": {"route": "high_confidence_answer", "reasons": [], "score_snapshot": {}},
            "top_candidate_summary": {"qa_id": "qa_3"},
        },
    ]

    summary = runner._summarize(records)

    assert summary["numeric_conflict_not_blocked_cases"][0]["id"] == "numeric_bad"
    assert summary["negation_conflict_not_reviewed_cases"][0]["id"] == "negation_bad"
    assert summary["failed_high_confidence_cases"][0]["id"] == "negation_bad"
