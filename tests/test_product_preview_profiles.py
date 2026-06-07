from __future__ import annotations

import json
from pathlib import Path

from rag_core import feedback_rerank_profile
from webapi import main


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


def _patch_product_preview(monkeypatch, candidates, *, audit_capture: dict | None = None):
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


def test_no_product_profile_preserves_existing_default_behavior(monkeypatch):
    _patch_product_preview(
        monkeypatch,
        [
            {"qa_id": "qa-a", "question_text": "A", "score": 0.1},
            {"qa_id": "qa-b", "question_text": "B", "score": 0.9},
        ],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="候補", top_k=2)
    )

    assert [candidate["qa_id"] for candidate in response["candidates"]] == ["qa-a", "qa-b"]
    assert "product_profile" not in response["profile_info"]
    assert "effective_apply_feedback_preview" not in response["decision"]


def test_production_safe_blocks_feedback_preview_even_if_requested(monkeypatch, tmp_path):
    _set_feedback_profile(
        monkeypatch,
        _feedback_profile(tmp_path / "feedback.json", {"qa-b": {"score_adjustment": 0.8}}),
    )
    _patch_product_preview(
        monkeypatch,
        [
            {"qa_id": "qa-a", "question_text": "A", "score": 0.9},
            {"qa_id": "qa-b", "question_text": "B", "score": 0.1},
        ],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            product_profile="production_safe",
            apply_feedback_preview=True,
            rerank_profile="feedback_preview",
        )
    )

    assert [candidate["qa_id"] for candidate in response["candidates"]] == ["qa-a", "qa-b"]
    assert response["decision"]["effective_apply_feedback_preview"] is False
    assert response["decision"]["feedback_preview_applied"] is False
    assert "feedback_preview_rerank_blocked_by_product_policy" in response["warnings"]


def test_production_safe_allows_feature_rerank_when_requested(monkeypatch):
    _patch_product_preview(
        monkeypatch,
        [
            {"qa_id": "qa-generic", "question_text": "一般的な申請", "score": 0.56},
            {"qa_id": "qa-insurance", "question_text": "健康保険被保険者証の再発行", "score": 0.50},
        ],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="保険証をなくした",
            product_profile="production_safe",
            apply_feature_rerank=True,
            feature_rerank_profile="approved_similar_feature_preview",
        )
    )

    assert response["decision"]["effective_apply_feature_rerank"] is True
    assert response["decision"]["feature_rerank_applied"] is True
    assert response["candidates"][0]["qa_id"] == "qa-insurance"
    assert response["answer_mode"] == "approved_similar_candidate_only"
    assert response["answer_text"] == ""


def test_production_low_cost_blocks_feedback_and_caps_display_candidates(monkeypatch, tmp_path):
    captured = {}
    _set_feedback_profile(
        monkeypatch,
        _feedback_profile(tmp_path / "feedback.json", {"qa-3": {"score_adjustment": 1.0}}),
    )
    _disable_exact(monkeypatch)
    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main.approved_similar, "decide_approved_similar_candidate", _fake_decision)

    def _search(query, **kwargs):
        captured["top_k"] = kwargs.get("top_k")
        return [
            {"qa_id": "qa-1", "question_text": "q1", "score": 0.9},
            {"qa_id": "qa-2", "question_text": "q2", "score": 0.8},
            {"qa_id": "qa-3", "question_text": "q3", "score": 0.7},
        ]

    monkeypatch.setattr(main.approved_similar, "search_approved_similar_candidates", _search)
    monkeypatch.setattr(main, "append_product_preview_chat_audit_event", lambda event: True)

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            top_k=10,
            product_profile="production_low_cost",
            apply_feedback_preview=True,
            rerank_profile="feedback_preview",
        )
    )

    assert captured["top_k"] == 3
    assert len(response["candidates"]) == 2
    assert response["decision"]["effective_apply_feedback_preview"] is False
    assert "feedback_preview_rerank" not in response["decision"]["enabled_steps"]
    assert "debug_comparison" not in response["decision"]["enabled_steps"]
    assert "llm_answer" not in response["decision"]["enabled_steps"]


