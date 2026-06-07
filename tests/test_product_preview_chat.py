from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import config
from rag_core import audit_log
from rag_core.approved_qa import ApprovedAnswer, ApprovedCitation
from webapi import main


REQUIRED_ENVELOPE_KEYS = {
    "request_id",
    "trace_id",
    "tenant_id",
    "answer_mode",
    "answer_text",
    "confidence_route",
    "citations",
    "candidates",
    "decision",
    "profile_info",
    "warnings",
    "feedback_token",
}


@pytest.fixture(autouse=True)
def _disable_product_preview_audit_write(monkeypatch):
    monkeypatch.setattr(main, "append_product_preview_chat_audit_event", lambda event: True)


def _disable_exact(monkeypatch):
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda *args, **kwargs: None)


def _fake_decision(candidates):
    return {
        "route": "candidate_only" if candidates else "no_candidate",
        "qa_id": candidates[0].get("qa_id") if candidates else None,
        "reasons": [],
        "blocking_flags": {},
        "score_snapshot": {},
        "top_candidate_summary": None,
    }


def test_product_preview_response_includes_envelope_keys_and_feedback_token(monkeypatch):
    _disable_exact(monkeypatch)
    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main.approved_similar, "decide_approved_similar_candidate", _fake_decision)
    monkeypatch.setattr(
        main.approved_similar,
        "search_approved_similar_candidates",
        lambda *args, **kwargs: [
            {
                "qa_id": "qa-1",
                "question_text": "自由回答は含まれますか？",
                "approved_answer_preview": "承認済み回答のプレビューです。",
                "hybrid_score": 0.9,
                "matched_terms": ["自由回答"],
            }
        ],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="自由回答は含まれますか？")
    )

    assert REQUIRED_ENVELOPE_KEYS <= set(response)
    assert response["feedback_token"]
    assert response["answer_mode"] == "approved_similar_candidate_only"
    assert response["confidence_route"] == "candidate_only"


def test_product_preview_accepts_message_when_query_missing(monkeypatch):
    _disable_exact(monkeypatch)
    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main.approved_similar, "decide_approved_similar_candidate", _fake_decision)
    monkeypatch.setattr(
        main.approved_similar,
        "search_approved_similar_candidates",
        lambda query, **kwargs: [{"qa_id": "qa-message", "question_text": query}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(message="メッセージから検索します")
    )

    assert response["candidates"][0]["qa_id"] == "qa-message"
    assert response["decision"]["audit_event_preview"]["user_query"] == "メッセージから検索します"


def test_product_preview_missing_query_and_message_returns_400():
    with pytest.raises(HTTPException) as exc_info:
        main.chat_product_preview(main.ProductPreviewChatRequest())

    assert exc_info.value.status_code == 400


def test_product_preview_similar_candidates_do_not_populate_final_answer_text(monkeypatch):
    _disable_exact(monkeypatch)
    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main.approved_similar, "decide_approved_similar_candidate", _fake_decision)
    monkeypatch.setattr(
        main.approved_similar,
        "search_approved_similar_candidates",
        lambda *args, **kwargs: [
            {
                "qa_id": "qa-secret",
                "question_text": "類似質問",
                "approved_answer": "この承認済み回答を最終回答に入れてはいけません。",
                "matched_terms": ["類似"],
            }
        ],
    )

    response = main.chat_product_preview(main.ProductPreviewChatRequest(query="類似質問"))

    assert response["answer_mode"] == "approved_similar_candidate_only"
    assert response["answer_text"] == ""
    assert "この承認済み回答" in response["candidates"][0]["approved_answer_preview"]
    assert "approved_similar_candidates_are_preview_only" in response["warnings"]


def test_product_preview_candidates_are_bounded_by_top_k(monkeypatch):
    captured = {}
    _disable_exact(monkeypatch)
    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main.approved_similar, "decide_approved_similar_candidate", _fake_decision)

    def _fake_search(query, **kwargs):
        captured["top_k"] = kwargs.get("top_k")
        return [
            {"qa_id": "qa-1", "question_text": "q1"},
            {"qa_id": "qa-2", "question_text": "q2"},
            {"qa_id": "qa-3", "question_text": "q3"},
        ]

    monkeypatch.setattr(main.approved_similar, "search_approved_similar_candidates", _fake_search)

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="候補", top_k=2)
    )

    assert captured["top_k"] == 2
    assert [candidate["qa_id"] for candidate in response["candidates"]] == ["qa-1", "qa-2"]


def test_product_preview_no_candidate_path_returns_no_answer(monkeypatch):
    _disable_exact(monkeypatch)
    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main.approved_similar, "search_approved_similar_candidates", lambda *args, **kwargs: [])

    response = main.chat_product_preview(main.ProductPreviewChatRequest(query="見つからない質問"))

    assert response["answer_mode"] == "fallback_no_answer"
    assert response["confidence_route"] == "no_answer"
    assert response["answer_text"] == ""
    assert response["candidates"] == []
    assert "no_approved_similar_candidate_found" in response["warnings"]


