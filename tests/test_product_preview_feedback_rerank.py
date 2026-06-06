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


def _patch_product_preview(
    monkeypatch,
    candidates,
    *,
    audit_capture: dict | None = None,
):
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


def _set_profile_path(monkeypatch, path: Path):
    monkeypatch.setattr(feedback_rerank_profile, "PROFILE_PATH", path)
    monkeypatch.setattr(main, "PROFILE_PATH", path)


def _valid_profile(adjustments):
    return {
        "profile_name": "feedback_preview",
        "profile_type": "approved_similar_feedback_rerank",
        "version": 1,
        "runtime_enabled": False,
        "production_enabled": False,
        "candidate_adjustments": adjustments,
        "safety": {
            "no_runtime_ranking_change": True,
            "no_auto_answer_enablement": True,
            "requires_offline_evaluation_before_production": True,
        },
    }


def test_product_preview_default_behavior_does_not_apply_feedback_rerank(monkeypatch, tmp_path):
    _set_profile_path(monkeypatch, tmp_path / "missing.json")
    _patch_product_preview(
        monkeypatch,
        [
            {"qa_id": "qa-a", "question_text": "A", "hybrid_score": 0.1},
            {"qa_id": "qa-b", "question_text": "B", "hybrid_score": 0.9},
        ],
    )

    response = main.chat_product_preview(main.ProductPreviewChatRequest(query="候補"))

    assert [candidate["qa_id"] for candidate in response["candidates"]] == ["qa-a", "qa-b"]
    assert response["decision"]["apply_feedback_preview"] is False
    assert response["decision"]["feedback_preview_applied"] is False
    assert not any("feedback_preview_" in key for key in response["candidates"][0])


def test_product_preview_unknown_rerank_profile_is_ignored_safely(monkeypatch, tmp_path):
    _set_profile_path(monkeypatch, tmp_path / "missing.json")
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-a", "question_text": "A", "hybrid_score": 0.5}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            apply_feedback_preview=True,
            rerank_profile="../not_allowed.json",
        )
    )

    assert response["candidates"][0]["qa_id"] == "qa-a"
    assert response["decision"]["feedback_preview_invalid_profile"] is True
    assert "feedback_preview_rerank_profile_ignored" in response["warnings"]


def test_product_preview_missing_feedback_profile_is_non_fatal(monkeypatch, tmp_path):
    _set_profile_path(monkeypatch, tmp_path / "missing.json")
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-a", "question_text": "A", "hybrid_score": 0.5}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            apply_feedback_preview=True,
            rerank_profile="feedback_preview",
        )
    )

    assert response["candidates"][0]["qa_id"] == "qa-a"
    assert response["decision"]["feedback_preview_missing_profile"] is True
    assert "feedback_preview_rerank_profile_missing" in response["warnings"]


def test_product_preview_invalid_feedback_profile_is_non_fatal(monkeypatch, tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps({"profile_name": "feedback_preview", "production_enabled": True}),
        encoding="utf-8",
    )
    _set_profile_path(monkeypatch, profile_path)
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-a", "question_text": "A", "hybrid_score": 0.5}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            apply_feedback_preview=True,
            rerank_profile="feedback_preview",
        )
    )

    assert response["candidates"][0]["qa_id"] == "qa-a"
    assert response["decision"]["feedback_preview_invalid_profile"] is True
    assert "feedback_preview_rerank_profile_invalid" in response["warnings"]


def test_product_preview_valid_profile_attaches_adjustment_metadata(monkeypatch, tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            _valid_profile(
                {
                    "qa-a": {
                        "score_adjustment": 0.03,
                        "positive_count": 2,
                        "negative_count": 1,
                        "review_needed_count": 1,
                        "reasons": ["positive_feedback"],
                    }
                }
            )
        ),
        encoding="utf-8",
    )
    _set_profile_path(monkeypatch, profile_path)
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-a", "question_text": "A", "hybrid_score": 0.5}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            apply_feedback_preview=True,
            rerank_profile="feedback_preview",
        )
    )
    candidate = response["candidates"][0]

    assert candidate["feedback_preview_score_adjustment"] == 0.03
    assert candidate["feedback_preview_adjusted_score"] == 0.53
    assert candidate["feedback_preview_positive_count"] == 2
    assert candidate["feedback_preview_negative_count"] == 1
    assert candidate["feedback_preview_review_needed_count"] == 1
    assert candidate["feedback_preview_reasons"] == ["positive_feedback"]
    assert "feedback_preview_rerank_applied_preview_only" in response["warnings"]


def test_product_preview_valid_profile_can_reorder_by_adjusted_score(monkeypatch, tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            _valid_profile(
                {
                    "qa-a": {
                        "score_adjustment": 0.05,
                        "positive_count": 1,
                        "negative_count": 0,
                        "review_needed_count": 0,
                        "reasons": ["positive_feedback"],
                    }
                }
            )
        ),
        encoding="utf-8",
    )
    _set_profile_path(monkeypatch, profile_path)
    _patch_product_preview(
        monkeypatch,
        [
            {"qa_id": "qa-b", "question_text": "B", "hybrid_score": 0.6},
            {"qa_id": "qa-a", "question_text": "A", "hybrid_score": 0.56},
        ],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            apply_feedback_preview=True,
            rerank_profile="feedback_preview",
        )
    )

    assert [candidate["qa_id"] for candidate in response["candidates"]] == ["qa-a", "qa-b"]
    assert response["decision"]["feedback_preview_reordered"] is True


def test_feedback_rerank_keeps_similar_candidate_answer_suppressed(monkeypatch, tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(_valid_profile({})), encoding="utf-8")
    _set_profile_path(monkeypatch, profile_path)
    _patch_product_preview(
        monkeypatch,
        [
            {
                "qa_id": "qa-secret",
                "question_text": "類似質問",
                "approved_answer": "この承認済み回答を最終回答に入れてはいけません。",
                "hybrid_score": 0.9,
            }
        ],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="類似質問",
            apply_feedback_preview=True,
            rerank_profile="feedback_preview",
        )
    )

    assert response["answer_mode"] == "approved_similar_candidate_only"
    assert response["confidence_route"] == "candidate_only"
    assert response["answer_text"] == ""
    assert "この承認済み回答" in response["candidates"][0]["approved_answer_preview"]


def test_product_preview_audit_includes_feedback_preview_flags_without_profile(monkeypatch, tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(_valid_profile({"qa-a": {"score_adjustment": 0.02}})),
        encoding="utf-8",
    )
    _set_profile_path(monkeypatch, profile_path)
    captured: dict = {}
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-a", "question_text": "A", "hybrid_score": 0.5}],
        audit_capture=captured,
    )

    main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="候補",
            apply_feedback_preview=True,
            rerank_profile="feedback_preview",
        )
    )

    assert captured["apply_feedback_preview"] is True
    assert captured["rerank_profile"] == "feedback_preview"
    assert captured["feedback_preview_applied"] is True
    assert captured["feedback_preview_adjusted_candidate_count"] == 1
    assert "candidate_adjustments" not in captured


def test_product_preview_page_references_feedback_preview_ui():
    response = main.product_preview_page()
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "apply-feedback-preview" in body
    assert "Apply feedback preview rerank" in body
    assert "rerank_profile" in body
