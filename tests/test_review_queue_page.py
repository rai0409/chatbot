from __future__ import annotations

import json
from pathlib import Path

import config
from webapi import main


def _write_review_queue(runs_dir: Path, rows: list[dict], *, malformed: bool = False) -> Path:
    path = runs_dir / "review" / "review_queue.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if malformed:
        lines.append("{bad json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _item(**overrides):
    base = {
        "review_id": "review-1",
        "priority": "high",
        "status": "open",
        "tenant_id": "default",
        "user_query": "15問に自由回答は含まれますか？",
        "answer_mode": "approved_similar_candidate_only",
        "confidence_route": "candidate_only",
        "decision_route": "candidate_only",
        "candidate_ids": ["qa-1", "qa-2"],
        "selected_candidate_id": "qa-1",
        "feedback_type": "bad",
        "bad_reason": "wrong_intent",
        "created_at": "2026-06-05T00:00:00+00:00",
        "source": "feedback",
        "reasons": ["bad_feedback_with_selected_candidate"],
        "approved_answer_preview": "出力してはいけない承認済み回答",
        "candidate_payload": {"answer": "出力してはいけない候補本文"},
    }
    base.update(overrides)
    return base


def test_admin_review_page_returns_html():
    response = main.admin_review_page()

    assert response.status_code == 200
    assert b"Review Queue" in response.body


def test_admin_review_page_references_items_endpoint():
    response = main.admin_review_page()
    body = response.body.decode("utf-8")

    assert "/admin/review/items" in body
    assert "priority" in body
    assert "status" in body


def test_admin_review_items_returns_empty_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))

    response = main.admin_review_items()

    assert response["items"] == []
    assert response["total_loaded"] == 0
    assert response["returned_count"] == 0
    assert response["skipped_malformed_lines"] == 0


def test_admin_review_items_returns_bounded_items(monkeypatch, tmp_path):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(config, "RUNS_DIR", str(runs_dir))
    long_query = "長い質問" * 200
    _write_review_queue(runs_dir, [_item(user_query=long_query, bad_reason="理由" * 200)])

    response = main.admin_review_items()
    item = response["items"][0]

    assert response["total_loaded"] == 1
    assert response["returned_count"] == 1
    assert item["review_id"] == "review-1"
    assert item["candidate_ids"] == ["qa-1", "qa-2"]
    assert len(item["user_query"]) <= 500
    assert item["user_query"].endswith("...[truncated]")
    assert len(item["bad_reason"]) <= 300
    assert item["bad_reason"].endswith("...[truncated]")


def test_admin_review_items_priority_filter_works(monkeypatch, tmp_path):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(config, "RUNS_DIR", str(runs_dir))
    _write_review_queue(
        runs_dir,
        [
            _item(review_id="review-high", priority="high"),
            _item(review_id="review-low", priority="low"),
        ],
    )

    response = main.admin_review_items(priority="low")

    assert response["returned_count"] == 1
    assert response["items"][0]["review_id"] == "review-low"
    assert response["filters"]["priority"] == "low"


def test_admin_review_items_status_filter_works(monkeypatch, tmp_path):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(config, "RUNS_DIR", str(runs_dir))
    _write_review_queue(
        runs_dir,
        [
            _item(review_id="review-open", status="open"),
            _item(review_id="review-closed", status="closed"),
        ],
    )

    response = main.admin_review_items(status="closed")

    assert response["returned_count"] == 1
    assert response["items"][0]["review_id"] == "review-closed"
    assert response["filters"]["status"] == "closed"


def test_admin_review_items_tenant_filter_works(monkeypatch, tmp_path):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(config, "RUNS_DIR", str(runs_dir))
    _write_review_queue(
        runs_dir,
        [
            _item(review_id="review-a", tenant_id="tenant-a"),
            _item(review_id="review-b", tenant_id="tenant-b"),
        ],
    )

    response = main.admin_review_items(tenant_id="tenant-b")

    assert response["returned_count"] == 1
    assert response["items"][0]["review_id"] == "review-b"


def test_admin_review_items_limit_is_bounded(monkeypatch, tmp_path):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(config, "RUNS_DIR", str(runs_dir))
    rows = [_item(review_id=f"review-{idx}") for idx in range(520)]
    _write_review_queue(runs_dir, rows)

    response = main.admin_review_items(limit=999)

    assert response["total_loaded"] == 520
    assert response["returned_count"] == 500
    assert response["filters"]["limit"] == 500


def test_admin_review_items_malformed_line_is_skipped_and_counted(monkeypatch, tmp_path):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(config, "RUNS_DIR", str(runs_dir))
    _write_review_queue(runs_dir, [_item()], malformed=True)

    response = main.admin_review_items()

    assert response["total_loaded"] == 1
    assert response["returned_count"] == 1
    assert response["skipped_malformed_lines"] == 1


def test_admin_review_items_does_not_expose_private_payloads(monkeypatch, tmp_path):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(config, "RUNS_DIR", str(runs_dir))
    _write_review_queue(runs_dir, [_item()])

    response = main.admin_review_items()
    raw = json.dumps(response, ensure_ascii=False)
    item = response["items"][0]

    assert "approved_answer_preview" not in item
    assert "candidate_payload" not in item
    assert "出力してはいけない承認済み回答" not in raw
    assert "出力してはいけない候補本文" not in raw
