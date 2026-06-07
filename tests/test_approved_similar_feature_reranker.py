from __future__ import annotations

import json

from rag_core.approved_similar_feature_reranker import (
    apply_feature_rerank,
    load_feature_rerank_profile,
)
from rag_core.japanese_normalizer import load_japanese_business_synonyms


def _profile(**overrides):
    profile = {
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
        "limits": {
            "max_positive_adjustment": 0.12,
            "max_negative_penalty": 0.20,
        },
        "safety": {
            "no_runtime_ranking_change": True,
            "no_auto_answer_enablement": True,
            "requires_offline_evaluation_before_production": True,
        },
    }
    profile.update(overrides)
    return profile


def test_feature_profile_loads_safely(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(_profile(), ensure_ascii=False), encoding="utf-8")

    profile = load_feature_rerank_profile(path)

    assert profile["profile_name"] == "approved_similar_feature_preview"
    assert profile["production_enabled"] is False
    assert profile["_metadata"]["loaded"] is True


def test_missing_profile_uses_safe_default(tmp_path):
    profile = load_feature_rerank_profile(tmp_path / "missing.json")

    assert profile["profile_name"] == "approved_similar_feature_preview"
    assert profile["production_enabled"] is False
    assert profile["_metadata"]["loaded"] is False
    assert profile["_metadata"]["reason"] == "missing"


def test_synonym_overlap_boosts_insurance_card_candidate():
    cfg = load_japanese_business_synonyms()
    candidates = [
        {
            "qa_id": "qa-insurance",
            "question_text": "健康保険被保険者証の再発行について",
            "scores": {"weighted_score": 0.5},
        }
    ]

    ranked, summary = apply_feature_rerank(
        "保険証をなくしました",
        candidates,
        profile=_profile(),
        synonym_config=cfg,
    )

    assert summary["feature_rerank_applied"] is True
    assert ranked[0]["feature_synonym_overlap_score"] > 0
    assert ranked[0]["feature_score_adjustment"] > 0
    assert ranked[0]["feature_matched_canonicals"] == ["健康保険証"]
    assert "synonym_overlap" in ranked[0]["feature_rerank_reasons"]


def test_business_term_overlap_boosts_childcare_leave_candidate():
    cfg = load_japanese_business_synonyms()
    candidates = [
        {
            "qa_id": "qa-childcare",
            "question_text": "育児休業の申請手続き",
            "score": 0.5,
        }
    ]

    ranked, _summary = apply_feature_rerank(
        "育休の申請をしたい",
        candidates,
        profile=_profile(),
        synonym_config=cfg,
    )

    assert ranked[0]["feature_business_term_overlap_score"] > 0
    assert ranked[0]["feature_score_adjustment"] > 0
    assert "business_term_overlap" in ranked[0]["feature_rerank_reasons"]


def test_negative_mismatch_penalizes_opposite_fuyo_intent():
    cfg = load_japanese_business_synonyms()
    candidates = [
        {
            "qa_id": "qa-opposite",
            "question_text": "扶養から外れる手続き",
            "score": 0.9,
        }
    ]

    ranked, summary = apply_feature_rerank(
        "扶養に入るにはどうしますか",
        candidates,
        profile=_profile(),
        synonym_config=cfg,
    )

    assert summary["negative_mismatch_count"] == 1
    assert ranked[0]["feature_negative_mismatch"] is True
    assert ranked[0]["feature_score_adjustment"] < 0
    assert ranked[0]["feature_negative_mismatch_reason"] == "opposite_intent_terms"
    assert "negative_mismatch_penalty" in ranked[0]["feature_rerank_reasons"]


def test_negative_mismatch_can_reorder_candidate_below_safer_candidate():
    cfg = load_japanese_business_synonyms()
    candidates = [
        {"qa_id": "qa-opposite", "question_text": "扶養から外れる手続き", "score": 0.9},
        {"qa_id": "qa-safe", "question_text": "被扶養者として扶養に入る手続き", "score": 0.8},
    ]

    ranked, summary = apply_feature_rerank(
        "扶養に入るにはどうしますか",
        candidates,
        profile=_profile(),
        synonym_config=cfg,
    )

    assert [candidate["qa_id"] for candidate in ranked] == ["qa-safe", "qa-opposite"]
    assert summary["reordered"] is True


