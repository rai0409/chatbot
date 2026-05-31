from __future__ import annotations

from rag_core import approved_similar
from rag_core.approved_similar import (
    build_approved_similar_candidate,
    score_approved_candidate_keyword,
    search_approved_similar_candidates,
)


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
    assert response["citations_count"] == 0
