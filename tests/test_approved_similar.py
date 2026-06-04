from __future__ import annotations

import json

from rag_core import approved_similar
from rag_core.approved_similar import (
    build_approved_similar_candidate,
    decide_approved_similar_candidate,
    score_approved_candidate_keyword,
    search_approved_similar_candidates,
)


def _write_keyword_profile(tmp_path, profile):
    path = tmp_path / "weights.json"
    path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    approved_similar._load_keyword_weight_profile.cache_clear()
    return path


def _write_decision_thresholds(tmp_path, profile):
    path = tmp_path / "decision_thresholds.json"
    path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    approved_similar._load_decision_threshold_config.cache_clear()
    return path


def _meta(**overrides):
    data = {
        "id": "approved_qa_pair:qa_free_answer",
        "qa_id": "qa_free_answer",
        "question_text": "15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。",
        "answer_text": "フリーアンサーも含みます。",
        "approved_answer": "フリーアンサーも含みます。",
        "normalized_question": "15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。",
        "source_doc": "58887_95105_misc.pdf",
        "source_pages": [1],
        "doc_version": "v1",
        "tenant_id": "default",
        "chunk_type": "qa_pair",
        "doc_type": "approved_qa_pair",
        "title": "観光デジタルアンケート分析業務 質問に対する回答",
    }
    data.update(overrides)
    return data


def _personal_info_meta(**overrides):
    data = _meta(
        id="approved_qa_pair:qa_personal_info",
        qa_id="qa_personal_info",
        question_text="15問程度の項目に個人情報は含まれますか。",
        answer_text="個人情報は含まれません。",
        approved_answer="個人情報は含まれません。",
        normalized_question="15問程度の項目に個人情報は含まれますか。",
    )
    data.update(overrides)
    return data


def _decision_candidate(**overrides):
    data = {
        "qa_id": "qa_decision",
        "question_text": "15問程度の項目はフリーアンサーも含まれますか。",
        "approved_answer_preview": "フリーアンサーも含みます。",
        "hybrid_score": 0.9,
        "semantic_score": 0.88,
        "keyword_score": 0.86,
        "weighted_keyword_score": 0.87,
        "top1_top2_margin": 0.12,
        "numeric_conflict": False,
        "negation_conflict": False,
        "ambiguous": False,
        "generic_matched_terms": ["設問"],
        "specific_matched_terms": ["フリーアンサー"],
        "matched_terms": ["設問", "フリーアンサー"],
        "matched_fields": ["question_text"],
    }
    data.update(overrides)
    return data


def test_topic_term_beats_generic_numeric_overlap_for_candidate_ranking():
    query = "15問に個人情報は含まれますか？"
    free_answer = build_approved_similar_candidate(
        query=query,
        text="Q: 15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。\nA: フリーアンサーも含みます。",
        metadata=_meta(),
        distance=0.2,
    )
    personal_info = build_approved_similar_candidate(
        query=query,
        text="Q: 15問程度の項目に個人情報は含まれますか。\nA: 個人情報は含まれません。",
        metadata=_personal_info_meta(),
        distance=0.25,
    )

    assert "個人情報" in personal_info.matched_terms
    assert "question_text" in personal_info.matched_fields
    assert personal_info.negation_conflict is True
    assert free_answer.keyword_score < personal_info.keyword_score
    assert free_answer.hybrid_score < personal_info.hybrid_score


def test_keyword_score_prefers_overlap_and_synonym_terms():
    related = score_approved_candidate_keyword(
        "15問に自由回答は入りますか？",
        _meta(),
        text="Q: 15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。\nA: フリーアンサーも含みます。",
    )
    unrelated = score_approved_candidate_keyword(
        "15問に自由回答は入りますか？",
        _meta(
            question_text="発送は海外も対象ですか。",
            answer_text="国内のみとし、海外発送は対象外とします。",
            normalized_question="発送は海外も対象ですか。",
        ),
        text="Q: 発送は海外も対象ですか。\nA: 国内のみとし、海外発送は対象外とします。",
    )

    assert related["keyword_score"] > unrelated["keyword_score"]
    assert "自由回答" in related["matched_terms"] or "フリーアンサー" in related["matched_terms"]
    assert "question_text" in related["matched_fields"]
    assert {
        "query_term": "自由回答",
        "matched_synonym": "フリーアンサー",
        "field": "question_text",
    } in related["synonym_matches"]
    assert not unrelated["synonym_matches"]


