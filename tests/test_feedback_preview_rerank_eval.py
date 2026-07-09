from __future__ import annotations

import json
from pathlib import Path

from eval.feedback_preview_rerank_eval import evaluate_feedback_preview_rerank, main


def _write_jsonl(path: Path, rows: list[dict], *, malformed: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if malformed:
        lines.append("{not json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _profile(path: Path, adjustments: dict, **overrides) -> Path:
    payload = {
        "profile_name": "feedback_preview",
        "profile_type": "approved_similar_feedback_rerank",
        "production_enabled": False,
        "candidate_adjustments": adjustments,
        "safety": {"no_runtime_ranking_change": True},
    }
    payload.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _case(**overrides) -> dict:
    base = {
        "case_id": "case-1",
        "query": "これは出力されても短く丸められる質問です",
        "expected_qa_id": "qa-expected",
        "candidates": [
            {"qa_id": "qa-top", "scores": {"weighted_score": 0.9}},
            {"qa_id": "qa-expected", "scores": {"weighted_score": 0.8}},
            {"qa_id": "qa-other", "scores": {"weighted_score": 0.7}},
        ],
        "approved_answer": "出力してはいけない承認済み回答",
        "comment": "出力してはいけないコメント",
        "private_chunks": [{"text": "秘密のチャンク"}],
    }
    base.update(overrides)
    return base


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_missing_cases_file_creates_safe_empty_comparison_output(tmp_path):
    output = tmp_path / "out.json"
    profile = _profile(tmp_path / "profile.json", {})

    report = evaluate_feedback_preview_rerank(
        cases_path=tmp_path / "missing.jsonl",
        profile_path=profile,
        output=output,
    )

    assert output.exists()
    assert report["metrics"]["total_cases"] == 0
    assert report["metrics"]["evaluated_cases"] == 0
    assert str(tmp_path / "missing.jsonl") in report["data_quality"]["missing_input_files"]


def test_missing_profile_creates_baseline_only_comparison_output(tmp_path):
    cases = tmp_path / "cases.jsonl"
    output = tmp_path / "out.json"
    _write_jsonl(cases, [_case()])

    report = evaluate_feedback_preview_rerank(
        cases_path=cases,
        profile_path=tmp_path / "missing_profile.json",
        output=output,
    )

    assert report["profile_info"]["loaded"] is False
    assert report["profile_info"]["valid"] is False
    assert report["metrics"]["baseline_top1"] == 0
    assert report["metrics"]["preview_top1"] == 0
    assert report["unchanged"][0]["case_id"] == "case-1"


def test_invalid_profile_is_rejected_safely(tmp_path):
    cases = tmp_path / "cases.jsonl"
    output = tmp_path / "out.json"
    profile = _profile(
        tmp_path / "profile.json",
        {"qa-expected": {"score_adjustment": 1.0}},
        production_enabled=True,
    )
    _write_jsonl(cases, [_case()])

    report = evaluate_feedback_preview_rerank(cases_path=cases, profile_path=profile, output=output)

    assert report["profile_info"]["loaded"] is True
    assert report["profile_info"]["valid"] is False
    assert report["profile_info"]["reason"] == "production_enabled_must_be_false"
    assert report["metrics"]["preview_top1"] == report["metrics"]["baseline_top1"]


def test_baseline_top1_top3_top5_are_computed(tmp_path):
    cases = tmp_path / "cases.jsonl"
    output = tmp_path / "out.json"
    profile = _profile(tmp_path / "profile.json", {})
    _write_jsonl(
        cases,
        [
            _case(case_id="top1", expected_qa_id="qa-top"),
            _case(case_id="top3", expected_qa_id="qa-expected"),
            _case(
                case_id="top5",
                expected_qa_id="qa-fifth",
                candidates=[
                    {"qa_id": f"qa-{idx}", "score": 1.0 - idx / 10}
                    for idx in range(4)
                ]
                + [{"qa_id": "qa-fifth", "score": 0.1}],
            ),
        ],
    )

    report = evaluate_feedback_preview_rerank(cases_path=cases, profile_path=profile, output=output)

    assert report["metrics"]["evaluated_cases"] == 3
    assert report["metrics"]["baseline_top1"] == 1
    assert report["metrics"]["baseline_top3"] == 2
    assert report["metrics"]["baseline_top5"] == 3


def test_preview_positive_adjustment_can_improve_rank(tmp_path):
    cases = tmp_path / "cases.jsonl"
    output = tmp_path / "out.json"
    profile = _profile(
        tmp_path / "profile.json",
        {"qa-expected": {"score_adjustment": 0.2}},
    )
    _write_jsonl(cases, [_case()])

    report = evaluate_feedback_preview_rerank(cases_path=cases, profile_path=profile, output=output)

    assert report["metrics"]["baseline_top1"] == 0
    assert report["metrics"]["preview_top1"] == 1
    assert report["metrics"]["top1_delta"] == 1
    assert report["metrics"]["improvement_count"] == 1
    assert report["improvements"][0]["baseline_rank"] == 2
    assert report["improvements"][0]["preview_rank"] == 1
    assert report["improvements"][0]["adjusted_candidate_ids"] == ["qa-expected"]


def test_preview_negative_adjustment_can_regress_rank(tmp_path):
    cases = tmp_path / "cases.jsonl"
    output = tmp_path / "out.json"
    profile = _profile(
        tmp_path / "profile.json",
        {"qa-expected": {"score_adjustment": -0.3}},
    )
    _write_jsonl(cases, [_case(expected_qa_id="qa-expected", candidates=[
        {"qa_id": "qa-expected", "weighted_score": 0.95},
        {"qa_id": "qa-second", "weighted_score": 0.8},
    ])])

    report = evaluate_feedback_preview_rerank(cases_path=cases, profile_path=profile, output=output)

    assert report["metrics"]["baseline_top1"] == 1
    assert report["metrics"]["preview_top1"] == 0
    assert report["metrics"]["regression_count"] == 1
    assert report["regressions"][0]["baseline_rank"] == 1
    assert report["regressions"][0]["preview_rank"] == 2


def test_no_numeric_score_preserves_original_order_but_counts_adjustment(tmp_path):
    cases = tmp_path / "cases.jsonl"
    output = tmp_path / "out.json"
    profile = _profile(
        tmp_path / "profile.json",
        {"qa-expected": {"score_adjustment": 9.0}},
    )
    _write_jsonl(
        cases,
        [
            _case(
                expected_qa_id="qa-expected",
                candidates=[
                    {"qa_id": "qa-other"},
                    {"qa_id": "qa-expected"},
                ],
            )
        ],
    )

    report = evaluate_feedback_preview_rerank(cases_path=cases, profile_path=profile, output=output)

    assert report["unchanged"][0]["baseline_top_candidate_id"] == "qa-other"
    assert report["unchanged"][0]["preview_top_candidate_id"] == "qa-other"
    assert report["unchanged"][0]["baseline_rank"] == 2
    assert report["unchanged"][0]["preview_rank"] == 2
    assert report["metrics"]["adjusted_case_count"] == 1
    assert report["metrics"]["adjusted_candidate_count"] == 1


def test_malformed_jsonl_is_skipped_and_counted(tmp_path):
    cases = tmp_path / "cases.jsonl"
    output = tmp_path / "out.json"
    profile = _profile(tmp_path / "profile.json", {})
    _write_jsonl(cases, [_case()], malformed=True)

    report = evaluate_feedback_preview_rerank(cases_path=cases, profile_path=profile, output=output)

    assert report["metrics"]["total_cases"] == 1
    assert report["metrics"]["skipped_malformed_lines"] == 1
    assert report["data_quality"]["skipped_malformed_lines"] == 1


def test_flexible_field_names_are_parsed(tmp_path):
    cases = tmp_path / "cases.jsonl"
    output = tmp_path / "out.json"
    profile = _profile(
        tmp_path / "profile.json",
        {"qa-winner": {"score_adjustment": 0.1}},
    )
    _write_jsonl(
        cases,
        [
            {
                "id": "flex",
                "user_query": "field variants",
                "expected": "qa-winner",
                "candidates": [
                    {"candidate_id": "qa-leader", "scores": {"final_score": 0.5}},
                    {"id": "qa-winner", "similarity": 0.45},
                ],
            }
        ],
    )

    report = evaluate_feedback_preview_rerank(cases_path=cases, profile_path=profile, output=output)

    assert report["improvements"][0]["case_id"] == "flex"
    assert report["improvements"][0]["expected_qa_id"] == "qa-winner"
    assert report["improvements"][0]["preview_top_candidate_id"] == "qa-winner"


def test_output_excludes_answers_comments_chunks_and_full_candidate_payloads(tmp_path):
    cases = tmp_path / "cases.jsonl"
    output = tmp_path / "out.json"
    profile = _profile(tmp_path / "profile.json", {"qa-expected": {"score_adjustment": 0.1}})
    _write_jsonl(
        cases,
        [
            _case(
                query="長い質問" * 100,
                candidates=[
                    {
                        "qa_id": "qa-expected",
                        "score": 0.1,
                        "approved_answer": "秘密の承認済み回答",
                        "comment": "秘密のコメント",
                        "private_chunks": [{"text": "秘密のチャンク"}],
                    }
                ],
            )
        ],
    )

    evaluate_feedback_preview_rerank(cases_path=cases, profile_path=profile, output=output)
    text = output.read_text(encoding="utf-8")
    report = _load(output)

    assert "秘密の承認済み回答" not in text
    assert "秘密のコメント" not in text
    assert "秘密のチャンク" not in text
    assert len(report["unchanged"][0]["query"]) <= 180
    assert "candidates" not in report["unchanged"][0]


def test_cli_writes_requested_output_path(tmp_path):
    cases = tmp_path / "cases.jsonl"
    output = tmp_path / "nested" / "comparison.json"
    profile = _profile(tmp_path / "profile.json", {})
    _write_jsonl(cases, [_case(expected_qa_id="qa-top")])

    code = main(["--cases", str(cases), "--profile", str(profile), "--output", str(output)])

    assert code == 0
    assert output.exists()
    assert _load(output)["metrics"]["baseline_top1"] == 1
