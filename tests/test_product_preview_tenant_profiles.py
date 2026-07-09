from __future__ import annotations

import json
from pathlib import Path

from rag_core import feedback_rerank_profile
from rag_core.approved_qa import ApprovedAnswer, ApprovedCitation
from webapi import main


def _resolution(
    *,
    tenant_id: str = "tenant-a",
    customer_id: str | None = "customer-a",
    resolved_profile: str | None = "production_safe",
    requested_profile: str | None = None,
    allowed_profiles: list[str] | None = None,
    tenant_status: str = "active",
    decision: str = "resolved",
    reasons: list[str] | None = None,
    warnings: list[str] | None = None,
):
    return {
        "tenant_id": tenant_id,
        "customer_id": customer_id,
        "resolved_profile": resolved_profile,
        "requested_profile": requested_profile,
        "default_profile": "production_safe",
        "allowed_profiles": allowed_profiles or ["production_safe", "production_low_cost", "pilot_high_accuracy"],
        "tenant_status": tenant_status,
        "decision": decision,
        "reasons": reasons or ["tenant_profile_selected"],
        "warnings": warnings or [],
    }


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


def _patch_preview(monkeypatch, candidates, *, audit_capture: dict | None = None):
    _disable_exact(monkeypatch)
    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main.approved_similar, "decide_approved_similar_candidate", _fake_decision)
    monkeypatch.setattr(
        main.approved_similar,
        "search_approved_similar_candidates",
        lambda *args, **kwargs: [dict(candidate) for candidate in candidates],
    )
    if audit_capture is None:
        monkeypatch.setattr(main, "append_product_preview_chat_audit_event", lambda event: True)
    else:
        monkeypatch.setattr(
            main,
            "append_product_preview_chat_audit_event",
            lambda event: audit_capture.update(event) or True,
        )


def _feedback_profile(path: Path, adjustments: dict) -> Path:
    path.write_text(
        json.dumps(
            {
                "profile_name": "feedback_preview",
                "profile_type": "approved_similar_feedback_rerank",
                "runtime_enabled": False,
                "production_enabled": False,
                "candidate_adjustments": adjustments,
                "safety": {
                    "no_runtime_ranking_change": True,
                    "no_auto_answer_enablement": True,
                    "requires_offline_evaluation_before_production": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _set_feedback_profile(monkeypatch, path: Path):
    monkeypatch.setattr(feedback_rerank_profile, "PROFILE_PATH", path)
    monkeypatch.setattr(main, "PROFILE_PATH", path)


def test_use_tenant_profile_omitted_preserves_product_profile_behavior(monkeypatch):
    _patch_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}])
    monkeypatch.setattr(main, "resolve_tenant_product_profile", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("tenant mapping should not load")))

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="候補", product_profile="dev_debug")
    )

    assert response["decision"]["product_profile"] == "dev_debug"
    assert "tenant_profile_resolution" not in response["profile_info"]


def test_use_tenant_profile_false_preserves_product_profile_behavior(monkeypatch):
    _patch_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}])
    monkeypatch.setattr(main, "resolve_tenant_product_profile", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("tenant mapping should not load")))

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="候補", product_profile="evaluation", use_tenant_profile=False)
    )

    assert response["decision"]["product_profile"] == "evaluation"
    assert "tenant_profile_resolution" not in response["decision"]


def test_use_tenant_profile_true_default_tenant_resolves_production_safe(monkeypatch):
    _patch_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}])

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="候補", tenant_id="default", use_tenant_profile=True)
    )

    assert response["decision"]["product_profile"] == "production_safe"
    assert response["decision"]["use_tenant_profile"] is True
    assert response["decision"]["tenant_profile_resolution"]["resolved_profile"] == "production_safe"
    assert response["profile_info"]["tenant_profile_resolution"]["decision"] == "resolved"