def test_product_preview_exact_match_returns_exact_answer(monkeypatch):
    approved = ApprovedAnswer(
        qa_id="qa-exact",
        question="完全一致質問",
        normalized_question="完全一致質問",
        approved_answer="完全一致の承認済み回答です。",
        approved_citations=(
            ApprovedCitation(source_doc="faq.pdf", source_pages=(1,), chunk_id="c1"),
        ),
        tenant_id="default",
        language="ja",
    )
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda *args, **kwargs: approved)

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="完全一致質問")
    )

    assert response["answer_mode"] == "approved_exact_match"
    assert response["confidence_route"] == "exact_match"
    assert response["answer_text"] == "完全一致の承認済み回答です。"
    assert response["citations"][0]["source_doc"] == "faq.pdf"
    assert response["citations"][0]["source_pages"] == [1]
    assert response["citations"][0]["chunk_id"] == "c1"
    assert response["citations"][0]["title"] is None
    assert response["candidates"] == []


def test_product_preview_appends_bounded_jsonl_audit_event(monkeypatch, tmp_path):
    _disable_exact(monkeypatch)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(main, "append_product_preview_chat_audit_event", audit_log.append_product_preview_chat_audit_event)
    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main.approved_similar, "decide_approved_similar_candidate", _fake_decision)
    monkeypatch.setattr(
        main.approved_similar,
        "search_approved_similar_candidates",
        lambda *args, **kwargs: [
            {
                "qa_id": "qa-audit",
                "question_text": "監査対象",
                "approved_answer_preview": "このプレビュー本文は監査ログに入れません。",
            }
        ],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="監査ログを書きます", top_k=1)
    )

    audit_path = tmp_path / "runs" / "audit" / "chat_events.jsonl"
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    required = {
        "request_id",
        "trace_id",
        "tenant_id",
        "user_query",
        "answer_mode",
        "selected_qa_id",
        "candidate_ids",
        "decision_route",
        "keyword_profile",
        "threshold_profile",
        "latency_ms",
        "timestamp",
        "feedback_token",
    }
    assert required <= set(event)
    assert event["kind"] == "product_preview_chat"
    assert event["feedback_token"] == response["feedback_token"]
    assert event["candidate_ids"] == ["qa-audit"]
    assert event["candidate_count"] == 1
    assert event["top_k"] == 1
    assert event["auto_answer_suppressed_for_similar_candidates"] is True
    assert event["exact_match_checked"] is True
    assert "このプレビュー本文" not in lines[0]


def test_product_preview_audit_bounds_user_query(monkeypatch, tmp_path):
    _disable_exact(monkeypatch)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(main, "append_product_preview_chat_audit_event", audit_log.append_product_preview_chat_audit_event)
    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main.approved_similar, "search_approved_similar_candidates", lambda *args, **kwargs: [])
    query = "長い質問" * 200

    main.chat_product_preview(main.ProductPreviewChatRequest(query=query))

    event = json.loads((tmp_path / "runs" / "audit" / "chat_events.jsonl").read_text(encoding="utf-8"))
    assert len(event["user_query"]) <= 500
    assert event["user_query"].endswith("...[truncated]")


def test_product_preview_no_candidate_fallback_writes_audit_event(monkeypatch, tmp_path):
    _disable_exact(monkeypatch)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(main, "append_product_preview_chat_audit_event", audit_log.append_product_preview_chat_audit_event)
    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main.approved_similar, "search_approved_similar_candidates", lambda *args, **kwargs: [])

    response = main.chat_product_preview(main.ProductPreviewChatRequest(query="候補なし"))

    event = json.loads((tmp_path / "runs" / "audit" / "chat_events.jsonl").read_text(encoding="utf-8"))
    assert response["answer_mode"] == "fallback_no_answer"
    assert event["answer_mode"] == "fallback_no_answer"
    assert event["candidate_ids"] == []
    assert event["candidate_count"] == 0


def test_product_preview_audit_write_failure_does_not_fail_endpoint(monkeypatch):
    _disable_exact(monkeypatch)
    monkeypatch.setattr(main, "append_product_preview_chat_audit_event", lambda event: False)
    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main.approved_similar, "search_approved_similar_candidates", lambda *args, **kwargs: [])

    response = main.chat_product_preview(main.ProductPreviewChatRequest(query="監査失敗"))

    assert response["answer_mode"] == "fallback_no_answer"
    assert response["decision"]["audit_persisted"] is False
    assert "product_preview_audit_logging_failed" in response["warnings"]
