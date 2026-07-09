from __future__ import annotations

from rag_core import qa
from rag_core.retrieval import RetrievedChunk


def _chunk(text: str, chunk_id: str = "c1", searchable: str | None = None) -> RetrievedChunk:
    meta = {"id": chunk_id, "source_doc": "doc.pdf", "retrieval_source": "hybrid"}
    if searchable is not None:
        meta["searchable_text"] = searchable
    return RetrievedChunk(text=text, metadata=meta, score=0.3)


def _grounded(retrieved):
    return qa._to_grounded(retrieved)


def _guard(question, retrieved):
    return qa.guard_merged_top(question, "other", _grounded(retrieved), retrieved)


_EVIDENCE = (
    "観光デジタルアンケート分析業務 質問に対する回答 "
    "アンケートの設問を設計後のアンケートシステムへの入力作業は受託者が行う認識で"
    "お間違いないでしょうか。入力作業はこちらで実施いたします。"
    "概要版として作成をお願いします。"
)


def test_full_question_with_all_terms_evidenced_bypasses_too_general():
    # Long business question; every content term appears in the top-1 chunk.
    retrieved = [_chunk(_EVIDENCE)]
    reason = _guard("アンケートの設問の入力作業は受託者が行う認識で間違いないでしょうか", retrieved)
    assert reason is None


def test_full_question_with_uncovered_terms_still_fires():
    # 著作権/帰属 are absent from the evidence: stays guarded.
    retrieved = [_chunk(_EVIDENCE)]
    reason = _guard("成果物の著作権は誰に帰属しますか", retrieved)
    assert reason == "too_general"


def test_single_specific_term_lookup_bypasses():
    retrieved = [_chunk(_EVIDENCE)]
    reason = _guard("概要版 について", retrieved)
    assert reason is None


def test_single_generic_two_char_term_still_fires():
    retrieved = [_chunk("運用に関する一般的な説明です。")]
    reason = _guard("運用は？", retrieved)
    assert reason == "too_general"


def test_short_multi_term_overview_question_still_fires():
    # Short (content < 12 chars) with two terms: the broad_semantic smoke
    # contract — overview-style short asks stay guarded even when evidenced.
    retrieved = [_chunk("電子入札制度の概要を説明する章です。")]
    reason = _guard("電子入札制度の概要は？", retrieved)
    assert reason == "too_general"


def test_no_content_term_query_still_fires():
    retrieved = [_chunk(_EVIDENCE)]
    reason = _guard("これは？", retrieved)
    assert reason == "too_general"


def test_no_retrieved_chunks_never_bypasses():
    assert qa._should_bypass_too_general("概要版 について", []) is False


def test_searchable_text_metadata_is_used_for_coverage():
    # Q+A pair chunks carry the searchable side in metadata.
    retrieved = [_chunk("表示用テキスト", searchable="Q: 解約の期限は？\nA: 30日前までに解約申請が必要です。概要版もあります。")]
    assert qa._should_bypass_too_general("概要版 について", retrieved) is True
