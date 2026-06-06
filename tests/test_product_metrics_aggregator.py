from __future__ import annotations

import json
from pathlib import Path

from eval.product_metrics_aggregator import aggregate_product_metrics, main


def _write_jsonl(path: Path, rows: list[dict], *, malformed: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if malformed:
        lines.append("{bad json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _chat(**overrides):
    base = {
        "feedback_token": "token-1",
        "request_id": "req-1",
        "tenant_id": "default",
        "user_query": "出力してはいけない質問",
        "answer_mode": "approved_similar_candidate_only",
        "decision_route": "candidate_only",
        "latency_ms": 100,
        "approved_answer": "出力してはいけない承認済み回答",
        "candidate_payload": {"text": "出力してはいけない候補"},
    }
    base.update(overrides)
    return base


def _feedback(**overrides):
    base = {
        "feedback_token": "token-1",
        "feedback_type": "good",
        "bad_reason": None,
        "comment": "出力してはいけないコメント",
    }
    base.update(overrides)
    return base


def _review(**overrides):
    base = {
        "review_id": "review-1",
        "priority": "high",
        "status": "open",
        "reasons": ["bad_feedback_with_selected_candidate"],
        "user_query": "出力してはいけないレビュー質問",
    }
    base.update(overrides)
    return base


def _action(**overrides):
    base = {
        "action_id": "action-1",
        "review_id": "review-1",
        "action_type": "approve_candidate",
        "status_after": "resolved",
        "operator_note": "出力してはいけない作業メモ",
    }
    base.update(overrides)
    return base


def test_missing_files_create_safe_zero_metrics_output(tmp_path):
    output = tmp_path / "metrics" / "product_metrics.json"

    metrics = aggregate_product_metrics(
        chat_events=tmp_path / "missing-chat.jsonl",
        feedback_events=tmp_path / "missing-feedback.jsonl",
        review_queue=tmp_path / "missing-review.jsonl",
        review_actions=tmp_path / "missing-actions.jsonl",
        output=output,
    )

    assert metrics["chat"]["total_questions"] == 0
    assert metrics["feedback"]["total_feedback"] == 0
    assert metrics["review_queue"]["total_review_items"] == 0
    assert metrics["review_actions"]["total_review_actions"] == 0
    assert metrics["quality_indicators"]["candidate_only_rate"] == 0.0
    assert len(metrics["data_quality"]["missing_input_files"]) == 4
    assert output.exists()


def test_malformed_jsonl_lines_are_skipped_and_counted(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    review_path = tmp_path / "review.jsonl"
    actions_path = tmp_path / "actions.jsonl"
    output = tmp_path / "metrics.json"
    _write_jsonl(chat_path, [_chat()], malformed=True)
    _write_jsonl(feedback_path, [_feedback()], malformed=True)
    _write_jsonl(review_path, [_review()], malformed=True)
    _write_jsonl(actions_path, [_action()], malformed=True)

    metrics = aggregate_product_metrics(
        chat_events=chat_path,
        feedback_events=feedback_path,
        review_queue=review_path,
        review_actions=actions_path,
        output=output,
    )

    assert metrics["data_quality"]["skipped_malformed_lines"] == 4
    assert metrics["chat"]["total_questions"] == 1
    assert metrics["feedback"]["total_feedback"] == 1


def test_chat_answer_mode_counts_and_quality_rates_are_correct(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    output = tmp_path / "metrics.json"
    _write_jsonl(
        chat_path,
        [
            _chat(answer_mode="approved_similar_candidate_only", decision_route="candidate_only"),
            _chat(answer_mode="fallback_no_answer", decision_route="no_answer", tenant_id="tenant-a"),
            _chat(answer_mode="approved_exact_match", decision_route="exact_match"),
            _chat(answer_mode="rag_answer", decision_route="rag"),
            _chat(answer_mode="human_escalation", decision_route="human_review"),
        ],
    )

    metrics = aggregate_product_metrics(
        chat_events=chat_path,
        feedback_events=tmp_path / "missing-feedback.jsonl",
        review_queue=tmp_path / "missing-review.jsonl",
        review_actions=tmp_path / "missing-actions.jsonl",
        output=output,
    )

    assert metrics["chat"]["total_questions"] == 5
    assert metrics["chat"]["candidate_only_count"] == 1
    assert metrics["chat"]["fallback_no_answer_count"] == 1
    assert metrics["chat"]["exact_match_count"] == 1
    assert metrics["chat"]["rag_answer_count"] == 1
    assert metrics["chat"]["human_escalation_count"] == 1
    assert metrics["chat"]["answer_mode_breakdown"]["approved_similar_candidate_only"] == 1
    assert metrics["chat"]["decision_route_breakdown"]["candidate_only"] == 1
    assert metrics["chat"]["tenant_breakdown"]["tenant-a"] == 1
    assert metrics["quality_indicators"]["candidate_only_rate"] == 0.2
    assert metrics["quality_indicators"]["fallback_no_answer_rate"] == 0.2


def test_feedback_counts_rates_and_bad_reason_breakdown_are_correct(tmp_path):
    feedback_path = tmp_path / "feedback.jsonl"
    chat_path = tmp_path / "chat.jsonl"
    output = tmp_path / "metrics.json"
    _write_jsonl(chat_path, [_chat(), _chat(feedback_token="token-2")])
    _write_jsonl(
        feedback_path,
        [
            _feedback(feedback_type="good"),
            _feedback(feedback_type="bad", bad_reason="wrong_intent"),
            _feedback(feedback_type="bad", bad_reason="weak_evidence"),
            _feedback(feedback_type="neutral"),
            _feedback(feedback_type="human_review_requested"),
        ],
    )

    metrics = aggregate_product_metrics(
        chat_events=chat_path,
        feedback_events=feedback_path,
        review_queue=tmp_path / "missing-review.jsonl",
        review_actions=tmp_path / "missing-actions.jsonl",
        output=output,
    )

    assert metrics["feedback"]["total_feedback"] == 5
    assert metrics["feedback"]["good_feedback_count"] == 1
    assert metrics["feedback"]["bad_feedback_count"] == 2
    assert metrics["feedback"]["neutral_feedback_count"] == 1
    assert metrics["feedback"]["human_review_requested_count"] == 1
    assert metrics["feedback"]["good_feedback_rate"] == 0.2
    assert metrics["feedback"]["bad_feedback_rate"] == 0.4
    assert metrics["feedback"]["feedback_coverage_rate"] == 2.5
    assert metrics["feedback"]["bad_reason_breakdown"] == {
        "wrong_intent": 1,
        "weak_evidence": 1,
    }
    assert metrics["quality_indicators"]["human_review_requested_rate"] == 0.2


def test_review_queue_priority_status_and_reason_breakdowns_are_correct(tmp_path):
    review_path = tmp_path / "review.jsonl"
    chat_path = tmp_path / "chat.jsonl"
    output = tmp_path / "metrics.json"
    _write_jsonl(chat_path, [_chat(), _chat(feedback_token="token-2")])
    _write_jsonl(
        review_path,
        [
            _review(priority="high", status="open", reasons=["bad_feedback_with_selected_candidate"]),
            _review(priority="medium", status="open", reasons=["fallback_no_answer"]),
            _review(priority="low", status="resolved", reasons=["approved_similar_candidate_only"]),
        ],
    )

    metrics = aggregate_product_metrics(
        chat_events=chat_path,
        feedback_events=tmp_path / "missing-feedback.jsonl",
        review_queue=review_path,
        review_actions=tmp_path / "missing-actions.jsonl",
        output=output,
    )

    assert metrics["review_queue"]["total_review_items"] == 3
    assert metrics["review_queue"]["open_review_items"] == 2
    assert metrics["review_queue"]["high_priority_review_items"] == 1
    assert metrics["review_queue"]["medium_priority_review_items"] == 1
    assert metrics["review_queue"]["low_priority_review_items"] == 1
    assert metrics["review_queue"]["review_status_breakdown"]["resolved"] == 1
    assert metrics["review_queue"]["review_reason_breakdown"]["fallback_no_answer"] == 1
    assert metrics["quality_indicators"]["review_item_rate"] == 1.5


def test_review_action_type_and_status_breakdowns_are_correct(tmp_path):
    actions_path = tmp_path / "actions.jsonl"
    output = tmp_path / "metrics.json"
    _write_jsonl(
        actions_path,
        [
            _action(action_type="approve_candidate", status_after="resolved"),
            _action(action_type="mark_ignored", status_after="ignored"),
            _action(action_type="needs_new_faq", status_after="needs_followup"),
        ],
    )

    metrics = aggregate_product_metrics(
        chat_events=tmp_path / "missing-chat.jsonl",
        feedback_events=tmp_path / "missing-feedback.jsonl",
        review_queue=tmp_path / "missing-review.jsonl",
        review_actions=actions_path,
        output=output,
    )

    assert metrics["review_actions"]["total_review_actions"] == 3
    assert metrics["review_actions"]["action_type_breakdown"]["approve_candidate"] == 1
    assert metrics["review_actions"]["status_after_breakdown"]["ignored"] == 1
    assert metrics["review_actions"]["resolved_action_count"] == 1
    assert metrics["review_actions"]["ignored_action_count"] == 1
    assert metrics["review_actions"]["needs_followup_count"] == 1


def test_latency_avg_p50_p95_are_computed_for_valid_numeric_values(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    output = tmp_path / "metrics.json"
    _write_jsonl(
        chat_path,
        [
            _chat(latency_ms=10),
            _chat(latency_ms=20),
            _chat(latency_ms=30),
            _chat(latency_ms=40),
            _chat(latency_ms="bad"),
        ],
    )

    metrics = aggregate_product_metrics(
        chat_events=chat_path,
        feedback_events=tmp_path / "missing-feedback.jsonl",
        review_queue=tmp_path / "missing-review.jsonl",
        review_actions=tmp_path / "missing-actions.jsonl",
        output=output,
    )

    assert metrics["chat"]["avg_latency_ms"] == 25.0
    assert metrics["chat"]["p50_latency_ms"] == 25.0
    assert metrics["chat"]["p95_latency_ms"] == 38.5


def test_output_excludes_queries_answers_comments_candidate_payloads_and_private_chunks(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    review_path = tmp_path / "review.jsonl"
    actions_path = tmp_path / "actions.jsonl"
    output = tmp_path / "metrics.json"
    _write_jsonl(chat_path, [_chat()])
    _write_jsonl(feedback_path, [_feedback()])
    _write_jsonl(review_path, [_review()])
    _write_jsonl(actions_path, [_action()])

    aggregate_product_metrics(
        chat_events=chat_path,
        feedback_events=feedback_path,
        review_queue=review_path,
        review_actions=actions_path,
        output=output,
    )
    raw = output.read_text(encoding="utf-8")

    assert "出力してはいけない質問" not in raw
    assert "出力してはいけない承認済み回答" not in raw
    assert "出力してはいけないコメント" not in raw
    assert "出力してはいけない候補" not in raw
    assert "出力してはいけないレビュー質問" not in raw
    assert "出力してはいけない作業メモ" not in raw
    assert "candidate_payload" not in raw


def test_cli_writes_requested_output_path(tmp_path):
    chat_path = tmp_path / "chat.jsonl"
    output = tmp_path / "runs" / "metrics" / "product_metrics.json"
    _write_jsonl(chat_path, [_chat()])

    exit_code = main(
        [
            "--chat-events",
            str(chat_path),
            "--feedback-events",
            str(tmp_path / "missing-feedback.jsonl"),
            "--review-queue",
            str(tmp_path / "missing-review.jsonl"),
            "--review-actions",
            str(tmp_path / "missing-actions.jsonl"),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert output.exists()
    assert _load_json(output)["chat"]["total_questions"] == 1