def test_pilot_high_accuracy_allows_feedback_and_feature_when_requested(monkeypatch, tmp_path):
    _set_feedback_profile(
        monkeypatch,
        _feedback_profile(tmp_path / "feedback.json", {"qa-insurance": {"score_adjustment": 0.02}}),
    )
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-insurance", "question_text": "健康保険被保険者証の再発行", "score": 0.5}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="保険証",
            product_profile="pilot_high_accuracy",
            apply_feedback_preview=True,
            rerank_profile="feedback_preview",
            apply_feature_rerank=True,
            feature_rerank_profile="approved_similar_feature_preview",
        )
    )

    assert response["decision"]["effective_apply_feedback_preview"] is True
    assert response["decision"]["effective_apply_feature_rerank"] is True
    assert response["decision"]["feedback_preview_applied"] is True
    assert response["decision"]["feature_rerank_applied"] is True
    assert response["candidates"][0]["feedback_preview_score_adjustment"] == 0.02
    assert response["candidates"][0]["feature_score_adjustment"] > 0
    assert response["answer_mode"] == "approved_similar_candidate_only"
    assert response["answer_text"] == ""


def test_evaluation_profile_returns_policy_metadata_without_auto_answer(monkeypatch):
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="候補", product_profile="evaluation")
    )

    assert response["decision"]["product_profile"] == "evaluation"
    assert response["decision"]["runtime_serving"] is False
    assert "debug_comparison" in response["decision"]["enabled_steps"]
    assert response["answer_mode"] == "approved_similar_candidate_only"
    assert response["answer_text"] == ""


def test_dev_debug_profile_returns_policy_metadata_without_auto_answer(monkeypatch):
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="候補", product_profile="dev_debug")
    )

    assert response["decision"]["product_profile"] == "dev_debug"
    assert "debug_comparison" in response["decision"]["enabled_steps"]
    assert response["answer_mode"] == "approved_similar_candidate_only"
    assert response["answer_text"] == ""


def test_unknown_product_profile_falls_back_safely(monkeypatch):
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(query="候補", product_profile="does_not_exist")
    )

    assert response["decision"]["product_profile"] == "default"
    assert "product_profile_fallback_default" in response["warnings"]


def test_invalid_product_profile_overrides_do_not_crash(monkeypatch):
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            product_profile="production_safe",
            product_profile_overrides={"features": {"unknown_feature": True}, "unknown": True},
        )
    )

    assert "unknown_override:features.unknown_feature" in response["decision"]["policy_warnings"]
    assert "unknown_override:unknown" in response["decision"]["policy_warnings"]


def test_overrides_cannot_enable_similar_auto_answer_or_llm(monkeypatch):
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            product_profile="production_safe",
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


def test_overrides_can_reduce_max_candidates_display(monkeypatch):
    _patch_product_preview(
        monkeypatch,
        [
            {"qa_id": "qa-1", "question_text": "q1", "score": 0.9},
            {"qa_id": "qa-2", "question_text": "q2", "score": 0.8},
            {"qa_id": "qa-3", "question_text": "q3", "score": 0.7},
        ],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            top_k=5,
            product_profile="pilot_high_accuracy",
            product_profile_overrides={"limits": {"max_candidates_display": 1}},
        )
    )

    assert len(response["candidates"]) == 1
    assert response["decision"]["max_candidates_display"] == 1


def test_audit_metadata_includes_safe_profile_policy_fields(monkeypatch):
    captured = {}
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-a", "question_text": "A", "score": 0.9}],
        audit_capture=captured,
    )

    main.chat_product_preview(
        main.ProductPreviewChatRequest(query="候補", product_profile="production_safe")
    )

    assert captured["product_profile"] == "production_safe"
    assert captured["operation_mode"] == "production_safe"
    assert "feature_rerank" in captured["enabled_steps"]
    assert captured["effective_apply_feedback_preview"] is False
    assert captured["effective_apply_feature_rerank"] is False
    assert "answer_policy" not in captured
