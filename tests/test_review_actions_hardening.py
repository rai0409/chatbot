"""PR F: review action log - who acted, real time order, only known statuses. Synthetic data only."""
from __future__ import annotations

import json

import config
from rag_core import review_actions as ra
from webapi import main


def _write(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")


def test_the_newest_action_wins_by_time_not_by_text(tmp_path):
    path = tmp_path / "actions.jsonl"
    _write(path, [
        # 2026-10-31T20:00Z (written first)
        {"tenant_id": "t", "review_id": "r1", "status_after": "resolved", "created_at": "2026-10-31T20:00:00+00:00"},
        # 2026-10-31T15:00Z: older in real time, but its text sorts later
        {"tenant_id": "t", "review_id": "r1", "status_after": "open", "created_at": "2026-11-01T00:00:00+09:00"},
    ])
    assert ra.latest_review_statuses(path)[("t", "r1")] == "resolved"


def test_unknown_statuses_are_ignored(tmp_path):
    path = tmp_path / "actions.jsonl"
    _write(path, [
        {"tenant_id": "t", "review_id": "r1", "status_after": "in_review", "created_at": "2026-10-31T10:00:00+00:00"},
        {"tenant_id": "t", "review_id": "r1", "status_after": "hacked", "created_at": "2026-10-31T11:00:00+00:00"},
    ])
    assert ra.latest_review_statuses(path)[("t", "r1")] == "in_review"


def test_an_unparsable_time_never_beats_a_valid_one(tmp_path):
    path = tmp_path / "actions.jsonl"
    _write(path, [
        {"tenant_id": "t", "review_id": "r1", "status_after": "resolved", "created_at": "2026-10-31T10:00:00+00:00"},
        {"tenant_id": "t", "review_id": "r1", "status_after": "ignored", "created_at": "zzzz"},
    ])
    assert ra.latest_review_statuses(path)[("t", "r1")] == "resolved"


def test_the_event_records_who_acted():
    event = ra.build_review_action_event(review_id="r1", action_type="mark_resolved", actor={"admin_scope": "installation"})
    assert event["actor"] == {"admin_scope": "installation"}
    assert ra.build_review_action_event(review_id="r1", action_type="mark_resolved")["actor"] is None


def test_the_route_stores_the_actor(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    main.admin_review_action(main.ReviewActionRequest(review_id="review-a", action_type="mark_ignored"))
    line = (tmp_path / "runs" / "review" / "review_actions.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(line)["actor"] == {"admin_scope": "installation"}