def test_tenant_profile_gates_feedback_and_feature_rerank(monkeypatch, tmp_path):
    _set_feedback_profile(monkeypatch, _feedback_profile(tmp_path / "feedback.json", {"qa-b": {"score_adjustment": 0.8}}))
    _patch_preview(
        monkeypatch,
        [
            {"qa_id": "qa-a", "question_text": "一般的な申請", "score": 0.9},
            {"qa_id": "qa-b", "question_text": "健康保険被保険者証の再発行", "score": 0.1},
        ],
    )
    monkeypatch.setattr(main, "resolve_tenant_product_profile", lambda *args, **kwargs: _resolution(resolved_profile="production_safe"))

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="保険証",
            tenant_id="tenant-a",
            use_tenant_profile=True,
            apply_feedback_preview=True,
            apply_feature_rerank=True,
            rerank_profile="feedback_preview",
            feature_rerank_profile="approved_similar_feature_preview",
        )
    )

    assert response["decision"]["effective_apply_feedback_preview"] is False
    assert response["decision"]["effective_apply_feature_rerank"] is True
    assert response["decision"]["feature_rerank_applied"] is True
    assert "feedback_preview_rerank_blocked_by_product_policy" in response["warnings"]


def test_requested_product_profile_inside_allowed_profiles_is_accepted(monkeypatch):
    _patch_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}])
    monkeypatch.setattr(
        main,
        "resolve_tenant_product_profile",
        lambda *args, **kwargs: _resolution(resolved_profile="production_low_cost", requested_profile="production_low_cost", reasons=["requested_profile_allowed"]),
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            tenant_id="tenant-a",
            product_profile="production_low_cost",
            use_tenant_profile=True,
        )
    )

    assert response["decision"]["product_profile"] == "production_low_cost"
    assert response["decision"]["tenant_profile_resolution"]["requested_profile"] == "production_low_cost"
    assert "requested_profile_allowed" in response["decision"]["tenant_profile_resolution"]["reasons"]


def test_requested_product_profile_outside_allowed_profiles_is_safely_ignored(monkeypatch):
    _patch_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}])
    monkeypatch.setattr(
        main,
        "resolve_tenant_product_profile",
        lambda *args, **kwargs: _resolution(
            resolved_profile="production_safe",
            requested_profile="dev_debug",
            allowed_profiles=["production_safe"],
            decision="fallback_default",
            reasons=["requested_profile_not_allowed"],
            warnings=["tenant_profile_request_ignored"],
        ),
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            tenant_id="tenant-a",
            product_profile="dev_debug",
            use_tenant_profile=True,
        )
    )

    assert response["decision"]["product_profile"] == "production_safe"
    assert response["decision"]["tenant_profile_resolution"]["decision"] == "fallback_default"
    assert "tenant_profile_request_ignored" in response["warnings"]
    assert "debug_comparison" not in response["decision"]["enabled_steps"]


def test_unknown_tenant_falls_back_safely_and_never_uses_dev_or_evaluation(monkeypatch):
    _patch_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}])
    monkeypatch.setattr(
        main,
        "resolve_tenant_product_profile",
        lambda *args, **kwargs: _resolution(
            tenant_id="missing",
            customer_id=None,
            resolved_profile="production_safe",
            tenant_status="unknown",
            decision="fallback_default",
            reasons=["unknown_tenant_default_profile"],
            warnings=["unknown_tenant", "default_profile_selected"],
        ),
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="候補", tenant_id="missing", use_tenant_profile=True)
    )

    resolved = response["decision"]["tenant_profile_resolution"]["resolved_profile"]
    assert resolved == "production_safe"
    assert resolved not in {"dev_debug", "evaluation"}
    assert "unknown_tenant" in response["warnings"]


def test_disabled_tenant_returns_safe_response_without_rerank_or_500(monkeypatch):
    _disable_exact(monkeypatch)
    monkeypatch.setattr(main, "_embedding_client", lambda: (_ for _ in ()).throw(AssertionError("search should not run")))
    monkeypatch.setattr(main, "append_product_preview_chat_audit_event", lambda event: True)
    monkeypatch.setattr(
        main,
        "resolve_tenant_product_profile",
        lambda *args, **kwargs: _resolution(
            resolved_profile=None,
            tenant_status="disabled",
            decision="disabled",
            reasons=["tenant_disabled"],
            warnings=["tenant_disabled"],
        ),
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            tenant_id="tenant-disabled",
            use_tenant_profile=True,
            apply_feedback_preview=True,
            apply_feature_rerank=True,
        )
    )

    assert response["answer_mode"] == "fallback_no_answer"
    assert response["confidence_route"] == "no_answer"
    assert response["candidates"] == []
    assert response["decision"]["effective_apply_feedback_preview"] is False
    assert response["decision"]["effective_apply_feature_rerank"] is False
    assert response["decision"]["tenant_profile_resolution"]["decision"] == "disabled"
    assert "tenant_disabled" in response["warnings"]


