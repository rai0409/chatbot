import json

from rag_core import retrieval
from rag_core.retrieval import RetrievedChunk


def test_heuristic_tokenize_with_normalized_preprocessing():
    tokens = retrieval._heuristic_tokenize('「ＰＲ２」　ＡＢＣ１２３')
    assert "pr2" in tokens
    assert "abc123" in tokens


def test_query_match_terms_uses_shared_salient_extraction():
    exact_terms, quoted_terms, id_terms = retrieval._query_match_terms(
        '「ＡＢＣ１２３」 PR2 カタカナ語 漢字複合語'
    )
    assert "abc123" in quoted_terms
    assert "pr2" in id_terms
    assert "カタカナ語" in exact_terms
    assert "漢字複合語" in exact_terms


def test_keyword_retrieve_uses_searchable_text_and_returns_display_text(tmp_path, monkeypatch):
    chunks_path = tmp_path / "chunks.jsonl"
    rows = [
        {
            "id": "c1",
            "text": "短い表示テキストです。",
            "display_text": "表示: 短い説明です。",
            "searchable_text": "タイトル 管理者権限コード ZZ-10 の確認手順",
            "source_doc": "doc.pdf",
            "source_pages": [1],
            "doc_id": "doc.pdf",
            "chunk_index": 1,
            "searchable": 1,
            "type": "pdf",
            "quality": "high",
            "chunk_role": "child",
        },
        {
            "id": "c2",
            "text": "管理者の一般説明です。",
            "display_text": "表示: 一般説明です。",
            "searchable_text": "一般説明のみ",
            "source_doc": "doc.pdf",
            "source_pages": [2],
            "doc_id": "doc.pdf",
            "chunk_index": 2,
            "searchable": 1,
            "type": "pdf",
            "quality": "high",
            "chunk_role": "child",
        },
    ]
    chunks_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    monkeypatch.setattr(retrieval.config, "CHUNKS_JSONL_PATH", str(chunks_path))
    retrieval._INDEX_CACHE["path"] = None
    retrieval._INDEX_CACHE["mtime"] = None
    retrieval._INDEX_CACHE["index"] = None

    out = retrieval.keyword_retrieve("ZZ-10 の確認方法", top_k=2)
    assert out
    assert out[0].metadata["id"] == "c1"
    assert out[0].text == "表示: 短い説明です。"


def test_expand_parent_chunks_merges_duplicate_parents(tmp_path, monkeypatch):
    chunks_path = tmp_path / "chunks.jsonl"
    rows = [
        {
            "id": "parent-1",
            "text": "親本文です。手順全体の説明。",
            "display_text": "親表示テキスト: 手順全体の説明。",
            "searchable_text": "手順全体の説明",
            "source_doc": "ops.pdf",
            "source_pages": [5],
            "doc_id": "ops.pdf",
            "chunk_index": 1,
            "searchable": 1,
            "type": "pdf",
            "quality": "high",
            "chunk_role": "parent",
        },
        {
            "id": "child-a",
            "text": "子A",
            "display_text": "子A",
            "searchable_text": "手順 ステップA",
            "source_doc": "ops.pdf",
            "source_pages": [6],
            "doc_id": "ops.pdf",
            "chunk_index": 2,
            "searchable": 1,
            "type": "pdf",
            "quality": "high",
            "chunk_role": "child",
            "parent_chunk_id": "parent-1",
        },
        {
            "id": "child-b",
            "text": "子B",
            "display_text": "子B",
            "searchable_text": "手順 ステップB",
            "source_doc": "ops.pdf",
            "source_pages": [7],
            "doc_id": "ops.pdf",
            "chunk_index": 3,
            "searchable": 1,
            "type": "pdf",
            "quality": "high",
            "chunk_role": "child",
            "parent_chunk_id": "parent-1",
        },
    ]
    chunks_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(retrieval.config, "CHUNKS_JSONL_PATH", str(chunks_path))
    monkeypatch.setattr(retrieval.config, "ENABLE_PARENT_EXPANSION", True)
    retrieval._INDEX_CACHE["path"] = None
    retrieval._INDEX_CACHE["mtime"] = None
    retrieval._INDEX_CACHE["index"] = None

    seeds = [
        RetrievedChunk(
            text="子A",
            metadata={"id": "child-a", "parent_chunk_id": "parent-1", "source_doc": "ops.pdf", "source_pages": [6]},
            score=0.31,
        ),
        RetrievedChunk(
            text="子B",
            metadata={"id": "child-b", "parent_chunk_id": "parent-1", "source_doc": "ops.pdf", "source_pages": [7]},
            score=0.27,
        ),
    ]
    expanded = retrieval.expand_parent_chunks(seeds, max_parent_chunks=3, max_parent_context_chars=2000)
    assert len(expanded) == 1
    assert expanded[0].metadata["chunk_role"] == "parent"
    assert expanded[0].metadata["primary_child_chunk_id"] == "child-b"
    assert expanded[0].metadata["child_chunk_ids"] == ["child-a", "child-b"]
    assert expanded[0].text.startswith("親表示テキスト")