def test_keyword_score_handles_minimal_japanese_synonym_groups():
    related = score_approved_candidate_keyword(
        "15問に自由記述は入りますか？",
        _meta(),
        text="Q: 15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。\nA: フリーアンサーも含みます。",
    )
    unrelated = score_approved_candidate_keyword(
        "配送先住所を変更できますか？",
        _meta(),
        text="Q: 15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。\nA: フリーアンサーも含みます。",
    )

    assert related["keyword_score"] > unrelated["keyword_score"]
    assert any(
        match["query_term"] == "自由記述" and match["matched_synonym"] == "フリーアンサー"
        for match in related["synonym_matches"]
    )


def test_keyword_score_without_profile_preserves_existing_score_shape(monkeypatch):
    monkeypatch.delenv("APPROVED_SIMILAR_KEYWORD_WEIGHTS", raising=False)
    approved_similar._load_keyword_weight_profile.cache_clear()

    result = score_approved_candidate_keyword(
        "15問に自由回答は入りますか？",
        _meta(),
        text="Q: 15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。\nA: フリーアンサーも含みます。",
    )

    assert result["keyword_score"] == result["weighted_keyword_score"]
    assert result["keyword_score"] > 0
    assert result["keyword_weight_details"]
    assert result["field_weight_details"]


def test_keyword_profile_can_downweight_generic_terms(monkeypatch, tmp_path):
    monkeypatch.delenv("APPROVED_SIMILAR_KEYWORD_WEIGHTS", raising=False)
    approved_similar._load_keyword_weight_profile.cache_clear()
    baseline = score_approved_candidate_keyword(
        "アンケートは必要ですか？",
        _meta(question_text="アンケートは必要ですか。", answer_text="必要です。"),
    )
    profile_path = _write_keyword_profile(
        tmp_path,
        {
            "generic_terms": ["アンケート", "必要"],
            "generic_multiplier": 0.2,
            "field_weights": {"question_text": 1.0},
        },
    )
    monkeypatch.setenv("APPROVED_SIMILAR_KEYWORD_WEIGHTS", str(profile_path))

    weighted = score_approved_candidate_keyword(
        "アンケートは必要ですか？",
        _meta(question_text="アンケートは必要ですか。", answer_text="必要です。"),
    )

    assert weighted["keyword_score"] < baseline["keyword_score"]
    assert "アンケート" in weighted["generic_matched_terms"]
    assert any(detail["class_multiplier"] == 0.2 for detail in weighted["keyword_weight_details"])


def test_keyword_profile_can_upweight_specific_terms(monkeypatch, tmp_path):
    monkeypatch.delenv("APPROVED_SIMILAR_KEYWORD_WEIGHTS", raising=False)
    approved_similar._load_keyword_weight_profile.cache_clear()
    baseline = score_approved_candidate_keyword(
        "個人情報は含まれますか？",
        _personal_info_meta(),
    )
    profile_path = _write_keyword_profile(
        tmp_path,
        {
            "specific_terms": ["個人情報"],
            "specific_multiplier": 1.8,
            "field_weights": {"question_text": 1.0},
        },
    )
    monkeypatch.setenv("APPROVED_SIMILAR_KEYWORD_WEIGHTS", str(profile_path))

    weighted = score_approved_candidate_keyword(
        "個人情報は含まれますか？",
        _personal_info_meta(),
    )

    assert weighted["keyword_score"] > baseline["keyword_score"]
    assert "個人情報" in weighted["specific_matched_terms"]
    assert any(detail["class_multiplier"] == 1.8 for detail in weighted["keyword_weight_details"])


