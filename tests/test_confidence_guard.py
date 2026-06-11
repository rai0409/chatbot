from __future__ import annotations

import json

import pytest

import config
from rag_core import qa
from rag_core.retrieval import RetrievedChunk
from rag_grounded import Chunk
from webapi import main


def _retrieved(text="適格請求書制度の概要説明", **meta):
    base = {"id": "c1", "source_doc": "doc.pdf"}
    base.update(meta)
    score = base.pop("score", 0.25)
    return RetrievedChunk(text=text, metadata=base, score=score)


def _chunk(text="適格請求書制度の概要説明"):
    return Chunk(id="1", text=text, source_doc="doc.pdf", source_pages=(1,))


def test_hard_distance_fires_on_raw_vector_distance_despite_low_rank_score():
    retrieved = [_retrieved(vector_distance=0.97, retrieval_source="hybrid", score=0.25)]

    reason = qa.guard_merged_top("新システムの概要", "other", [_chunk()], retrieved)

    assert reason == "hard_distance"


def test_soft_distance_fires_on_raw_vector_distance():
    retrieved = [_retrieved(vector_distance=0.70, retrieval_source="vector", score=0.25)]

    reason = qa.guard_merged_top("新システムの概要", "other", [_chunk()], retrieved)

    assert reason == "soft_distance"


def test_no_distance_guard_without_vector_evidence():
    retrieved = [
        _retrieved(
            bm25_score=5.0,
            retrieval_source="keyword",
            score=0.25,
            score_details={"keyword_score": 2.0, "matched_terms": ["適格請求書"]},
        )
    ]

    reason = qa.guard_merged_top(
        "適格請求書制度の確認手順を教えてください", "procedure", [_chunk()], retrieved
    )

    assert reason is None


def test_keyword_only_weak_evidence_triggers_guard():
    retrieved = [
        _retrieved(
            text="まったく無関係な本文",
            bm25_score=0.0,
            retrieval_source="keyword",
            score=0.25,
            score_details={"keyword_score": 0.0, "matched_terms": []},
        )
    ]

    reason = qa.guard_merged_top(
        "未知トピックの設定手順", "procedure", [_chunk("まったく無関係な本文")], retrieved
    )

    assert reason == "weak_keyword_evidence"


def test_no_results_reason_unchanged():
    assert qa.guard_merged_top("何か", "other", [], []) == "no_results"


def test_salient_mismatch_reason_unchanged():
    retrieved = [
        _retrieved(
            bm25_score=3.0,
            retrieval_source="keyword",
            score=0.25,
            score_details={"keyword_score": 1.0, "matched_terms": ["制度"]},
        )
    ]

    reason = qa.guard_merged_top(
        "「ZX99」の仕様を確認したい", "procedure", [_chunk()], retrieved
    )

    assert reason == "salient_mismatch"


def test_too_general_reason_unchanged():
    retrieved = [
        _retrieved(
            bm25_score=2.0,
            retrieval_source="keyword",
            score=0.25,
            score_details={"keyword_score": 1.0, "matched_terms": ["概要"]},
        )
    ]

    reason = qa.guard_merged_top("料金とは", "other", [_chunk()], retrieved)

    assert reason == "too_general"


def test_approved_exact_match_bypasses_guard(monkeypatch, tmp_path):
    record = {
        "qa_id": "qa-guard-1",
        "question": "営業時間を教えてください",
        "approved_answer": "営業時間は9時から17時です。",
        "status": "approved",
        "tenant_id": "default",
        "approved_citations": [{"source_doc": "faq.pdf", "source_pages": [1]}],
    }
    path = tmp_path / "approved.jsonl"
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", True)
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(path))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(main, "_approved_qa_index", None)
    monkeypatch.setattr(main, "_approved_qa_index_path", None)

    def forbidden(*args, **kwargs):
        raise AssertionError("RAG pipeline (and guard) must not run for approved exact match")

    monkeypatch.setattr(main, "answer_query_with_trace", forbidden)
    monkeypatch.setattr(main, "ensure_openai_client", forbidden)

    payload = main.chat(main.ChatRequest(question="営業時間を教えてください"))

    assert payload["answer_mode"] == "approved_exact_match"
    assert payload["answer_text"] == "営業時間は9時から17時です。"
    assert payload["approved_qa_id"] == "qa-guard-1"
    assert payload["guard_reason"] is None
    assert payload["retrieved"] == []
