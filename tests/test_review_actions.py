from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import config
from webapi import main


def _actions_path(tmp_path):
    return tmp_path / "runs" / "review" / "review_actions.jsonl"


def test_valid_approve_candidate_action_returns_ok_and_writes_jsonl(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))

    response = main.admin_review_action(
        main.ReviewActionRequest(
            review_id="review-1",
            action_type="approve_candidate",
            feedback_token="token-1",
            tenant_id="tenant-a",
            selected_candidate_id="qa-1",
            operator_note="承認します。",
            status_after="resolved",
            tags=["approved", "candidate"],
        )
    )

    assert response["ok"] is True
    assert response["stored"] is True
    assert response["review_id"] == "review-1"
    assert response["action_type"] == "approve_candidate"
    assert response["action_id"]
    event = json.loads(_actions_path(tmp_path).read_text(encoding="utf-8"))
    assert event["action_id"] == response["action_id"]
    assert event["review_id"] == "review-1"
    assert event["feedback_token"] == "token-1"
    assert event["tenant_id"] == "tenant-a"
    assert event["selected_candidate_id"] == "qa-1"
    assert event["operator_note"] == "承認します。"
    assert event["status_after"] == "resolved"
    assert event["tags"] == ["approved", "candidate"]
    assert event["created_at"]


def test_valid_needs_new_faq_action_writes_jsonl(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))

    response = main.admin_review_action(
        main.ReviewActionRequest(
            review_id="review-2",
            action_type="needs_new_faq",
            tenant_id="default",
            operator_note="FAQ候補化します。",
            status_after="needs_followup",
        )
    )

    event = json.loads(_actions_path(tmp_path).read_text(encoding="utf-8"))
    assert response["stored"] is True
    assert event["action_type"] == "needs_new_faq"
    assert event["status_after"] == "needs_followup"


def test_invalid_action_type_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        main.admin_review_action(
            main.ReviewActionRequest(
                review_id="review-3",
                action_type="unsafe",
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid action_type"


def test_invalid_status_after_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        main.admin_review_action(
            main.ReviewActionRequest(
                review_id="review-4",
                action_type="reject_candidate",
                status_after="deleted",
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid status_after"


def test_missing_review_id_is_rejected():
    with pytest.raises(ValidationError):
        main.ReviewActionRequest(action_type="approve_candidate")


def test_blank_review_id_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        main.admin_review_action(
            main.ReviewActionRequest(review_id="  ", action_type="approve_candidate")
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "review_id is required"


def test_operator_note_and_tags_are_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    long_note = "長いメモ" * 400
    tags = [f"tag-{idx}" for idx in range(25)]

    main.admin_review_action(
        main.ReviewActionRequest(
            review_id="review-5",
            action_type="needs_policy_review",
            operator_note=long_note,
            tags=tags,
        )
    )
    event = json.loads(_actions_path(tmp_path).read_text(encoding="utf-8"))

    assert len(event["operator_note"]) <= 1000
    assert event["operator_note"].endswith("...[truncated]")
    assert len(event["tags"]) == 20
    assert event["tags"] == tags[:20]


def test_logging_failure_returns_safe_warning(monkeypatch):
    monkeypatch.setattr(main, "append_review_action_event", lambda event: False)

    response = main.admin_review_action(
        main.ReviewActionRequest(review_id="review-6", action_type="mark_ignored")
    )

    assert response["ok"] is True
    assert response["stored"] is False
    assert response["warning"] == "review_action_logging_failed"


def test_action_event_excludes_candidate_payloads_and_approved_answers(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))

    main.admin_review_action(
        main.ReviewActionRequest(
            review_id="review-7",
            action_type="reject_candidate",
            selected_candidate_id="qa-secret",
            operator_note="出力してよいメモ",
            tags=["tag"],
        )
    )
    raw = _actions_path(tmp_path).read_text(encoding="utf-8")
    event = json.loads(raw)

    assert event["selected_candidate_id"] == "qa-secret"
    assert "candidate_payload" not in event
    assert "approved_answer" not in event
    assert "承認済み回答" not in raw


def test_admin_review_page_references_review_action_endpoint():
    response = main.admin_review_page()
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "/admin/review/action" in body
