from __future__ import annotations

from rag_core.product_contract import (
    ANSWER_MODE_APPROVED_SIMILAR_CANDIDATE_ONLY,
    ANSWER_MODE_RAG_ANSWER,
    CONFIDENCE_ROUTE_CANDIDATE_ONLY,
    CONFIDENCE_ROUTE_RAG,
    build_audit_event,
    build_candidate_contract,
    build_product_answer_envelope,
    planned_safe_stages,
)


def test_product_envelope_includes_required_keys_and_feedback_token():
    envelope = build_product_answer_envelope(
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-a",
        answer_mode=ANSWER_MODE_RAG_ANSWER,
        answer_text="回答です",
        confidence_route=CONFIDENCE_ROUTE_RAG,
        citations=[{"source_doc": "doc.pdf"}],
        candidates=[],
        decision={"route": "rag"},
        profile_info={"keyword_profile": "default"},
        warnings=["debug"],
    )

    assert set(envelope.keys()) == {
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
    assert envelope["feedback_token"]


def test_candidate_only_envelope_does_not_expose_approved_answer_as_final_answer():
    envelope = build_product_answer_envelope(
        request_id="req-1",
        trace_id="trace-1",
        answer_mode=ANSWER_MODE_APPROVED_SIMILAR_CANDIDATE_ONLY,
        answer_text="承認済み回答の全文",
        confidence_route=CONFIDENCE_ROUTE_CANDIDATE_ONLY,
    )

    assert envelope["answer_text"] == ""


def test_candidate_preview_is_bounded():
    candidate = build_candidate_contract(
        {
            "qa_id": "qa_1",
            "question_text": "question",
            "approved_answer": "あ" * 80,
            "hybrid_score": 0.9,
            "matched_terms": ["自由回答"],
            "source_doc": "doc.pdf",
            "source_pages": [1],
        },
        max_preview_chars=30,
    )

    assert candidate["qa_id"] == "qa_1"
    assert len(candidate["approved_answer_preview"]) <= 30
    assert candidate["approved_answer_preview"].endswith("...[truncated]")
    assert candidate["scores"]["hybrid_score"] == 0.9


def test_audit_event_includes_required_keys_and_bounds_query():
    event = build_audit_event(
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-a",
        user_query="q" * 100,
        answer_mode=ANSWER_MODE_RAG_ANSWER,
        selected_qa_id="qa_1",
        candidate_ids=["qa_1", "qa_2"],
        decision_route="high_confidence_answer",
        keyword_profile="kw",
        threshold_profile="th",
        latency_ms=12,
        max_query_chars=25,
    )

    assert set(event.keys()) == {
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
    assert len(event["user_query"]) <= 25
    assert event["feedback_token"]


def test_route_planning_returns_expected_ordered_stages():
    assert planned_safe_stages() == [
        "approved_exact_match",
        "approved_similar_candidate",
        "normal_rag",
        "fallback",
        "human_escalation",
    ]
