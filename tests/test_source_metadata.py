from __future__ import annotations

from rag_core.source_metadata import (
    build_source_metadata_from_manifest_record,
    merge_source_metadata,
    normalize_citation,
    normalize_source_metadata,
    normalize_source_pages,
)


def test_normalize_source_pages_handles_int_string_list_and_invalid_values():
    assert normalize_source_pages(1) == [1]
    assert normalize_source_pages("2") == [2]
    assert normalize_source_pages("3,4") == [3, 4]
    assert normalize_source_pages("[5, 6]") == [5, 6]
    assert normalize_source_pages([7, "8", "", "bad", None, True]) == [7, 8]


def test_normalize_source_metadata_bounds_strings_and_drops_complex_fields():
    metadata = normalize_source_metadata(
        {
            "source_doc": "doc.pdf",
            "source_title": "あ" * 700,
            "source_pages": ["1", "bad"],
            "tenant_id": "tenant-a",
            "private_text": "本文を出してはいけない",
            "metadata": {"body": "本文を出してはいけない"},
            "source_id": ["complex"],
        }
    )

    assert metadata["source_doc"] == "doc.pdf"
    assert metadata["source_pages"] == [1]
    assert metadata["tenant_id"] == "tenant-a"
    assert len(metadata["source_title"]) <= 500
    assert "private_text" not in metadata
    assert "metadata" not in metadata
    assert "source_id" not in metadata


def test_normalize_citation_preserves_existing_and_optional_safe_fields():
    citation = normalize_citation(
        {
            "source_doc": "faq.pdf",
            "source_pages": "[1,2]",
            "chunk_id": "c1",
            "title": "FAQ",
            "source_id": "src-1",
            "source_title": "FAQ Source",
            "source_type": "approved_qa",
            "version": "v1",
            "status": "active",
            "updated_at": "2026-06-07T00:00:00Z",
            "tenant_id": "default",
            "body": "private file content",
        }
    )

    assert citation == {
        "source_id": "src-1",
        "source_title": "FAQ Source",
        "source_type": "approved_qa",
        "source_doc": "faq.pdf",
        "source_pages": [1, 2],
        "chunk_id": "c1",
        "title": "FAQ",
        "version": "v1",
        "status": "active",
        "updated_at": "2026-06-07T00:00:00Z",
        "tenant_id": "default",
    }


def test_manifest_record_converts_without_file_contents_or_metadata_blob():
    record = {
        "source_id": "manifest-src",
        "source_title": "Manifest Source",
        "source_type": "pdf",
        "source_path": "pdfs/source.pdf",
        "checksum": "abc",
        "checksum_algorithm": "sha256",
        "status": "active",
        "updated_at": "2026-06-07T00:00:00Z",
        "tenant_id": "default",
        "category": "pdf",
        "metadata": {"text": "private content"},
    }

    metadata = build_source_metadata_from_manifest_record(record)

    assert metadata["source_id"] == "manifest-src"
    assert metadata["source_doc"] == "source.pdf"
    assert metadata["source_path"] == "pdfs/source.pdf"
    assert "metadata" not in metadata
    assert "private content" not in repr(metadata)


def test_merge_source_metadata_prefers_explicit_fields_over_manifest_defaults():
    merged = merge_source_metadata(
        {"source_id": "manifest", "source_doc": "manifest.pdf", "source_pages": [1]},
        {"source_doc": "explicit.pdf", "source_pages": "[2]", "chunk_id": "chunk-2"},
    )

    assert merged["source_id"] == "manifest"
    assert merged["source_doc"] == "explicit.pdf"
    assert merged["source_pages"] == [2]
    assert merged["chunk_id"] == "chunk-2"
