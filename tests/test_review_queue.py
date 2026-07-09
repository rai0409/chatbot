from __future__ import annotations

import json
from pathlib import Path

from rag_core.review_queue import build_review_queue, main


def _write_jsonl(path: Path, rows: list[dict], *, malformed: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if malformed:
        lines.append("{bad json")
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
        "confidence_route": "candidate_only",
        "decision_route": "candidate_only",
        "candidate_ids": ["qa-1", "qa-2"],
        "timestamp": "2026-06-05T00:00:00+00:00",
        "approved_answer_preview": "出力してはいけないプレビュー本文",
        "private_context": {"chunk": "出力してはいけないチャンク"},
    }
    base.update(overrides)
    return base


def _feedback(**overrides):
    base = {
        "feedback_token": "token-1",
        "feedback_type": "bad",
        "tenant_id": "default",
        "selected_candidate_id": "qa-1",
        "shown_candidate_ids": ["qa-1", "qa-2"],
        "shown_rank": 1,
        "bad_reason": "wrong_intent",
        "comment": "出力してはいけないコメント",
        "timestamp": "2026-06-05T00:01:00+00:00",
    }
    base.update(overrides)
    return base


def test_bad_feedback_with_selected_candidate_creates_high_priority_item(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output = tmp_path / "review.jsonl"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(feedback_path, [_feedback()])

    summary = build_review_queue(chat_events=chat_path, feedback_events=feedback_path, output=output)
    items = _read_jsonl(output)

    assert summary["joined_feedback_events"] == 1
    assert summary["review_items_written"] == 1
    assert items[0]["priority"] == "high"
    assert items[0]["source"] == "feedback"
    assert items[0]["selected_candidate_id"] == "qa-1"
    assert items[0]["feedback_type"] == "bad"
    assert items[0]["reasons"] == ["bad_feedback_with_selected_candidate"]
    assert items[0]["status"] == "open"


def test_bad_feedback_without_selected_candidate_creates_medium_priority_item(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output = tmp_path / "review.jsonl"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(feedback_path, [_feedback(selected_candidate_id=None)])

    build_review_queue(chat_events=chat_path, feedback_events=feedback_path, output=output)
    item = _read_jsonl(output)[0]

    assert item["priority"] == "medium"
    assert item["reasons"] == ["bad_feedback_without_selected_candidate"]
    assert item["selected_candidate_id"] is None


def test_human_review_requested_creates_high_priority_item(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output = tmp_path / "review.jsonl"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(
        feedback_path,
        [_feedback(feedback_type="human_review_requested", selected_candidate_id="qa-2")],
    )

    build_review_queue(chat_events=chat_path, feedback_events=feedback_path, output=output)
    item = _read_jsonl(output)[0]

    assert item["priority"] == "high"
    assert item["feedback_type"] == "human_review_requested"
    assert item["reasons"] == ["human_review_requested"]


def test_fallback_no_answer_chat_event_creates_medium_priority_item(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output = tmp_path / "review.jsonl"
    _write_jsonl(
        chat_path,
        [_chat(answer_mode="fallback_no_answer", confidence_route="no_answer", candidate_ids=[])],
    )
    _write_jsonl(feedback_path, [])

    build_review_queue(chat_events=chat_path, feedback_events=feedback_path, output=output)
    item = _read_jsonl(output)[0]

    assert item["priority"] == "medium"
    assert item["source"] == "chat_event"
    assert item["answer_mode"] == "fallback_no_answer"
    assert item["reasons"] == ["fallback_no_answer"]


def test_candidate_only_chat_event_without_bad_feedback_creates_low_priority_item(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output = tmp_path / "review.jsonl"
    _write_jsonl(chat_path, [_chat(feedback_token="token-low")])
    _write_jsonl(feedback_path, [])

    build_review_queue(chat_events=chat_path, feedback_events=feedback_path, output=output)
    item = _read_jsonl(output)[0]

    assert item["priority"] == "low"
    assert item["source"] == "chat_event"
    assert item["answer_mode"] == "approved_similar_candidate_only"
    assert item["reasons"] == ["approved_similar_candidate_only"]


def test_duplicate_feedback_token_and_reason_does_not_create_duplicate_items(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output = tmp_path / "review.jsonl"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(feedback_path, [_feedback(), _feedback(bad_reason="still_duplicate")])

    summary = build_review_queue(chat_events=chat_path, feedback_events=feedback_path, output=output)

    assert summary["review_items_written"] == 1
    assert summary["skipped_duplicates"] == 1


def test_malformed_jsonl_is_skipped_and_counted(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output = tmp_path / "review.jsonl"
    _write_jsonl(chat_path, [_chat()], malformed=True)
    _write_jsonl(feedback_path, [_feedback()], malformed=True)

    summary = build_review_queue(chat_events=chat_path, feedback_events=feedback_path, output=output)

    assert summary["skipped_malformed_lines"] == 2
    assert summary["review_items_written"] == 1


def test_missing_files_are_tolerated(tmp_path):
    output = tmp_path / "runs" / "review" / "review_queue.jsonl"

    summary = build_review_queue(
        chat_events=tmp_path / "missing-chat.jsonl",
        feedback_events=tmp_path / "missing-feedback.jsonl",
        output=output,
    )

    assert summary["chat_events_loaded"] == 0
    assert summary["feedback_events_loaded"] == 0
    assert summary["review_items_written"] == 0
    assert output.exists()
    assert output.read_text(encoding="utf-8") == ""


def test_output_excludes_candidate_payloads_and_approved_answers(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output = tmp_path / "review.jsonl"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(feedback_path, [_feedback()])

    build_review_queue(chat_events=chat_path, feedback_events=feedback_path, output=output)
    raw = output.read_text(encoding="utf-8")
    item = _read_jsonl(output)[0]

    assert item["candidate_ids"] == ["qa-1", "qa-2"]
    assert "approved_answer_preview" not in item
    assert "private_context" not in item
    assert "出力してはいけないプレビュー本文" not in raw
    assert "出力してはいけないチャンク" not in raw
    assert "出力してはいけないコメント" not in raw


def test_user_query_and_bad_reason_are_bounded(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output = tmp_path / "review.jsonl"
    long_query = "長い質問" * 200
    long_reason = "理由" * 300
    _write_jsonl(chat_path, [_chat(user_query=long_query)])
    _write_jsonl(feedback_path, [_feedback(bad_reason=long_reason)])

    build_review_queue(chat_events=chat_path, feedback_events=feedback_path, output=output)
    item = _read_jsonl(output)[0]

    assert len(item["user_query"]) <= 500
    assert item["user_query"].endswith("...[truncated]")
    assert len(item["bad_reason"]) <= 300
    assert item["bad_reason"].endswith("...[truncated]")


def test_summary_counts_missing_required_fields(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output = tmp_path / "review.jsonl"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(feedback_path, [_feedback(feedback_token=None), _feedback(feedback_token="missing")])

    summary = build_review_queue(chat_events=chat_path, feedback_events=feedback_path, output=output)

    assert summary["feedback_events_loaded"] == 2
    assert summary["joined_feedback_events"] == 0
    assert summary["skipped_missing_required_fields"] == 2
    assert summary["review_items_written"] == 1


def test_cli_writes_provided_output_path(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    output = tmp_path / "runs" / "review" / "review_queue.jsonl"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(feedback_path, [_feedback()])

    exit_code = main(
        [
            "--chat-events",
            str(chat_path),
            "--feedback-events",
            str(feedback_path),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert output.exists()
    assert len(_read_jsonl(output)) == 1
