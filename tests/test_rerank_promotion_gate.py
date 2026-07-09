from __future__ import annotations

import json
from pathlib import Path

from eval.rerank_promotion_gate import evaluate_rerank_promotion_gate, main


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _comparison(path: Path, **metrics) -> Path:
    base = {
        "evaluated_cases": 3,
        "baseline_top1": 1,
        "preview_top1": 2,
        "top1_delta": 1,
        "baseline_top3": 3,
        "preview_top3": 3,
        "top3_delta": 0,
        "baseline_top5": 3,
        "preview_top5": 3,
        "top5_delta": 0,
        "improvement_count": 1,
        "regression_count": 0,
        "missing_expected_count": 0,
    }
    base.update(metrics)
    return _write_json(
        path,
        {
            "metrics": base,
            "improvements": [{"query": "出力しない質問本文", "approved_answer": "秘密"}],
        },
    )


def _profile(path: Path, **overrides) -> Path:
    payload = {
        "profile_name": "feedback_preview",
        "profile_type": "approved_similar_feedback_rerank",
        "production_enabled": False,
        "runtime_enabled": False,
        "candidate_adjustments": {"qa-1": {"score_adjustment": 0.03}},
        "safety": {
            "no_runtime_ranking_change": True,
            "no_auto_answer_enablement": True,
            "requires_offline_evaluation_before_production": True,
        },
    }
    payload.update(overrides)
    return _write_json(path, payload)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_missing_comparison_returns_insufficient_data(tmp_path):
    profile = _profile(tmp_path / "profile.json")
    output = tmp_path / "decision.json"

    report = evaluate_rerank_promotion_gate(
        comparison_path=tmp_path / "missing.json",
        profile_path=profile,
        output=output,
    )

    assert report["decision"] == "insufficient_data"
    assert report["safe_to_promote"] is False
    assert "comparison_file_missing" in report["reasons"]
    assert str(tmp_path / "missing.json") in report["data_quality"]["missing_input_files"]


