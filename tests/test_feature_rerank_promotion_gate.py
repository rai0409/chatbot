from __future__ import annotations

import json
from pathlib import Path

from eval.feature_rerank_promotion_gate import (
    build_feature_rerank_promotion_decision,
    evaluate_feature_rerank_promotion,
    main,
)


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _comparison(
    *,
    cases: int = 20,
    feature_delta: tuple[int, int, int] = (1, 0, 0),
    combined_delta: tuple[int, int, int] = (1, 0, 0),
    feature_regressions: int = 0,
    combined_regressions: int = 0,
    feature_improvements: int = 2,
    combined_improvements: int = 2,
    negative_expected: int = 0,
) -> dict:
    baseline = {"evaluated_cases": cases, "top1": 10, "top3": 18, "top5": 20}
    feature = {
        "evaluated_cases": cases,
        "top1": baseline["top1"] + feature_delta[0],
        "top3": baseline["top3"] + feature_delta[1],
        "top5": baseline["top5"] + feature_delta[2],
    }
    combined = {
        "evaluated_cases": cases,
        "top1": baseline["top1"] + combined_delta[0],
        "top3": baseline["top3"] + combined_delta[1],
        "top5": baseline["top5"] + combined_delta[2],
    }
    return {
        "metrics": {
            "baseline": baseline,
            "feature_rerank": feature,
            "combined_feedback_then_feature": combined,
        },
        "deltas": {
            "feature_rerank": {
                "top1_delta": feature_delta[0],
                "top3_delta": feature_delta[1],
                "top5_delta": feature_delta[2],
                "improvement_count": feature_improvements,
                "regression_count": feature_regressions,
            },
            "combined_feedback_then_feature": {
                "top1_delta": combined_delta[0],
                "top3_delta": combined_delta[1],
                "top5_delta": combined_delta[2],
                "improvement_count": combined_improvements,
                "regression_count": combined_regressions,
            },
        },
        "feature_specific_metrics": {
            "synonym_boost_case_count": 4,
            "business_term_boost_case_count": 3,
            "negative_mismatch_case_count": 1 if negative_expected else 0,
            "negative_mismatch_demoted_expected_count": negative_expected,
            "negative_mismatch_demoted_non_expected_count": 1,
        },
        "private_payload": {"query": "秘密の質問", "approved_answer": "秘密の回答"},
    }


def _decision(report: dict, mode: str) -> dict:
    for decision in report["decisions"]:
        if decision["mode"] == mode:
            return decision
    raise AssertionError(f"missing decision for {mode}")


def test_missing_input_produces_insufficient_data_and_writes_report(tmp_path):
    output = tmp_path / "decision.json"

    report = build_feature_rerank_promotion_decision(
        input_path=tmp_path / "missing.json",
        output_path=output,
    )

    assert output.exists()
    assert report["summary"]["insufficient_data_count"] == 2
    assert _decision(report, "feature_rerank")["decision"] == "insufficient_data"
    assert "input_missing" in report["warnings"]


def test_malformed_input_produces_safe_warning(tmp_path):
    input_path = tmp_path / "bad.json"
    input_path.write_text("{bad json", encoding="utf-8")

    report = build_feature_rerank_promotion_decision(input_path=input_path, output_path=tmp_path / "decision.json")

    assert report["summary"]["insufficient_data_count"] == 2
    assert report["warnings"] == ["input_malformed"]
    assert "Traceback" not in (tmp_path / "decision.json").read_text(encoding="utf-8")


def test_modes_are_evaluated_independently():
    report = evaluate_feature_rerank_promotion(
        _comparison(feature_delta=(1, 0, 0), combined_delta=(-1, 0, 0))
    )

    assert _decision(report, "feature_rerank")["decision"] == "promote_candidate"
    assert _decision(report, "combined_feedback_then_feature")["decision"] == "blocked"


def test_one_mode_can_promote_while_other_needs_review():
    report = evaluate_feature_rerank_promotion(
        _comparison(feature_regressions=0, combined_regressions=1),
        {"max_regression_count": 1, "max_regression_rate": 0.10},
    )

    assert _decision(report, "feature_rerank")["decision"] == "promote_candidate"
    assert _decision(report, "combined_feedback_then_feature")["decision"] == "needs_review"


def test_no_mode_is_hard_coded_as_winner():
    report = evaluate_feature_rerank_promotion(
        _comparison(feature_delta=(-1, 0, 0), combined_delta=(2, 0, 0))
    )

    assert _decision(report, "feature_rerank")["decision"] == "blocked"
    assert _decision(report, "combined_feedback_then_feature")["decision"] == "promote_candidate"