def test_tenant_metadata_appears_only_when_use_tenant_profile_true(monkeypatch):
    _patch_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}])

    without_tenant = main.chat_product_preview(main.ProductPreviewChatRequest(query="候補"))
    with_tenant = main.chat_product_preview(main.ProductPreviewChatRequest(query="候補", use_tenant_profile=True))

    assert "tenant_profile_resolution" not in without_tenant["profile_info"]
    assert "tenant_profile_resolution" in with_tenant["profile_info"]


def test_similar_candidate_only_remains_candidate_only_under_tenant_profile(monkeypatch):
    _patch_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "A", "approved_answer": "最終回答にしない"}])

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="候補", use_tenant_profile=True)
    )

    assert response["answer_mode"] == "approved_similar_candidate_only"
    assert response["confidence_route"] == "candidate_only"
    assert response["answer_text"] == ""


def test_exact_approved_answer_remains_exact_under_tenant_profile(monkeypatch):
    approved = ApprovedAnswer(
        qa_id="qa-exact",
        question="完全一致質問",
        normalized_question="完全一致質問",
        approved_answer="完全一致の承認済み回答です。",
        approved_citations=(ApprovedCitation(source_doc="faq.pdf", source_pages=(1,), chunk_id="c1"),),
        tenant_id="default",
        language="ja",
    )
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda *args, **kwargs: approved)
    monkeypatch.setattr(main, "append_product_preview_chat_audit_event", lambda event: True)

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="完全一致質問", use_tenant_profile=True)
    )

    assert response["answer_mode"] == "approved_exact_match"
    assert response["confidence_route"] == "exact_match"
    assert response["answer_text"] == "完全一致の承認済み回答です。"
    assert response["candidates"] == []


def test_production_low_cost_tenant_display_limit_is_applied(monkeypatch):
    captured = {}
    _disable_exact(monkeypatch)
    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main.approved_similar, "decide_approved_similar_candidate", _fake_decision)
    monkeypatch.setattr(main, "append_product_preview_chat_audit_event", lambda event: True)
    monkeypatch.setattr(main, "resolve_tenant_product_profile", lambda *args, **kwargs: _resolution(resolved_profile="production_low_cost"))

    def _search(query, **kwargs):
        captured["top_k"] = kwargs.get("top_k")
        return [
            {"qa_id": "qa-1", "question_text": "q1"},
            {"qa_id": "qa-2", "question_text": "q2"},
            {"qa_id": "qa-3", "question_text": "q3"},
        ]

    monkeypatch.setattr(main.approved_similar, "search_approved_similar_candidates", _search)

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="候補", top_k=10, use_tenant_profile=True)
    )

    assert captured["top_k"] == 3
    assert len(response["candidates"]) == 2


def test_invalid_overrides_cannot_enable_similar_auto_answer_or_llm_under_tenant_profile(monkeypatch):
    _patch_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}])

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            use_tenant_profile=True,
            product_profile_overrides={
                "answer_policy": {"allow_similar_auto_answer": True},
                "features": {"llm_answer": True},
            },
        )
    )

    assert response["answer_mode"] == "approved_similar_candidate_only"
    assert response["answer_text"] == ""
    assert "llm_answer" not in response["decision"]["enabled_steps"]
    assert "unsafe_enable_ignored:answer_policy.allow_similar_auto_answer" in response["decision"]["policy_warnings"]
    assert "unsafe_enable_ignored:features.llm_answer" in response["decision"]["policy_warnings"]


def test_audit_metadata_includes_safe_tenant_resolution_fields(monkeypatch):
    captured = {}
    _patch_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}], audit_capture=captured)
    monkeypatch.setattr(
        main,
        "resolve_tenant_product_profile",
        lambda *args, **kwargs: _resolution(resolved_profile="production_low_cost", requested_profile="production_low_cost"),
    )

    main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            tenant_id="tenant-a",
            customer_id="customer-a",
            product_profile="production_low_cost",
            use_tenant_profile=True,
        )
    )

    assert captured["use_tenant_profile"] is True
    assert captured["resolved_profile"] == "production_low_cost"
    assert captured["requested_profile"] == "production_low_cost"
    assert captured["tenant_status"] == "active"
    assert captured["tenant_profile_decision"] == "resolved"
    assert captured["effective_apply_feedback_preview"] is False
    assert captured["effective_apply_feature_rerank"] is False
    assert "tenant_profile_resolution" not in captured
