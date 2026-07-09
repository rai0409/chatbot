from __future__ import annotations

import json
from pathlib import Path

from eval.feedback_rerank_signal_builder import build_feedback_rerank_signals


def _write_jsonl(path: Path, rows: list[dict], *, malformed: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if malformed:
        lines.append("{not json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _chat(**overrides):
    base = {
        "feedback_token": "token-1",
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "default",
        "user_query": "15問に自由回答は含まれますか？",
        "answer_mode": "approved_similar_candidate_only",
        "candidate_ids": ["qa-good", "qa-other-1", "qa-other-2"],
        "decision_route": "candidate_only",
        "keyword_profile": "weights.json",
        "threshold_profile": "thresholds.json",
        "timestamp": "2026-06-05T00:00:00+00:00",
        "approved_answer_preview": "監査ログに混ざっていても出力しない本文",
    }
    base.update(overrides)
    return base


def _feedback(**overrides):
    base = {
        "feedback_token": "token-1",
        "feedback_type": "good",
        "selected_candidate_id": "qa-good",
        "shown_candidate_ids": ["qa-good", "qa-other-1", "qa-other-2"],
        "shown_rank": 1,
        "bad_reason": None,
        "comment": "ok",
        "timestamp": "2026-06-05T00:01:00+00:00",
    }
    base.update(overrides)
    return base


def test_good_feedback_creates_positive_vs_other_shown_negative_pairs(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output_path = tmp_path / "pairs.jsonl"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(feedback_path, [_feedback()])

    summary = build_feedback_rerank_signals(
        chat_events=chat_path,
        feedback_events=feedback_path,
        output=output_path,
    )
    rows = _read_jsonl(output_path)

    assert summary["chat_events_loaded"] == 1
    assert summary["feedback_events_loaded"] == 1
    assert summary["joined_feedback_events"] == 1
    assert summary["output_rows"] == 2
    assert {row["negative_candidate_id"] for row in rows} == {"qa-other-1", "qa-other-2"}
    assert all(row["positive_candidate_id"] == "qa-good" for row in rows)
    assert all(row["signal"] == "good_positive_vs_shown_negative" for row in rows)
    assert rows[0]["user_query"] == "15問に自由回答は含まれますか？"
    assert rows[0]["keyword_profile"] == "weights.json"


def test_bad_feedback_creates_negative_signal(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output_path = tmp_path / "pairs.jsonl"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(
        feedback_path,
        [
            _feedback(
                feedback_type="bad",
                selected_candidate_id="qa-bad",
                bad_reason="wrong_intent",
            )
        ],
    )

    build_feedback_rerank_signals(
        chat_events=chat_path,
        feedback_events=feedback_path,
        output=output_path,
    )
    rows = _read_jsonl(output_path)

    assert len(rows) == 1
    assert rows[0]["signal"] == "bad_selected_negative"
    assert rows[0]["negative_candidate_id"] == "qa-bad"
    assert rows[0]["positive_candidate_id"] is None
    assert rows[0]["bad_reason"] == "wrong_intent"


def test_bad_feedback_without_selected_uses_shown_candidates_as_negative_page(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output_path = tmp_path / "pairs.jsonl"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(
        feedback_path,
        [_feedback(feedback_type="bad", selected_candidate_id=None)],
    )

    build_feedback_rerank_signals(
        chat_events=chat_path,
        feedback_events=feedback_path,
        output=output_path,
    )
    rows = _read_jsonl(output_path)

    assert len(rows) == 3
    assert {row["signal"] for row in rows} == {"bad_unselected_or_page"}
    assert {row["negative_candidate_id"] for row in rows} == {"qa-good", "qa-other-1", "qa-other-2"}


def test_human_review_requested_creates_review_needed_signal(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output_path = tmp_path / "pairs.jsonl"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(
        feedback_path,
        [_feedback(feedback_type="human_review_requested", selected_candidate_id="qa-review")],
    )

    build_feedback_rerank_signals(
        chat_events=chat_path,
        feedback_events=feedback_path,
        output=output_path,
    )
    rows = _read_jsonl(output_path)

    assert len(rows) == 1
    assert rows[0]["signal"] == "review_needed"
    assert rows[0]["candidate_id"] == "qa-review"
    assert rows[0]["positive_candidate_id"] is None
    assert rows[0]["negative_candidate_id"] is None


def test_neutral_feedback_does_not_create_positive_signal(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output_path = tmp_path / "pairs.jsonl"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(
        feedback_path,
        [_feedback(feedback_type="neutral", selected_candidate_id="qa-neutral")],
    )

    build_feedback_rerank_signals(
        chat_events=chat_path,
        feedback_events=feedback_path,
        output=output_path,
    )
    rows = _read_jsonl(output_path)

    assert len(rows) == 1
    assert rows[0]["signal"] == "neutral"
    assert rows[0]["positive_candidate_id"] is None


def test_missing_files_are_tolerated(tmp_path):
    output_path = tmp_path / "out" / "pairs.jsonl"

    summary = build_feedback_rerank_signals(
        chat_events=tmp_path / "missing-chat.jsonl",
        feedback_events=tmp_path / "missing-feedback.jsonl",
        output=output_path,
    )

    assert summary["chat_events_loaded"] == 0
    assert summary["feedback_events_loaded"] == 0
    assert summary["output_rows"] == 0
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8") == ""


def test_malformed_jsonl_is_skipped_and_counted(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output_path = tmp_path / "pairs.jsonl"
    _write_jsonl(chat_path, [_chat()], malformed=True)
    _write_jsonl(feedback_path, [_feedback()], malformed=True)

    summary = build_feedback_rerank_signals(
        chat_events=chat_path,
        feedback_events=feedback_path,
        output=output_path,
    )

    assert summary["skipped_malformed_lines"] == 2
    assert summary["output_rows"] == 2


def test_missing_chat_event_is_skipped_and_counted(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output_path = tmp_path / "pairs.jsonl"
    _write_jsonl(chat_path, [_chat(feedback_token="different-token")])
    _write_jsonl(feedback_path, [_feedback()])

    summary = build_feedback_rerank_signals(
        chat_events=chat_path,
        feedback_events=feedback_path,
        output=output_path,
    )

    assert summary["joined_feedback_events"] == 0
    assert summary["skipped_missing_chat_event"] == 1
    assert summary["output_rows"] == 0


def test_output_rows_are_bounded_and_exclude_candidate_payloads(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output_path = tmp_path / "pairs.jsonl"
    long_query = "長い質問" * 200
    _write_jsonl(
        chat_path,
        [
            _chat(
                user_query=long_query,
                candidate_ids=["qa-good", "qa-other"],
                approved_answer="完全な承認済み回答を出力してはいけません",
            )
        ],
    )
    _write_jsonl(
        feedback_path,
        [_feedback(shown_candidate_ids=["qa-good", "qa-other"])],
    )

    build_feedback_rerank_signals(
        chat_events=chat_path,
        feedback_events=feedback_path,
        output=output_path,
    )
    raw = output_path.read_text(encoding="utf-8")
    rows = _read_jsonl(output_path)

    assert len(rows[0]["user_query"]) <= 500
    assert rows[0]["user_query"].endswith("...[truncated]")
    assert "approved_answer" not in rows[0]
    assert "approved_answer_preview" not in rows[0]
    assert "完全な承認済み回答" not in raw
