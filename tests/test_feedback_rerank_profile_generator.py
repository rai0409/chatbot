from __future__ import annotations

import json
from pathlib import Path

from eval.feedback_rerank_profile_generator import generate_feedback_rerank_profile


def _write_jsonl(path: Path, rows: list[dict], *, malformed: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if malformed:
        lines.append("{not json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _row(**overrides):
    base = {
        "feedback_token": "token-1",
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "default",
        "user_query": "出力に含めない質問本文",
        "positive_candidate_id": None,
        "negative_candidate_id": None,
        "candidate_id": "qa-1",
        "signal": "neutral",
        "feedback_type": "neutral",
        "bad_reason": None,
        "shown_rank": 1,
        "answer_mode": "approved_similar_candidate_only",
        "decision_route": "candidate_only",
        "keyword_profile": "weights.json",
        "threshold_profile": "thresholds.json",
        "chat_timestamp": "2026-06-05T00:00:00+00:00",
        "feedback_timestamp": "2026-06-05T00:01:00+00:00",
        "approved_answer": "出力してはいけない承認済み回答",
        "comment": "出力してはいけないコメント",
    }
    base.update(overrides)
    return base


def test_missing_input_file_creates_safe_empty_preview_profile(tmp_path):
    output = tmp_path / "configs" / "preview.json"

    summary = generate_feedback_rerank_profile(
        input_path=tmp_path / "missing.jsonl",
        output=output,
    )
    profile = _load_json(output)

    assert summary["input_rows_loaded"] == 0
    assert summary["output_candidate_adjustments"] == 0
    assert profile["profile_name"] == "feedback_preview"
    assert profile["runtime_enabled"] is False
    assert profile["production_enabled"] is False
    assert profile["candidate_adjustments"] == {}
    assert profile["safety"]["no_runtime_ranking_change"] is True
    assert profile["safety"]["no_auto_answer_enablement"] is True
    assert profile["safety"]["requires_offline_evaluation_before_production"] is True


def test_malformed_jsonl_lines_are_skipped_and_counted(tmp_path):
    input_path = tmp_path / "pairs.jsonl"
    output = tmp_path / "preview.json"
    _write_jsonl(
        input_path,
        [_row(signal="good_positive", feedback_type="good", positive_candidate_id="qa-1")],
        malformed=True,
    )

    summary = generate_feedback_rerank_profile(input_path=input_path, output=output)

    assert summary["input_rows_loaded"] == 1
    assert summary["skipped_malformed_lines"] == 1
    assert summary["positive_signals"] == 1


def test_positive_signals_create_small_positive_adjustment(tmp_path):
    input_path = tmp_path / "pairs.jsonl"
    output = tmp_path / "preview.json"
    _write_jsonl(
        input_path,
        [
            _row(
                signal="good_positive",
                feedback_type="good",
                candidate_id="qa-positive",
                positive_candidate_id="qa-positive",
            )
        ],
    )

    generate_feedback_rerank_profile(input_path=input_path, output=output)
    adjustment = _load_json(output)["candidate_adjustments"]["qa-positive"]

    assert adjustment["positive_count"] == 1
    assert adjustment["negative_count"] == 0
    assert adjustment["score_adjustment"] == 0.03
    assert "positive feedback signals: 1" in adjustment["reasons"]


def test_negative_signals_create_larger_negative_adjustment(tmp_path):
    input_path = tmp_path / "pairs.jsonl"
    output = tmp_path / "preview.json"
    _write_jsonl(
        input_path,
        [
            _row(
                signal="bad_selected_negative",
                feedback_type="bad",
                candidate_id="qa-negative",
                negative_candidate_id="qa-negative",
                bad_reason="wrong_intent",
            )
        ],
    )

    generate_feedback_rerank_profile(input_path=input_path, output=output)
    adjustment = _load_json(output)["candidate_adjustments"]["qa-negative"]

    assert adjustment["negative_count"] == 1
    assert adjustment["positive_count"] == 0
    assert adjustment["score_adjustment"] == -0.06
    assert "negative feedback signals: 1" in adjustment["reasons"]


def test_review_needed_creates_review_penalty_and_reason(tmp_path):
    input_path = tmp_path / "pairs.jsonl"
    output = tmp_path / "preview.json"
    _write_jsonl(
        input_path,
        [
            _row(
                signal="review_needed",
                feedback_type="human_review_requested",
                candidate_id="qa-review",
            )
        ],
    )

    generate_feedback_rerank_profile(input_path=input_path, output=output)
    adjustment = _load_json(output)["candidate_adjustments"]["qa-review"]

    assert adjustment["review_needed_count"] == 1
    assert adjustment["score_adjustment"] == -0.03
    assert "review-needed signals: 1" in adjustment["reasons"]


def test_neutral_does_not_create_positive_boost(tmp_path):
    input_path = tmp_path / "pairs.jsonl"
    output = tmp_path / "preview.json"
    _write_jsonl(
        input_path,
        [_row(signal="neutral", feedback_type="neutral", candidate_id="qa-neutral")],
    )

    generate_feedback_rerank_profile(input_path=input_path, output=output)
    adjustment = _load_json(output)["candidate_adjustments"]["qa-neutral"]

    assert adjustment["neutral_count"] == 1
    assert adjustment["positive_count"] == 0
    assert adjustment["score_adjustment"] == 0.0


def test_score_adjustment_is_clamped(tmp_path):
    input_path = tmp_path / "pairs.jsonl"
    output = tmp_path / "preview.json"
    rows = [
        _row(
            signal="good_positive",
            feedback_type="good",
            candidate_id="qa-positive-clamped",
            positive_candidate_id="qa-positive-clamped",
            feedback_token=f"p-{idx}",
        )
        for idx in range(4)
    ]
    rows.extend(
        _row(
            signal="bad_selected_negative",
            feedback_type="bad",
            candidate_id="qa-negative-clamped",
            negative_candidate_id="qa-negative-clamped",
            feedback_token=f"n-{idx}",
        )
        for idx in range(4)
    )
    _write_jsonl(input_path, rows)

    generate_feedback_rerank_profile(input_path=input_path, output=output)
    adjustments = _load_json(output)["candidate_adjustments"]

    assert adjustments["qa-positive-clamped"]["score_adjustment"] == 0.05
    assert adjustments["qa-negative-clamped"]["score_adjustment"] == -0.10


def test_profile_safety_flags_and_disabled_runtime_fields(tmp_path):
    input_path = tmp_path / "pairs.jsonl"
    output = tmp_path / "preview.json"
    _write_jsonl(input_path, [_row()])

    summary = generate_feedback_rerank_profile(input_path=input_path, output=output)
    profile = _load_json(output)

    assert summary["output_path"] == str(output)
    assert profile["profile_type"] == "approved_similar_feedback_rerank"
    assert profile["version"] == 1
    assert profile["runtime_enabled"] is False
    assert profile["production_enabled"] is False
    assert profile["global_weights"]["feedback_positive_boost"] < profile["global_weights"]["feedback_negative_penalty"]
    assert profile["limits"]["max_positive_boost"] == 0.05
    assert profile["limits"]["max_negative_penalty"] == 0.10
    assert profile["safety"] == {
        "no_runtime_ranking_change": True,
        "no_auto_answer_enablement": True,
        "requires_offline_evaluation_before_production": True,
    }


def test_output_excludes_queries_answers_comments_and_private_payloads(tmp_path):
    input_path = tmp_path / "pairs.jsonl"
    output = tmp_path / "preview.json"
    _write_jsonl(
        input_path,
        [
            _row(
                signal="good_positive",
                feedback_type="good",
                candidate_id="qa-safe",
                positive_candidate_id="qa-safe",
                user_query="秘密の質問本文",
                approved_answer="秘密の承認済み回答",
                comment="秘密のコメント",
                private_payload={"chunk": "秘密のチャンク"},
            )
        ],
    )

    generate_feedback_rerank_profile(input_path=input_path, output=output)
    raw = output.read_text(encoding="utf-8")

    assert "秘密の質問本文" not in raw
    assert "秘密の承認済み回答" not in raw
    assert "秘密のコメント" not in raw
    assert "private_payload" not in raw
    assert "approved_answer" not in raw


def test_missing_candidate_id_is_skipped_and_counted(tmp_path):
    input_path = tmp_path / "pairs.jsonl"
    output = tmp_path / "preview.json"
    _write_jsonl(
        input_path,
        [_row(signal="neutral", feedback_type="neutral", candidate_id=None)],
    )

    summary = generate_feedback_rerank_profile(input_path=input_path, output=output)
    profile = _load_json(output)

    assert summary["skipped_missing_candidate_id"] == 1
    assert summary["output_candidate_adjustments"] == 0
    assert profile["candidate_adjustments"] == {}
