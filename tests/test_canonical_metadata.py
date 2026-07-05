from __future__ import annotations

from rag_core.canonical_metadata import (
    build_searchable_text,
    normalize_record,
    normalize_source_metadata,
    validate_required_retrieval_metadata,
)


def test_normalize_source_metadata_backfills_source_aliases_and_pages():
    meta = normalize_source_metadata(
        {
            "source_doc": "example.pdf",
            "pageno": 12,
            "type": "pdf",
        }
    )

    assert meta["source_doc"] == "example.pdf"
    assert meta["source_file"] == "example.pdf"
    assert meta["source_pages"] == [12]
    assert meta["source_page_start"] == 12
    assert meta["source_page_end"] == 12
    assert meta["source_type"] == "pdf"
    assert meta["parser"] == "pdf"
    assert meta["doc_type"] == "document"
    assert meta["chunk_type"] == "text"
    assert meta["tenant_id"] == "default"


def test_normalize_record_preserves_text_and_adds_derived_text_fields():
    original = "A\n\n  B"
    row = normalize_record({"id": "doc.pdf:p1:c0", "text": original, "source_file": "doc.pdf", "source_pages": [1]})

    assert row["text"] == original
    assert row["display_text"] == original
    assert row["searchable_text"] == "A B"
    assert row["chunk_id"] == "doc.pdf:p1:c0"
    assert validate_required_retrieval_metadata(row) == []


def test_build_searchable_text_prefers_existing_searchable_text():
    assert build_searchable_text({"text": "raw", "searchable_text": " Already\nnormalized "}) == "Already normalized"