def test_runtime_and_production_enabled_are_always_false():
    report = evaluate_feature_rerank_promotion(_comparison())

    assert report["runtime_enabled"] is False
    assert report["production_enabled"] is False
    for decision in report["decisions"]:
        assert decision["runtime_enabled"] is False
        assert decision["production_enabled"] is False


def test_negative_topk_delta_blocks_mode():
    report = evaluate_feature_rerank_promotion(_comparison(feature_delta=(1, -1, 0)))

    decision = _decision(report, "feature_rerank")
    assert decision["decision"] == "blocked"
    assert "top3_delta_below_threshold" in decision["reasons"]


def test_excessive_regression_count_blocks_mode():
    report = evaluate_feature_rerank_promotion(_comparison(feature_regressions=1))

    decision = _decision(report, "feature_rerank")
    assert decision["decision"] == "blocked"
    assert "regression_count_above_threshold" in decision["reasons"]


def test_insufficient_case_count_returns_insufficient_data():
    report = evaluate_feature_rerank_promotion(_comparison(cases=3))

    decision = _decision(report, "feature_rerank")
    assert decision["decision"] == "insufficient_data"
    assert "evaluated_cases_below_min_cases" in decision["reasons"]


def test_mixed_improvement_and_nonzero_regression_returns_needs_review():
    report = evaluate_feature_rerank_promotion(
        _comparison(feature_improvements=2, feature_regressions=1),
        {"max_regression_count": 1, "max_regression_rate": 0.10},
    )

    decision = _decision(report, "feature_rerank")
    assert decision["decision"] == "needs_review"
    assert "mixed_improvements_and_regressions" in decision["reasons"]


def test_strong_metrics_can_return_promote_candidate():
    report = evaluate_feature_rerank_promotion(_comparison(cases=40, feature_delta=(3, 1, 0)))

    decision = _decision(report, "feature_rerank")
    assert decision["decision"] == "promote_candidate"
    assert decision["recommended_profile_scope"] in {"pilot_high_accuracy", "production_safe_candidate"}


def test_output_includes_recommended_scope_but_no_production_enablement(tmp_path):
    input_path = _write_json(tmp_path / "comparison.json", _comparison(cases=40, feature_delta=(3, 1, 0)))
    output = tmp_path / "decision.json"

    report = build_feature_rerank_promotion_decision(input_path=input_path, output_path=output)
    text = output.read_text(encoding="utf-8")

    assert _decision(report, "feature_rerank")["recommended_profile_scope"]
    assert "production_enabled" in text
    assert "enable production rerank" not in text.lower()
    assert "runtime_enabled\": true" not in text
    assert "production_enabled\": true" not in text


def test_output_does_not_copy_private_payloads(tmp_path):
    input_path = _write_json(tmp_path / "comparison.json", _comparison())
    output = tmp_path / "decision.json"

    build_feature_rerank_promotion_decision(input_path=input_path, output_path=output)
    text = output.read_text(encoding="utf-8")

    assert "秘密の質問" not in text
    assert "秘密の回答" not in text
    assert "private_payload" not in text


def test_script_does_not_import_or_modify_runtime_or_product_profiles():
    source = Path("eval/feature_rerank_promotion_gate.py").read_text(encoding="utf-8")

    assert "webapi.main" not in source
    assert "product_profile" not in source
    assert "configs/product_profiles" not in source


def test_product_profile_configs_are_not_modified_by_builder(tmp_path):
    profile_paths = sorted(Path("configs/product_profiles").glob("*.json"))
    before = {path.name: path.read_text(encoding="utf-8") for path in profile_paths}

    build_feature_rerank_promotion_decision(
        input_path=_write_json(tmp_path / "comparison.json", _comparison()),
        output_path=tmp_path / "decision.json",
    )

    after = {path.name: path.read_text(encoding="utf-8") for path in profile_paths}
    assert after == before


def test_product_readiness_smoke_script_still_does_not_reference_pr43():
    text = Path("scripts/product_readiness_smoke.sh").read_text(encoding="utf-8")

    assert "feature_rerank_promotion_gate" not in text


def test_cli_writes_requested_output_path(tmp_path):
    input_path = _write_json(tmp_path / "comparison.json", _comparison())
    output = tmp_path / "nested" / "decision.json"

    code = main(["--input", str(input_path), "--output", str(output)])

    assert code == 0
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["evaluated_modes"] == [
        "feature_rerank",
        "combined_feedback_then_feature",
    ]