def test_answer_side_configured_terms_can_contribute(monkeypatch, tmp_path):
    profile_path = _write_keyword_profile(
        tmp_path,
        {
            "specific_terms": ["ウエイトバック"],
            "specific_multiplier": 1.2,
            "field_weights": {"approved_answer": 1.5},
        },
    )
    monkeypatch.setenv("APPROVED_SIMILAR_KEYWORD_WEIGHTS", str(profile_path))

    result = score_approved_candidate_keyword(
        "ウエイトバックは必須ですか？",
        _meta(
            question_text="集計条件について確認します。",
            answer_text="詳細は別途協議します。",
            approved_answer="ウエイトバック集計は必須ではありません。",
        ),
    )

    assert "approved_answer" in result["matched_fields"]
    assert "ウエイトバック" in result["specific_matched_terms"]
    assert any(
        detail["field"] == "approved_answer" and detail["field_multiplier"] == 1.5
        for detail in result["keyword_weight_details"]
    )


def test_candidate_debug_output_includes_weighting_evidence(monkeypatch):
    monkeypatch.delenv("APPROVED_SIMILAR_KEYWORD_WEIGHTS", raising=False)
    approved_similar._load_keyword_weight_profile.cache_clear()

    candidate = build_approved_similar_candidate(
        query="15問に自由回答は入りますか？",
        text="Q: 15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。\nA: フリーアンサーも含みます。",
        metadata=_meta(),
        distance=0.25,
    ).to_dict()

    assert candidate["weighted_keyword_score"] == candidate["keyword_score"]
    assert candidate["keyword_weight_details"]
    assert candidate["field_weight_details"]
    assert "generic_matched_terms" in candidate
    assert "specific_matched_terms" in candidate


def test_decision_gate_no_candidates():
    decision = decide_approved_similar_candidate([])

    assert decision["route"] == "no_candidate"
    assert decision["qa_id"] is None
    assert decision["top_candidate_summary"] is None


def test_decision_gate_numeric_conflict_blocks():
    decision = decide_approved_similar_candidate(
        [_decision_candidate(numeric_conflict=True, hybrid_score=0.99)]
    )

    assert decision["route"] == "numeric_conflict_blocked"
    assert decision["blocking_flags"]["numeric_conflict"] is True
    assert "numeric_conflict" in decision["reasons"][0]


def test_decision_gate_negation_conflict_requires_review():
    decision = decide_approved_similar_candidate(
        [_decision_candidate(negation_conflict=True, hybrid_score=0.99)]
    )

    assert decision["route"] == "negation_conflict_review"
    assert decision["blocking_flags"]["negation_conflict"] is True


def test_decision_gate_ambiguous_multi_topic():
    decision = decide_approved_similar_candidate(
        [_decision_candidate(ambiguous=True, hybrid_score=0.99)]
    )

    assert decision["route"] == "ambiguous_multi_topic"
    assert decision["blocking_flags"]["ambiguous"] is True


def test_decision_gate_high_score_and_margin_allows_debug_high_confidence():
    decision = decide_approved_similar_candidate([_decision_candidate()])

    assert decision["route"] == "high_confidence_answer"
    assert decision["qa_id"] == "qa_decision"
    assert decision["confidence_like_score"] == 0.9
    assert decision["score_snapshot"]["top1_top2_margin"] == 0.12
    assert decision["top_candidate_summary"]["specific_matched_terms"] == ["フリーアンサー"]
    assert decision["threshold_source"] == "default"


def test_decision_gate_low_margin_stays_candidate_only():
    decision = decide_approved_similar_candidate(
        [_decision_candidate(hybrid_score=0.95, top1_top2_margin=0.02)]
    )

    assert decision["route"] == "candidate_only"
    assert "top1_top2_margin" in " ".join(decision["reasons"])


def test_decision_gate_low_score_no_answer():
    decision = decide_approved_similar_candidate(
        [_decision_candidate(hybrid_score=0.2, top1_top2_margin=0.5)]
    )

    assert decision["route"] == "low_confidence_no_answer"
    assert "low_confidence_score" in decision["reasons"][0]


