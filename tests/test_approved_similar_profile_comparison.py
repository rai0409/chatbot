from __future__ import annotations

import importlib
import json
import os


comparison = importlib.import_module("eval.approved_similar_profile_comparison")


def _payload(profile_name: str):
    weighted = 0.7 if profile_name == "no_profile" else 0.9
    passed_second = profile_name != "no_profile"
    per_case = [
        {
            "id": "case_pass",
            "category": "direct",
            "query": "q1",
            "ambiguous": False,
            "expected_top_qa_id": "qa_1",
            "expected_any_qa_ids": [],
            "actual_top_qa_id": "qa_1",
            "top_answer_preview": "answer 1",
            "passed": True,
            "failure_reasons": [],
            "top_candidate": {
                "hybrid_score": 0.8,
                "semantic_score": 0.6,
                "keyword_score": 0.5,
                "weighted_keyword_score": weighted,
                "top1_top2_margin": 0.1,
                "generic_matched_terms": ["アンケート"],
                "specific_matched_terms": ["自由回答"],
            },
        },
        {
            "id": "case_ambiguous",
            "category": "multi",
            "query": "q2",
            "ambiguous": True,
            "expected_top_qa_id": "qa_2",
            "expected_any_qa_ids": ["qa_2", "qa_3"],
            "actual_top_qa_id": "qa_3" if passed_second else "qa_bad",
            "top_answer_preview": "answer 2",
            "passed": passed_second,
            "failure_reasons": [] if passed_second else ["expected top qa_id qa_2, got qa_bad"],
            "top_candidate": {
                "hybrid_score": 0.4,
                "semantic_score": 0.2,
                "keyword_score": 0.3,
                "weighted_keyword_score": weighted - 0.2,
                "top1_top2_margin": None,
                "generic_matched_terms": ["質問"],
                "specific_matched_terms": ["個人情報"],
            },
        },
    ]
    passed = sum(1 for case in per_case if case["passed"])
    return {
        "total": 2,
        "passed": passed,
        "failed": 2 - passed,
        "pass_rate": passed / 2,
        "per_case": per_case,
    }


def test_compare_profiles_generates_json_and_markdown_reports(tmp_path, monkeypatch):
    calls = []

    def fake_eval(**kwargs):
        profile = os.environ.get("APPROVED_SIMILAR_KEYWORD_WEIGHTS") or "no_profile"
        calls.append({"profile": profile, **kwargs})
        return _payload(profile)

    monkeypatch.setenv("APPROVED_SIMILAR_KEYWORD_WEIGHTS", "original-profile.json")
    profile_path = tmp_path / "weights.json"
    profile_path.write_text("{}", encoding="utf-8")
    output_json = tmp_path / "comparison.json"
    output_md = tmp_path / "comparison.md"

    report = comparison.compare_profiles(
        cases="cases.jsonl",
        collection="approved_qa_pdf45",
        profiles=["no_profile", str(profile_path)],
        output_json=output_json,
        output_md=output_md,
        top_k=3,
        eval_fn=fake_eval,
    )

    assert os.environ["APPROVED_SIMILAR_KEYWORD_WEIGHTS"] == "original-profile.json"
    assert [call["profile"] for call in calls] == ["no_profile", str(profile_path)]
    assert output_json.exists()
    assert output_md.exists()
    assert json.loads(output_json.read_text(encoding="utf-8")) == report
    assert "# approved_similar_candidate Profile Comparison" in output_md.read_text(encoding="utf-8")

    baseline = report["profiles"][0]
    weighted = report["profiles"][1]
    assert baseline["name"] == "no_profile"
    assert baseline["passed"] == 1
    assert baseline["failed_case_ids"] == ["case_ambiguous"]
    assert baseline["ambiguous_case_results"][0]["id"] == "case_ambiguous"
    assert baseline["average_hybrid_score"] == 0.6
    assert baseline["average_semantic_score"] == 0.4
    assert baseline["average_keyword_score"] == 0.4
    assert baseline["average_weighted_keyword_score"] == 0.6
    assert baseline["average_top1_top2_margin"] == 0.1
    assert baseline["failure_details"][0]["top_answer_preview"] == "answer 2"
    assert baseline["failure_details"][0]["generic_matched_terms"] == ["質問"]
    assert baseline["failure_details"][0]["specific_matched_terms"] == ["個人情報"]
    assert weighted["passed"] == 2


def test_compare_profiles_requires_at_least_one_profile():
    try:
        comparison.compare_profiles(
            cases="cases.jsonl",
            collection=None,
            profiles=[],
            eval_fn=lambda **kwargs: {},
        )
    except ValueError as exc:
        assert "at least one profile" in str(exc)
    else:
        raise AssertionError("expected ValueError")