def test_missing_profile_returns_insufficient_data_with_clear_reason(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json")

    report = evaluate_rerank_promotion_gate(
        comparison_path=comparison,
        profile_path=tmp_path / "missing_profile.json",
        output=tmp_path / "decision.json",
    )

    assert report["decision"] == "insufficient_data"
    assert "profile_file_missing" in report["reasons"]
    assert report["profile_info"]["loaded"] is False


def test_invalid_json_profile_is_blocked_or_invalid_without_private_payload(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json")
    profile = tmp_path / "profile.json"
    profile.write_text("{not json", encoding="utf-8")

    report = evaluate_rerank_promotion_gate(
        comparison_path=comparison,
        profile_path=profile,
        output=tmp_path / "decision.json",
    )

    assert report["decision"] == "blocked"
    assert report["profile_info"]["reason"] == "invalid_json"
    assert "profile_invalid:invalid_json" in report["reasons"]


def test_production_enabled_true_profile_is_blocked(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json")
    profile = _profile(tmp_path / "profile.json", production_enabled=True)

    report = evaluate_rerank_promotion_gate(
        comparison_path=comparison,
        profile_path=profile,
        output=tmp_path / "decision.json",
    )

    assert report["decision"] == "blocked"
    assert report["profile_info"]["valid"] is False
    assert "profile_invalid:production_enabled_must_be_false" in report["reasons"]


def test_zero_evaluated_cases_returns_insufficient_data(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json", evaluated_cases=0)
    profile = _profile(tmp_path / "profile.json")

    report = evaluate_rerank_promotion_gate(
        comparison_path=comparison,
        profile_path=profile,
        output=tmp_path / "decision.json",
    )

    assert report["decision"] == "insufficient_data"
    assert "no_evaluated_cases" in report["reasons"]


def test_top3_regression_blocks(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json", baseline_top3=3, preview_top3=2)
    profile = _profile(tmp_path / "profile.json")

    report = evaluate_rerank_promotion_gate(
        comparison_path=comparison,
        profile_path=profile,
        output=tmp_path / "decision.json",
    )

    assert report["decision"] == "blocked"
    assert "top3_regression" in report["reasons"]


def test_top5_regression_blocks(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json", baseline_top5=3, preview_top5=2)
    profile = _profile(tmp_path / "profile.json")

    report = evaluate_rerank_promotion_gate(
        comparison_path=comparison,
        profile_path=profile,
        output=tmp_path / "decision.json",
    )

    assert report["decision"] == "blocked"
    assert "top5_regression" in report["reasons"]


def test_regression_count_above_threshold_blocks(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json", regression_count=2)
    profile = _profile(tmp_path / "profile.json")

    report = evaluate_rerank_promotion_gate(
        comparison_path=comparison,
        profile_path=profile,
        output=tmp_path / "decision.json",
    )

    assert report["decision"] == "blocked"
    assert "regression_count_above_threshold" in report["reasons"]


def test_top1_delta_below_threshold_blocks(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json", top1_delta=-1)
    profile = _profile(tmp_path / "profile.json")

    report = evaluate_rerank_promotion_gate(
        comparison_path=comparison,
        profile_path=profile,
        output=tmp_path / "decision.json",
    )

    assert report["decision"] == "blocked"
    assert "top1_delta_below_threshold" in report["reasons"]


def test_missing_expected_above_threshold_blocks(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json", missing_expected_count=1)
    profile = _profile(tmp_path / "profile.json")

    report = evaluate_rerank_promotion_gate(
        comparison_path=comparison,
        profile_path=profile,
        output=tmp_path / "decision.json",
    )

    assert report["decision"] == "blocked"
    assert "missing_expected_count_above_threshold" in report["reasons"]


def test_valid_non_regressing_metrics_produce_promote_candidate(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json")
    profile = _profile(tmp_path / "profile.json")

    report = evaluate_rerank_promotion_gate(
        comparison_path=comparison,
        profile_path=profile,
        output=tmp_path / "decision.json",
    )

    assert report["decision"] == "promote_candidate"
    assert report["safe_to_promote"] is True
    assert report["reasons"] == ["promotion_gate_passed"]


def test_cli_threshold_overrides_work(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json", regression_count=2)
    profile = _profile(tmp_path / "profile.json")
    output = tmp_path / "nested" / "decision.json"

    code = main(
        [
            "--comparison",
            str(comparison),
            "--profile",
            str(profile),
            "--output",
            str(output),
            "--max-allowed-regressions",
            "2",
            "--min-top1-delta",
            "1",
        ]
    )

    report = _load(output)
    assert code == 0
    assert report["decision"] == "promote_candidate"
    assert report["thresholds"]["max_allowed_regressions"] == 2
    assert report["thresholds"]["min_top1_delta"] == 1


def test_output_excludes_queries_answers_candidates_comments_and_private_chunks(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json")
    raw = _load(comparison)
    raw["query"] = "秘密の質問"
    raw["approved_answer"] = "秘密の承認済み回答"
    raw["candidate_payload"] = {"qa_id": "qa-secret"}
    raw["comment"] = "秘密のコメント"
    raw["private_chunks"] = [{"text": "秘密のチャンク"}]
    _write_json(comparison, raw)
    profile = _profile(tmp_path / "profile.json")
    output = tmp_path / "decision.json"

    evaluate_rerank_promotion_gate(
        comparison_path=comparison,
        profile_path=profile,
        output=output,
    )
    text = output.read_text(encoding="utf-8")

    assert "秘密の質問" not in text
    assert "秘密の承認済み回答" not in text
    assert "qa-secret" not in text
    assert "秘密のコメント" not in text
    assert "秘密のチャンク" not in text


def test_cli_writes_requested_output_path(tmp_path):
    comparison = _comparison(tmp_path / "comparison.json")
    profile = _profile(tmp_path / "profile.json")
    output = tmp_path / "decision.json"

    code = main(["--comparison", str(comparison), "--profile", str(profile), "--output", str(output)])

    assert code == 0
    assert output.exists()
    assert _load(output)["safe_to_promote"] is True