def test_no_numeric_base_score_preserves_original_order_but_adds_metadata():
    cfg = load_japanese_business_synonyms()
    candidates = [
        {"qa_id": "qa-a", "question_text": "退職の手続き"},
        {"qa_id": "qa-b", "question_text": "健康保険被保険者証の再発行"},
    ]

    ranked, summary = apply_feature_rerank(
        "保険証の再発行",
        candidates,
        profile=_profile(),
        synonym_config=cfg,
    )

    assert [candidate["qa_id"] for candidate in ranked] == ["qa-a", "qa-b"]
    assert summary["reordered"] is False
    assert ranked[1]["feature_base_score"] is None
    assert ranked[1]["feature_adjusted_score"] is None
    assert ranked[1]["feature_score_adjustment"] > 0


def test_feature_adjustments_are_clamped():
    cfg = load_japanese_business_synonyms()
    profile = _profile(
        weights={
            "base_score": 1.0,
            "synonym_overlap": 10.0,
            "business_term_overlap": 10.0,
            "negative_mismatch_penalty": 10.0,
        },
        limits={"max_positive_adjustment": 0.12, "max_negative_penalty": 0.2},
    )
    positive, _ = apply_feature_rerank(
        "保険証の再発行",
        [{"qa_id": "qa-positive", "question_text": "健康保険被保険者証の再発行", "score": 0.5}],
        profile=profile,
        synonym_config=cfg,
    )
    negative, _ = apply_feature_rerank(
        "扶養に入る",
        [{"qa_id": "qa-negative", "question_text": "扶養から外れる", "score": 0.5}],
        profile=profile,
        synonym_config=cfg,
    )

    assert positive[0]["feature_score_adjustment"] == 0.12
    assert negative[0]["feature_score_adjustment"] >= -0.2


def test_metadata_is_bounded_and_excludes_private_payloads():
    cfg = {
        "synonym_groups": [
            {"canonical": f"用語{i}", "terms": [f"別名{i}"]}
            for i in range(30)
        ],
        "negative_mismatch_pairs": [],
    }
    query = " ".join(f"用語{i}" for i in range(30))
    candidate = {
        "qa_id": "qa-safe",
        "question_text": " ".join(f"別名{i}" for i in range(30)),
        "approved_answer_preview": "短いプレビュー",
        "private_payload": {"chunk": "秘密のチャンク"},
        "approved_answer": "秘密の承認済み回答",
        "score": 0.5,
    }

    ranked, _summary = apply_feature_rerank(
        query,
        [candidate],
        profile=_profile(),
        synonym_config=cfg,
    )
    rendered = json.dumps(ranked[0], ensure_ascii=False)

    assert len(ranked[0]["feature_matched_canonicals"]) == 20
    assert "秘密のチャンク" not in rendered
    assert "秘密の承認済み回答" not in rendered
    assert "秘密のチャンク" not in json.dumps(
        {
            "feature_matched_canonicals": ranked[0]["feature_matched_canonicals"],
            "feature_rerank_reasons": ranked[0]["feature_rerank_reasons"],
        },
        ensure_ascii=False,
    )


def test_production_enabled_profile_is_rejected_and_order_is_unchanged():
    candidates = [
        {"qa_id": "qa-a", "question_text": "保険証", "score": 0.1},
        {"qa_id": "qa-b", "question_text": "被保険者証", "score": 0.9},
    ]

    ranked, summary = apply_feature_rerank(
        "保険証",
        candidates,
        profile=_profile(production_enabled=True),
        synonym_config=load_japanese_business_synonyms(),
    )

    assert [candidate["qa_id"] for candidate in ranked] == ["qa-a", "qa-b"]
    assert summary["feature_rerank_applied"] is False
    assert summary["valid_profile"] is False
    assert summary["profile_invalid_reason"] == "production_enabled_must_be_false"
    assert "feature_score_adjustment" not in ranked[0]


def test_module_does_not_change_runtime_routes_by_default():
    from webapi.main import ChatRequest, ProductPreviewChatRequest

    assert not hasattr(ChatRequest, "feature_rerank_profile")
    assert not hasattr(ProductPreviewChatRequest, "feature_rerank_profile")
