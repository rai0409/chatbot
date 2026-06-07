from __future__ import annotations

import json
from pathlib import Path

from eval.feature_rerank_eval import evaluate_feature_rerank, main


def _write_jsonl(path: Path, rows: list[dict], *, malformed: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if malformed:
        lines.append("{not json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _feedback_profile(path: Path, adjustments: dict | None = None, **overrides) -> Path:
    payload = {
        "profile_name": "feedback_preview",
        "profile_type": "approved_similar_feedback_rerank",
        "runtime_enabled": False,
        "production_enabled": False,
        "candidate_adjustments": adjustments or {},
        "safety": {
            "no_runtime_ranking_change": True,
            "no_auto_answer_enablement": True,
            "requires_offline_evaluation_before_production": True,
        },
    }
    payload.update(overrides)
    return _write_json(path, payload)


def _feature_profile(path: Path, **overrides) -> Path:
    payload = {
        "profile_name": "approved_similar_feature_preview",
        "profile_type": "approved_similar_feature_rerank",
        "runtime_enabled": False,
        "production_enabled": False,
        "weights": {
            "base_score": 1.0,
            "synonym_overlap": 0.08,
            "business_term_overlap": 0.10,
            "negative_mismatch_penalty": 0.18,
        },
        "limits": {"max_positive_adjustment": 0.12, "max_negative_penalty": 0.20},
        "safety": {
            "no_runtime_ranking_change": True,
            "no_auto_answer_enablement": True,
            "requires_offline_evaluation_before_production": True,
        },
    }
    payload.update(overrides)
    return _write_json(path, payload)


def _synonyms(path: Path) -> Path:
    return _write_json(
        path,
        {
            "synonym_groups": [
                {
                    "canonical": "健康保険証",
                    "terms": ["保険証", "被保険者証", "健康保険被保険者証"],
                },
                {"canonical": "育児休業", "terms": ["育休", "育児休暇"]},
                {"canonical": "扶養", "terms": ["被扶養者", "扶養に入る", "扶養から外れる"]},
            ],
            "negative_mismatch_pairs": [
                {"left": ["扶養に入る", "加入"], "right": ["扶養から外れる", "脱退"]},
            ],
        },
    )


def _paths(tmp_path: Path):
    return {
        "feedback": _feedback_profile(tmp_path / "feedback.json"),
        "feature": _feature_profile(tmp_path / "feature.json"),
        "synonyms": _synonyms(tmp_path / "synonyms.json"),
        "output": tmp_path / "out.json",
    }


def _case(**overrides) -> dict:
    base = {
        "case_id": "case-1",
        "query": "保険証をなくした",
        "expected_qa_id": "qa-expected",
        "candidates": [
            {"qa_id": "qa-generic", "question_text": "一般的な申請", "scores": {"weighted_score": 0.56}},
            {"qa_id": "qa-expected", "question_text": "健康保険被保険者証の再発行", "scores": {"weighted_score": 0.50}},
        ],
        "approved_answer": "出力してはいけない承認済み回答",
        "comment": "出力してはいけないコメント",
        "private_chunks": [{"text": "秘密のチャンク"}],
    }
    base.update(overrides)
    return base


def _eval(tmp_path: Path, cases: list[dict], **profile_overrides):
    paths = _paths(tmp_path)
    if "feedback" in profile_overrides:
        paths["feedback"] = profile_overrides["feedback"]
    if "feature" in profile_overrides:
        paths["feature"] = profile_overrides["feature"]
    if "synonyms" in profile_overrides:
        paths["synonyms"] = profile_overrides["synonyms"]
    case_path = _write_jsonl(tmp_path / "cases.jsonl", cases, malformed=profile_overrides.get("malformed", False))
    return evaluate_feature_rerank(
        cases_path=case_path,
        feedback_profile_path=paths["feedback"],
        feature_profile_path=paths["feature"],
        synonyms_path=paths["synonyms"],
        output=paths["output"],
    )


def test_missing_cases_file_creates_safe_empty_comparison_output(tmp_path):
    paths = _paths(tmp_path)

    report = evaluate_feature_rerank(
        cases_path=tmp_path / "missing.jsonl",
        feedback_profile_path=paths["feedback"],
        feature_profile_path=paths["feature"],
        synonyms_path=paths["synonyms"],
        output=paths["output"],
    )

    assert report["metrics"]["baseline"]["evaluated_cases"] == 0
    assert str(tmp_path / "missing.jsonl") in report["data_quality"]["missing_input_files"]


def test_missing_feedback_profile_still_evaluates_baseline_and_feature(tmp_path):
    paths = _paths(tmp_path)
    missing_feedback = tmp_path / "missing_feedback.json"
    report = evaluate_feature_rerank(
        cases_path=_write_jsonl(tmp_path / "cases.jsonl", [_case()]),
        feedback_profile_path=missing_feedback,
        feature_profile_path=paths["feature"],
        synonyms_path=paths["synonyms"],
        output=paths["output"],
    )

    assert report["profile_info"]["feedback_preview"]["loaded"] is False
    assert report["profile_info"]["feature_rerank"]["valid"] is True
    assert report["metrics"]["baseline"]["evaluated_cases"] == 1
    assert report["metrics"]["feature_rerank"]["top1"] == 1


def test_missing_feature_profile_still_evaluates_baseline_and_feedback(tmp_path):
    paths = _paths(tmp_path)
    feedback = _feedback_profile(
        tmp_path / "feedback.json",
        {"qa-expected": {"score_adjustment": 0.08}},
    )
    report = evaluate_feature_rerank(
        cases_path=_write_jsonl(tmp_path / "cases.jsonl", [_case()]),
        feedback_profile_path=feedback,
        feature_profile_path=tmp_path / "missing_feature.json",
        synonyms_path=paths["synonyms"],
        output=paths["output"],
    )

    assert report["profile_info"]["feature_rerank"]["loaded"] is False
    assert report["metrics"]["feedback_preview"]["top1"] == 1
    assert report["metrics"]["feature_rerank"]["top1"] == 0


def test_invalid_profile_is_rejected_safely(tmp_path):
    invalid_feature = _feature_profile(tmp_path / "invalid_feature.json", production_enabled=True)
    report = _eval(tmp_path, [_case()], feature=invalid_feature)

    assert report["profile_info"]["feature_rerank"]["valid"] is False
    assert report["metrics"]["feature_rerank"]["top1"] == report["metrics"]["baseline"]["top1"]


def test_baseline_top1_top3_top5_are_computed(tmp_path):
    report = _eval(
        tmp_path,
        [
            _case(case_id="top1", expected_qa_id="qa-generic"),
            _case(case_id="top3", expected_qa_id="qa-expected"),
            _case(
                case_id="top5",
                query="その他",
                expected_qa_id="qa-fifth",
                candidates=[
                    {"qa_id": f"qa-{idx}", "score": 1.0 - idx / 10}
                    for idx in range(4)
                ]
                + [{"qa_id": "qa-fifth", "score": 0.1}],
            ),
        ],
    )

    assert report["metrics"]["baseline"]["top1"] == 1
    assert report["metrics"]["baseline"]["top3"] == 2
    assert report["metrics"]["baseline"]["top5"] == 3


def test_feature_rerank_improves_rank_for_synonym_business_term_match(tmp_path):
    report = _eval(tmp_path, [_case()])

    assert report["metrics"]["baseline"]["top1"] == 0
    assert report["metrics"]["feature_rerank"]["top1"] == 1
    assert report["deltas"]["feature_rerank"]["top1_delta"] == 1
    assert report["deltas"]["feature_rerank"]["improvement_count"] == 1
    assert report["feature_specific_metrics"]["synonym_boost_case_count"] == 1
    assert report["feature_specific_metrics"]["business_term_boost_case_count"] == 1


def test_feature_rerank_penalizes_negative_mismatch(tmp_path):
    report = _eval(
        tmp_path,
        [
            _case(
                case_id="negative",
                query="扶養に入るには",
                expected_qa_id="qa-safe",
                candidates=[
                    {"qa_id": "qa-opposite", "question_text": "扶養から外れる手続き", "score": 0.90},
                    {"qa_id": "qa-safe", "question_text": "被扶養者として扶養に入る手続き", "score": 0.80},
                ],
            )
        ],
    )

    assert report["metrics"]["feature_rerank"]["top1"] == 1
    assert report["feature_specific_metrics"]["negative_mismatch_case_count"] == 1
    assert report["feature_specific_metrics"]["negative_mismatch_demoted_non_expected_count"] == 1


def test_negative_mismatch_demoted_expected_is_counted(tmp_path):
    report = _eval(
        tmp_path,
        [
            _case(
                case_id="negative-expected",
                query="扶養に入るには",
                expected_qa_id="qa-opposite",
                candidates=[
                    {"qa_id": "qa-opposite", "question_text": "扶養から外れる手続き", "score": 0.90},
                    {"qa_id": "qa-safe", "question_text": "一般的な手続き", "score": 0.85},
                ],
            )
        ],
    )

    assert report["feature_specific_metrics"]["negative_mismatch_demoted_expected_count"] == 1
    assert report["regressions"][0]["case_id"] == "negative-expected"


def test_combined_mode_applies_feedback_then_feature_deterministically(tmp_path):
    feedback = _feedback_profile(
        tmp_path / "custom_feedback.json",
        {"qa-feedback": {"score_adjustment": 0.09}},
    )
    report = _eval(
        tmp_path,
        [
            _case(
                case_id="combined",
                query="保険証をなくした",
                expected_qa_id="qa-expected",
                candidates=[
                    {"qa_id": "qa-feedback", "question_text": "一般的な申請", "score": 0.52},
                    {"qa_id": "qa-expected", "question_text": "健康保険被保険者証の再発行", "score": 0.50},
                ],
            )
        ],
        feedback=feedback,
    )
    case = report["cases"][0]

    assert case["feedback_preview_top_candidate_id"] == "qa-feedback"
    assert case["combined_top_candidate_id"] == "qa-expected"
    assert case["adjusted_candidate_ids"]["combined_feedback_then_feature"] == ["qa-expected", "qa-feedback"]


def test_no_numeric_score_preserves_original_order_where_appropriate(tmp_path):
    report = _eval(
        tmp_path,
        [
            _case(
                expected_qa_id="qa-expected",
                candidates=[
                    {"qa_id": "qa-first", "question_text": "一般的な申請"},
                    {"qa_id": "qa-expected", "question_text": "健康保険被保険者証の再発行"},
                ],
            )
        ],
    )

    case = report["cases"][0]
    assert case["baseline_top_candidate_id"] == "qa-first"
    assert case["feature_rerank_top_candidate_id"] == "qa-first"
    assert case["feature_rerank_rank"] == 2


def test_malformed_jsonl_is_skipped_and_counted(tmp_path):
    paths = _paths(tmp_path)
    case_path = _write_jsonl(tmp_path / "cases.jsonl", [_case()], malformed=True)

    report = evaluate_feature_rerank(
        cases_path=case_path,
        feedback_profile_path=paths["feedback"],
        feature_profile_path=paths["feature"],
        synonyms_path=paths["synonyms"],
        output=paths["output"],
    )

    assert report["data_quality"]["skipped_malformed_lines"] == 1
    assert report["metrics"]["baseline"]["evaluated_cases"] == 1


def test_flexible_field_names_are_parsed(tmp_path):
    report = _eval(
        tmp_path,
        [
            {
                "id": "flex",
                "user_query": "保険証",
                "expected": "qa-expected",
                "candidates": [
                    {"candidate_id": "qa-other", "question": "一般", "scores": {"final_score": 0.56}},
                    {"id": "qa-expected", "question_text": "健康保険被保険者証", "similarity": 0.50},
                ],
            }
        ],
    )

    assert report["cases"][0]["case_id"] == "flex"
    assert report["cases"][0]["feature_rerank_top_candidate_id"] == "qa-expected"


def test_output_excludes_full_answers_comments_private_chunks_and_candidate_payloads(tmp_path):
    paths = _paths(tmp_path)
    case_path = _write_jsonl(
        tmp_path / "cases.jsonl",
        [
            _case(
                query="長い質問" * 100,
                candidates=[
                    {
                        "qa_id": "qa-expected",
                        "question_text": "健康保険被保険者証",
                        "approved_answer_preview": "短いプレビュー",
                        "approved_answer": "秘密の承認済み回答",
                        "comment": "秘密のコメント",
                        "private_chunks": [{"text": "秘密のチャンク"}],
                        "score": 0.5,
                    }
                ],
            )
        ],
    )

    evaluate_feature_rerank(
        cases_path=case_path,
        feedback_profile_path=paths["feedback"],
        feature_profile_path=paths["feature"],
        synonyms_path=paths["synonyms"],
        output=paths["output"],
    )
    text = paths["output"].read_text(encoding="utf-8")
    report = json.loads(text)

    assert "秘密の承認済み回答" not in text
    assert "秘密のコメント" not in text
    assert "秘密のチャンク" not in text
    assert "candidates" not in report["cases"][0]
    assert len(report["cases"][0]["query"]) <= 180


def test_cli_writes_requested_output_path(tmp_path):
    paths = _paths(tmp_path)
    cases = _write_jsonl(tmp_path / "cases.jsonl", [_case()])
    output = tmp_path / "nested" / "comparison.json"

    code = main(
        [
            "--cases",
            str(cases),
            "--feedback-profile",
            str(paths["feedback"]),
            "--feature-profile",
            str(paths["feature"]),
            "--synonyms",
            str(paths["synonyms"]),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["metrics"]["feature_rerank"]["top1"] == 1
