from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import config
from webapi import main


def _feedback_path(tmp_path):
    return tmp_path / "runs" / "audit" / "feedback_events.jsonl"


def test_chat_feedback_good_feedback_returns_ok_and_writes_jsonl(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))

    payload = main.chat_feedback(
        main.ProductFeedbackRequest(
            feedback_token="fb-token-1",
            feedback_type="good",
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-a",
            selected_candidate_id="qa-1",
            shown_candidate_ids=["qa-1", "qa-2"],
            shown_rank=1,
            comment="役に立ちました。",
        )
    )

    assert payload == {
        "ok": True,
        "feedback_token": "fb-token-1",
        "feedback_type": "good",
        "stored": True,
    }
    lines = _feedback_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["kind"] == "product_preview_feedback"
    assert event["feedback_token"] == "fb-token-1"
    assert event["feedback_type"] == "good"
    assert event["selected_candidate_id"] == "qa-1"
    assert event["shown_candidate_ids"] == ["qa-1", "qa-2"]
    assert event["shown_rank"] == 1
    assert event["timestamp"]


def test_chat_feedback_bad_feedback_bounds_comment_and_candidate_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    comment = "長いコメント" * 200
    shown_candidate_ids = [f"qa-{idx}" for idx in range(25)]

    response = main.chat_feedback(
        main.ProductFeedbackRequest(
            feedback_token="fb-token-2",
            feedback_type="bad",
            selected_candidate_id="qa-selected",
            shown_candidate_ids=shown_candidate_ids,
            bad_reason="期待と違います。",
            comment=comment,
        )
    )

    assert response["ok"] is True
    event = json.loads(_feedback_path(tmp_path).read_text(encoding="utf-8"))
    assert len(event["comment"]) <= 1000
    assert event["comment"].endswith("...[truncated]")
    assert len(event["shown_candidate_ids"]) == 20
    assert event["shown_candidate_ids"] == shown_candidate_ids[:20]
    assert event["bad_reason"] == "期待と違います。"


def test_chat_feedback_invalid_feedback_type_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        main.chat_feedback(
            main.ProductFeedbackRequest(
                feedback_token="fb-token-3",
                feedback_type="unsafe",
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid feedback_type"


def test_chat_feedback_missing_feedback_token_is_rejected():
    with pytest.raises(ValidationError):
        main.ProductFeedbackRequest(feedback_type="good")


def test_chat_feedback_blank_feedback_token_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        main.chat_feedback(
            main.ProductFeedbackRequest(
                feedback_token="   ",
                feedback_type="good",
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "feedback_token is required"


def test_chat_feedback_logging_failure_returns_safe_response(monkeypatch):
    monkeypatch.setattr(main, "append_feedback_audit_event", lambda event: False)

    payload = main.chat_feedback(
        main.ProductFeedbackRequest(
            feedback_token="fb-token-4",
            feedback_type="neutral",
        )
    )

    assert payload["ok"] is True
    assert payload["stored"] is False
    assert payload["warning"] == "feedback_logging_failed"
