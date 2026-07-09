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


def _feature_profile(path: Path, **overrides) -> Path:
    payload = {
        "profile_name": "approved_similar_feature_preview",
        "profile_type": "approved_similar_feature_rerank",
        "runtime_enabled": False,
        "production_enabled": False,
        "weights": {
            "base_score": 1.0,
            "synonym_overlap": 0.08,
            "business_term_overlap": 0.10,
            "negative_mismatch_penalty": 0.18,
        },
        "limits": {"max_positive_adjustment": 0.12, "max_negative_penalty": 0.20},
        "safety": {
            "no_runtime_ranking_change": True,
            "no_auto_answer_enablement": True,
            "requires_offline_evaluation_before_production": True,
        },
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _set_feature_profile_path(monkeypatch, path: Path):
    monkeypatch.setattr(main.approved_similar_feature_reranker, "DEFAULT_PROFILE_PATH", path)


def test_product_preview_default_behavior_unchanged_when_feature_rerank_false(monkeypatch, tmp_path):
    _set_feature_profile_path(monkeypatch, tmp_path / "missing.json")
    _patch_product_preview(
        monkeypatch,
        [
            {"qa_id": "qa-a", "question_text": "A", "score": 0.1},
            {"qa_id": "qa-b", "question_text": "B", "score": 0.9},
        ],
    )

    response = main.chat_product_preview(main.ProductPreviewChatRequest(query="候補"))

    assert [candidate["qa_id"] for candidate in response["candidates"]] == ["qa-a", "qa-b"]
    assert response["decision"]["apply_feature_rerank"] is False
    assert response["decision"]["feature_rerank_applied"] is False
    assert "feature_score_adjustment" not in response["candidates"][0]


def test_missing_feature_profile_returns_unchanged_candidates_with_warning(monkeypatch, tmp_path):
    _set_feature_profile_path(monkeypatch, tmp_path / "missing.json")
    _patch_product_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "保険証", "score": 0.5}])

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="保険証",
            apply_feature_rerank=True,
            feature_rerank_profile="approved_similar_feature_preview",
        )
    )

    assert response["candidates"][0]["qa_id"] == "qa-a"
    assert response["decision"]["feature_rerank_missing_profile"] is True
    assert "feature_rerank_profile_missing" in response["warnings"]


def test_unknown_feature_profile_is_ignored_without_path_loading(monkeypatch, tmp_path):
    _set_feature_profile_path(monkeypatch, tmp_path / "missing.json")
    _patch_product_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "保険証", "score": 0.5}])

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="保険証",
            apply_feature_rerank=True,
            feature_rerank_profile="../not_allowed.json",
        )
    )

    assert response["candidates"][0]["qa_id"] == "qa-a"
    assert response["decision"]["feature_rerank_profile_valid"] is False
    assert response["decision"]["feature_rerank_safety_checked"] is True
    assert "feature_rerank_profile_ignored" in response["warnings"]


def test_invalid_feature_profile_returns_unchanged_candidates(monkeypatch, tmp_path):
    profile = _feature_profile(tmp_path / "feature.json", production_enabled=True)
    _set_feature_profile_path(monkeypatch, profile)
    _patch_product_preview(monkeypatch, [{"qa_id": "qa-a", "question_text": "保険証", "score": 0.5}])

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="保険証",
            apply_feature_rerank=True,
            feature_rerank_profile="approved_similar_feature_preview",
        )
    )

    assert response["candidates"][0]["qa_id"] == "qa-a"
    assert response["decision"]["feature_rerank_profile_valid"] is False
    assert "feature_rerank_profile_invalid" in response["warnings"]
    assert "feature_score_adjustment" not in response["candidates"][0]


def test_valid_feature_rerank_attaches_metadata(monkeypatch, tmp_path):
    profile = _feature_profile(tmp_path / "feature.json")
    _set_feature_profile_path(monkeypatch, profile)
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-insurance", "question_text": "健康保険被保険者証の再発行", "score": 0.5}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="保険証をなくした",
            apply_feature_rerank=True,
            feature_rerank_profile="approved_similar_feature_preview",
        )
    )
    candidate = response["candidates"][0]

    assert candidate["feature_rerank_applied"] is True
    assert candidate["feature_score_adjustment"] > 0
    assert candidate["feature_synonym_overlap_score"] > 0
    assert candidate["feature_business_term_overlap_score"] > 0
    assert candidate["feature_matched_canonicals"] == ["健康保険証"]
    assert "feature_rerank_applied_preview_only" in response["warnings"]


def test_synonym_business_overlap_can_improve_candidate_ordering(monkeypatch, tmp_path):
    profile = _feature_profile(tmp_path / "feature.json")
    _set_feature_profile_path(monkeypatch, profile)
    _patch_product_preview(
        monkeypatch,
        [
            {"qa_id": "qa-generic", "question_text": "一般的な申請手続き", "score": 0.56},
            {"qa_id": "qa-insurance", "question_text": "健康保険被保険者証の再発行", "score": 0.50},
        ],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="保険証をなくした",
            apply_feature_rerank=True,
            feature_rerank_profile="approved_similar_feature_preview",
        )
    )

    assert [candidate["qa_id"] for candidate in response["candidates"]] == ["qa-insurance", "qa-generic"]
    assert response["decision"]["feature_rerank_reordered"] is True


