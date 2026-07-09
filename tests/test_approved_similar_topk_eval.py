from __future__ import annotations

import importlib
import json
import os
from pathlib import Path


runner = importlib.import_module("eval.approved_similar_topk_eval")


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
        "top1_top2_margin": 0.1,
        "generic_matched_terms": ["設問"],
        "specific_matched_terms": ["自由回答"],
        "matched_terms": ["設問", "自由回答"],
    }
    data.update(overrides)
    return data


def test_topk_eval_metrics_mrr_and_outputs(tmp_path, monkeypatch):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {"id": "rank1", "category": "direct", "query": "q1", "expected_top_qa_id": "qa_1"},
            {"id": "rank2", "category": "rerank", "query": "q2", "expected_top_qa_id": "qa_2"},
            {
                "id": "rank4_any",
                "category": "ambiguous",
                "query": "q3",
                "expected_top_qa_id": "qa_3",
                "expected_any_qa_ids": ["qa_3", "qa_33"],
                "ambiguous": True,
            },
            {"id": "miss", "category": "miss", "query": "q4", "expected_top_qa_id": "qa_4"},
        ],
    )

    def fake_search(query, **kwargs):
        by_query = {
            "q1": [_candidate("qa_1"), _candidate("qa_x"), _candidate("qa_y"), _candidate("qa_z")],
            "q2": [_candidate("qa_x"), _candidate("qa_2"), _candidate("qa_y"), _candidate("qa_z")],
            "q3": [_candidate("qa_x"), _candidate("qa_y"), _candidate("qa_z"), _candidate("qa_33")],
            "q4": [_candidate("qa_x"), _candidate("qa_y"), _candidate("qa_z"), _candidate("qa_w")],
        }
        return by_query[query][: kwargs["top_k"]]

    monkeypatch.setenv("APPROVED_SIMILAR_KEYWORD_WEIGHTS", "original.json")
    profile_path = tmp_path / "weights.json"
    profile_path.write_text("{}", encoding="utf-8")
    output_json = tmp_path / "topk.json"
    output_md = tmp_path / "topk.md"

    report = runner.run_topk_eval(
        cases=cases_path,
        collection="approved_qa_pdf45",
        profile=str(profile_path),
        top_k=5,
        output_json=output_json,
        output_md=output_md,
        search_fn=fake_search,
    )

    assert os.environ["APPROVED_SIMILAR_KEYWORD_WEIGHTS"] == "original.json"
    assert json.loads(output_json.read_text(encoding="utf-8")) == report
    md = output_md.read_text(encoding="utf-8")
    assert "# approved_similar_candidate Top-K Eval" in md
    assert "rank2" in md
    assert "miss" in md

    summary = report["summary"]
    assert summary["total"] == 4
    assert summary["top1_hits"] == 1
    assert summary["top3_hits"] == 2
    assert summary["top5_hits"] == 3
    assert summary["top1_accuracy"] == 0.25
    assert summary["top3_recall"] == 0.5
    assert summary["top5_recall"] == 0.75
    assert summary["mrr"] == (1.0 + 0.5 + 0.25) / 4
    assert summary["failed_top1_but_found_in_top3"] == ["rank2"]
    assert summary["failed_top1_but_found_in_top5"] == ["rank2", "rank4_any"]
    assert summary["missed_all_top_k_case_ids"] == ["miss"]
    assert summary["ambiguous_case_summary"] == {
        "total": 1,
        "top1_hits": 0,
        "top3_hits": 0,
        "top5_hits": 1,
        "missed_top_k": 0,
    }

    by_id = {record["id"]: record for record in report["per_case"]}
    assert by_id["rank1"]["correct_rank"] == 1
    assert by_id["rank2"]["correct_rank"] == 2
    assert by_id["rank4_any"]["correct_rank"] == 4
    assert by_id["rank4_any"]["found_in_top5"] is True
    assert by_id["miss"]["correct_rank"] is None
    assert by_id["rank1"]["top_candidate_scores"]["hybrid_score"] == 0.9
    assert by_id["rank1"]["top_candidate_summary"]["qa_id"] == "qa_1"


def test_topk_eval_rejects_invalid_top_k(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(cases_path, [{"id": "c1", "query": "q", "expected_top_qa_id": "qa"}])

    try:
        runner.run_topk_eval(cases=cases_path, top_k=0, search_fn=lambda **kwargs: [])
    except ValueError as exc:
        assert "top_k must be >= 1" in str(exc)
    else:
        raise AssertionError("expected ValueError")
