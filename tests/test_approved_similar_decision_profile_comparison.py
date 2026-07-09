from __future__ import annotations

import importlib
import json
import os


runner = importlib.import_module("eval.approved_similar_decision_profile_comparison")


def _payload(thresholds):
    is_baseline = thresholds is None
    failed_high = [] if is_baseline else [{"id": "case_risky"}]
    numeric_bad = [] if is_baseline else [{"id": "case_numeric"}]
    negation_bad = [] if is_baseline else [{"id": "case_negation"}]
    passed_blocked = [{"id": "case_reviewed"}]
    return {
        "threshold_info": {
            "threshold_source": "default" if is_baseline else "config_file",
            "threshold_profile_name": None if is_baseline else "test_thresholds",
            "threshold_profile_path": thresholds,
        },
        "summary": {
            "total": 4,
            "passed": 3 if is_baseline else 2,
            "failed": 1 if is_baseline else 2,
            "pass_rate": 0.75 if is_baseline else 0.5,
            "route_counts": {
                "high_confidence_answer": 1,
                "candidate_only": 1,
                "numeric_conflict_blocked": 1,
                "low_confidence_no_answer": 1,
            },
            "high_confidence_count": 1,
            "candidate_only_count": 1,
            "blocked_or_review_count": 1,
            "low_confidence_count": 1,
            "failed_high_confidence_cases": failed_high,
            "numeric_conflict_not_blocked_cases": numeric_bad,
            "negation_conflict_not_reviewed_cases": negation_bad,
            "passed_blocked_or_reviewed_cases": passed_blocked,
        },
    }


def test_compare_decision_threshold_profiles_outputs_reports_and_restores_env(tmp_path, monkeypatch):
    calls = []

    def fake_decision_eval(**kwargs):
        calls.append(kwargs)
        return _payload(kwargs["thresholds"])

    monkeypatch.setenv("APPROVED_SIMILAR_DECISION_THRESHOLDS", "original-thresholds.json")
    profile_path = tmp_path / "thresholds.json"
    profile_path.write_text("{}", encoding="utf-8")
    output_json = tmp_path / "comparison.json"
    output_md = tmp_path / "comparison.md"

    report = runner.compare_decision_threshold_profiles(
        cases="cases.jsonl",
        collection="approved_qa_pdf45",
        keyword_profile="keyword_weights.json",
        threshold_profiles=["no_thresholds", str(profile_path)],
        top_k=5,
        output_json=output_json,
        output_md=output_md,
        decision_eval_fn=fake_decision_eval,
    )

    assert os.environ["APPROVED_SIMILAR_DECISION_THRESHOLDS"] == "original-thresholds.json"
    assert [call["thresholds"] for call in calls] == [None, str(profile_path)]
    assert all(call["profile"] == "keyword_weights.json" for call in calls)
    assert json.loads(output_json.read_text(encoding="utf-8")) == report
    md = output_md.read_text(encoding="utf-8")
    assert "# approved_similar_candidate Decision Threshold Profile Comparison" in md
    assert "## Failed High Confidence Cases" in md
    assert "case_risky" in md

    baseline = report["threshold_profiles"][0]
    configured = report["threshold_profiles"][1]
    assert baseline["name"] == "no_thresholds"
    assert baseline["threshold_source"] == "default"
    assert baseline["passed"] == 3
    assert baseline["failed_high_confidence_count"] == 0
    assert baseline["passed_blocked_or_reviewed_count"] == 1
    assert configured["name"] == "thresholds"
    assert configured["threshold_source"] == "config_file"
    assert configured["failed_high_confidence_ids"] == ["case_risky"]
    assert configured["numeric_conflict_not_blocked_ids"] == ["case_numeric"]
    assert configured["negation_conflict_not_reviewed_ids"] == ["case_negation"]


def test_compare_decision_threshold_profiles_requires_profiles():
    try:
        runner.compare_decision_threshold_profiles(
            cases="cases.jsonl",
            collection=None,
            keyword_profile=None,
            threshold_profiles=[],
            decision_eval_fn=lambda **kwargs: {},
        )
    except ValueError as exc:
        assert "at least one threshold profile" in str(exc)
    else:
        raise AssertionError("expected ValueError")