def test_negative_mismatch_can_demote_candidate(monkeypatch, tmp_path):
    profile = _feature_profile(tmp_path / "feature.json")
    _set_feature_profile_path(monkeypatch, profile)
    _patch_product_preview(
        monkeypatch,
        [
            {"qa_id": "qa-opposite", "question_text": "扶養から外れる手続き", "score": 0.90},
            {"qa_id": "qa-safe", "question_text": "被扶養者として扶養に入る手続き", "score": 0.80},
        ],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="扶養に入るには",
            apply_feature_rerank=True,
            feature_rerank_profile="approved_similar_feature_preview",
        )
    )

    assert [candidate["qa_id"] for candidate in response["candidates"]] == ["qa-safe", "qa-opposite"]
    assert response["candidates"][1]["feature_negative_mismatch"] is True
    assert response["decision"]["feature_rerank_negative_mismatch_count"] == 1


def test_no_numeric_base_score_preserves_order(monkeypatch, tmp_path):
    profile = _feature_profile(tmp_path / "feature.json")
    _set_feature_profile_path(monkeypatch, profile)
    _patch_product_preview(
        monkeypatch,
        [
            {"qa_id": "qa-first", "question_text": "一般的な手続き"},
            {"qa_id": "qa-insurance", "question_text": "健康保険被保険者証の再発行"},
        ],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="保険証をなくした",
            apply_feature_rerank=True,
            feature_rerank_profile="approved_similar_feature_preview",
        )
    )

    assert [candidate["qa_id"] for candidate in response["candidates"]] == ["qa-first", "qa-insurance"]
    assert response["candidates"][1]["feature_base_score"] is None
    assert response["decision"]["feature_rerank_reordered"] is False


def test_feature_rerank_keeps_similar_candidate_answer_suppressed(monkeypatch, tmp_path):
    profile = _feature_profile(tmp_path / "feature.json")
    _set_feature_profile_path(monkeypatch, profile)
    _patch_product_preview(
        monkeypatch,
        [
            {
                "qa_id": "qa-secret",
                "question_text": "健康保険被保険者証の再発行",
                "approved_answer_preview": "承認済み回答プレビュー",
                "score": 0.9,
            }
        ],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="保険証",
            apply_feature_rerank=True,
            feature_rerank_profile="approved_similar_feature_preview",
        )
    )

    assert response["answer_mode"] == "approved_similar_candidate_only"
    assert response["confidence_route"] == "candidate_only"
    assert response["answer_text"] == ""
    assert response["candidates"][0]["approved_answer_preview"] == "承認済み回答プレビュー"


def test_feedback_preview_and_feature_rerank_can_both_be_requested(monkeypatch, tmp_path):
    feature_profile = _feature_profile(tmp_path / "feature.json")
    _set_feature_profile_path(monkeypatch, feature_profile)
    feedback_profile = tmp_path / "feedback.json"
    feedback_profile.write_text(
        json.dumps(
            {
                "profile_name": "feedback_preview",
                "profile_type": "approved_similar_feedback_rerank",
                "runtime_enabled": False,
                "production_enabled": False,
                "candidate_adjustments": {
                    "qa-insurance": {
                        "score_adjustment": 0.02,
                        "positive_count": 1,
                        "negative_count": 0,
                        "review_needed_count": 0,
                    }
                },
                "safety": {
                    "no_runtime_ranking_change": True,
                    "no_auto_answer_enablement": True,
                    "requires_offline_evaluation_before_production": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(feedback_rerank_profile, "PROFILE_PATH", feedback_profile)
    monkeypatch.setattr(main, "PROFILE_PATH", feedback_profile)
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-insurance", "question_text": "健康保険被保険者証の再発行", "score": 0.5}],
    )

    response = main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="保険証",
            apply_feedback_preview=True,
            rerank_profile="feedback_preview",
            apply_feature_rerank=True,
            feature_rerank_profile="approved_similar_feature_preview",
        )
    )

    candidate = response["candidates"][0]
    assert response["decision"]["feedback_preview_applied"] is True
    assert response["decision"]["feature_rerank_applied"] is True
    assert candidate["feedback_preview_score_adjustment"] == 0.02
    assert candidate["feature_score_adjustment"] > 0


def test_feature_rerank_audit_metadata_is_bounded(monkeypatch, tmp_path):
    profile = _feature_profile(tmp_path / "feature.json")
    _set_feature_profile_path(monkeypatch, profile)
    captured: dict = {}
    _patch_product_preview(
        monkeypatch,
        [{"qa_id": "qa-insurance", "question_text": "健康保険被保険者証の再発行", "score": 0.5}],
        audit_capture=captured,
    )

    main.chat_product_preview(
        main.ProductPreviewChatRequest(
            query="保険証",
            apply_feature_rerank=True,
            feature_rerank_profile="approved_similar_feature_preview",
        )
    )

    assert captured["apply_feature_rerank"] is True
    assert captured["feature_rerank_profile"] == "approved_similar_feature_preview"
    assert captured["feature_rerank_applied"] is True
    assert captured["feature_rerank_adjusted_candidate_count"] == 1
    assert "weights" not in captured
    assert "candidate_adjustments" not in captured


def test_product_preview_page_references_feature_rerank_ui():
    response = main.product_preview_page()
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "apply-feature-rerank" in body
    assert "Apply Japanese feature rerank" in body
    assert "feature_rerank_profile" in body