def test_decision_gate_accepts_threshold_overrides():
    decision = decide_approved_similar_candidate(
        [_decision_candidate(hybrid_score=0.7, top1_top2_margin=0.03)],
        thresholds={"high_confidence_score": 0.65, "high_confidence_margin": 0.02},
    )

    assert decision["route"] == "high_confidence_answer"
    assert decision["thresholds"]["high_confidence_score"] == 0.65
    assert decision["threshold_source"] == "explicit_dict"


def test_decision_gate_loads_config_file_thresholds(monkeypatch, tmp_path):
    threshold_path = _write_decision_thresholds(
        tmp_path,
        {
            "profile_name": "strict_for_test",
            "high_confidence_min_hybrid": 0.95,
            "high_confidence_min_margin": 0.08,
            "low_confidence_max_hybrid": 0.45,
            "numeric_conflict_route": "numeric_conflict_blocked",
            "negation_conflict_route": "negation_conflict_review",
            "ambiguous_route": "ambiguous_multi_topic",
            "require_specific_terms_for_high_confidence": True,
            "min_specific_terms_for_high_confidence": 2,
            "allow_high_confidence_when_margin_missing": False,
        },
    )
    monkeypatch.setenv("APPROVED_SIMILAR_DECISION_THRESHOLDS", str(threshold_path))

    decision = decide_approved_similar_candidate([_decision_candidate()])

    assert decision["route"] == "candidate_only"
    assert decision["threshold_source"] == "config_file"
    assert decision["threshold_profile_name"] == "strict_for_test"
    assert decision["threshold_profile_path"] == str(threshold_path)
    assert any("high_confidence_min_hybrid" in reason for reason in decision["reasons"])
    assert any("specific_matched_terms" in reason for reason in decision["reasons"])


def test_decision_gate_explicit_thresholds_override_config(monkeypatch, tmp_path):
    threshold_path = _write_decision_thresholds(
        tmp_path,
        {
            "profile_name": "strict_for_test",
            "high_confidence_min_hybrid": 0.99,
            "high_confidence_min_margin": 0.5,
            "low_confidence_max_hybrid": 0.45,
        },
    )
    monkeypatch.setenv("APPROVED_SIMILAR_DECISION_THRESHOLDS", str(threshold_path))

    decision = decide_approved_similar_candidate(
        [_decision_candidate(hybrid_score=0.7, top1_top2_margin=0.03)],
        thresholds={"high_confidence_score": 0.65, "high_confidence_margin": 0.02},
    )

    assert decision["route"] == "high_confidence_answer"
    assert decision["threshold_source"] == "explicit_dict"
    assert decision["threshold_profile_path"] is None


def test_decision_gate_invalid_config_route_fails_clearly(monkeypatch, tmp_path):
    threshold_path = _write_decision_thresholds(
        tmp_path,
        {
            "profile_name": "bad_route",
            "numeric_conflict_route": "auto_answer_everything",
        },
    )
    monkeypatch.setenv("APPROVED_SIMILAR_DECISION_THRESHOLDS", str(threshold_path))

    try:
        decide_approved_similar_candidate([_decision_candidate(numeric_conflict=True)])
    except ValueError as exc:
        assert "invalid approved similar decision route" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_conflict_flags_are_exposed():
    numeric = score_approved_candidate_keyword(
        "20問に自由回答は入りますか？",
        _meta(),
        text="Q: 15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。",
    )
    negation = score_approved_candidate_keyword(
        "自由回答は含まれないですか？",
        _meta(),
        text="Q: 15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。",
    )

    assert numeric["numeric_conflict"] is True
    assert negation["negation_conflict"] is True
    assert numeric["synonym_matches"]
    assert negation["synonym_matches"]


def test_candidate_formatter_preserves_metadata():
    candidate = build_approved_similar_candidate(
        query="15問に自由回答は入りますか？",
        text="Q: 15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。\nA: フリーアンサーも含みます。",
        metadata=_meta(),
        distance=0.25,
    ).to_dict()

    assert candidate["qa_id"] == "qa_free_answer"
    assert candidate["source_doc"] == "58887_95105_misc.pdf"
    assert candidate["source_pages"] == [1]
    assert candidate["doc_type"] == "approved_qa_pair"
    assert candidate["chunk_type"] == "qa_pair"
    assert candidate["semantic_score"] is not None
    assert candidate["hybrid_score"] is not None
    assert candidate["approved_answer_preview"] == "フリーアンサーも含みます。"
    assert candidate["synonym_matches"]


def test_search_candidates_calculates_margin_and_filters_qa_pairs(monkeypatch):
    class FakeCollection:
        def query(self, **kwargs):
            assert kwargs.get("where")
            return {
                "documents": [
                    [
                        "Q: 15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。\nA: フリーアンサーも含みます。",
                        "Q: 発送は海外も対象ですか。\nA: 国内のみとします。",
                        "Q: 通常チャンク\nA: 対象外",
                    ]
                ],
                "metadatas": [
                    [
                        _meta(),
                        _meta(
                            id="approved_qa_pair:qa_shipping",
                            qa_id="qa_shipping",
                            question_text="発送は海外も対象ですか。",
                            answer_text="国内のみとします。",
                        ),
                        {"doc_type": "faq_glossary", "chunk_type": "child", "qa_id": "not_qa_pair"},
                    ]
                ],
                "distances": [[0.1, 0.4, 0.01]],
            }

    monkeypatch.setattr(approved_similar.store, "get_vectorstore", lambda **kwargs: FakeCollection())
    monkeypatch.setattr(approved_similar.embedder, "embed_queries", lambda queries, client=None: [[0.0, 1.0]])

    candidates = search_approved_similar_candidates(
        "15問に自由回答は入りますか？",
        client=None,
        top_k=2,
    )

    assert [candidate["qa_id"] for candidate in candidates] == ["qa_free_answer", "qa_shipping"]
    assert candidates[0]["top1_top2_margin"] is not None
    assert candidates[0]["margin_score_basis"] == "hybrid_score"
    assert "weighted_keyword_score" in candidates[0]
    assert "specific_matched_terms" in candidates[0]
    assert all(candidate["chunk_type"] == "qa_pair" for candidate in candidates)


def test_search_debug_exposes_candidates_without_chat_routing(monkeypatch):
    from webapi import main

    monkeypatch.setattr(main, "_embedding_client", lambda: None)
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda query: None)
    monkeypatch.setattr(main, "append_audit_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        main,
        "debug_retrieve_with_trace",
        lambda *args, **kwargs: {
            "request_id": "trace-1",
            "original_query": "15問に自由回答は入りますか？",
            "normalized_query": "15問に自由回答は入りますか?",
            "query_type": "faq",
            "before_rerank": [],
            "after_rerank": [],
            "after_parent_expansion": [],
            "answer_mode": "debug_retrieval_only",
            "citations_count": 0,
        },
    )
    monkeypatch.setattr(
        main,
        "search_approved_similar_candidates",
        lambda *args, **kwargs: [
            {
                "qa_id": "qa_free_answer",
                "question_text": "15問程度の項目はフリーアンサーも含まれるという認識で良いでしょうか。",
                "approved_answer_preview": "フリーアンサーも含みます。",
                "semantic_score": 0.9,
                "keyword_score": 0.8,
                "hybrid_score": 0.86,
                "top1_top2_margin": None,
                "matched_terms": ["自由回答"],
                "matched_fields": ["question_text"],
                "synonym_matches": [
                    {
                        "query_term": "自由回答",
                        "matched_synonym": "フリーアンサー",
                        "field": "question_text",
                    }
                ],
                "source_doc": "58887_95105_misc.pdf",
                "source_pages": [1],
                "doc_version": "v1",
                "tenant_id": "default",
                "chunk_type": "qa_pair",
                "doc_type": "approved_qa_pair",
            }
        ],
    )

    response = main.search_debug(
        main.SearchDebugRequest(
            query="15問に自由回答は入りますか？",
            generate_answer=False,
            include_approved_similar_candidates=True,
        )
    )

    assert response["answer_mode"] == "debug_retrieval_only"
    assert response["approved_similar_candidates"][0]["qa_id"] == "qa_free_answer"
    assert response["approved_similar_candidates"][0]["synonym_matches"]
    assert response["citations_count"] == 0
