#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_core import store
from rag_core.canonical_metadata import validate_required_retrieval_metadata
from rag_core.embedding_fingerprint import collection_extended_fingerprint, fingerprint_status


RETRIEVAL_METADATA_FIELDS = (
    "source_doc",
    "source_file",
    "source_pages",
    "source_page_start",
    "source_page_end",
    "source_type",
    "parser",
    "doc_type",
    "chunk_type",
    "searchable_text",
    "display_text",
    "tenant_id",
    "chunk_role",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _parse_metadata_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if (text.startswith("[") and text.endswith("]")) or (text.startswith("{") and text.endswith("}")):
            try:
                return json.loads(text)
            except Exception:
                return value
    return value


def _normalize_vector_record(chunk_id: str, document: Any, metadata: dict[str, Any] | None) -> dict[str, Any]:
    meta = {key: _parse_metadata_value(value) for key, value in dict(metadata or {}).items()}
    return {"id": str(chunk_id), "text": str(document or ""), **meta}


def read_chroma_collection(collection_name: str) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    collection = store.get_vectorstore(
        collection_name=collection_name,
        verify_embedding_fingerprint=False,
        create_if_missing=False,
    )
    total = int(collection.count())
    rows: list[dict[str, Any]] = []
    batch_size = 1000
    for offset in range(0, total, batch_size):
        result = collection.get(
            include=["metadatas", "documents"],
            limit=batch_size,
            offset=offset,
        )
        ids = result.get("ids") or []
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        for idx, chunk_id in enumerate(ids):
            rows.append(
                _normalize_vector_record(
                    str(chunk_id),
                    documents[idx] if idx < len(documents) else "",
                    metadatas[idx] if idx < len(metadatas) else {},
                )
            )
    metadata = dict(getattr(collection, "metadata", None) or {})
    return rows, str(getattr(collection, "name", "") or collection_name), metadata


def chunk_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("chunk_id") or "").strip()


def source_doc(row: dict[str, Any]) -> str:
    return str(row.get("source_doc") or row.get("source_file") or row.get("doc_id") or "").strip()


def source_doc_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(source_doc(row) for row in rows).items()))


def metadata_missing_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        for field in RETRIEVAL_METADATA_FIELDS:
            value = row.get(field)
            if value is None or value == "" or value == []:
                counts[field] += 1
    return dict(sorted(counts.items()))


def source_doc_diffs(bm25_rows: list[dict[str, Any]], vector_rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    bm25 = Counter(source_doc(row) for row in bm25_rows)
    vector = Counter(source_doc(row) for row in vector_rows)
    diffs: dict[str, dict[str, int]] = {}
    for doc in sorted(set(bm25) | set(vector)):
        if bm25[doc] != vector[doc]:
            diffs[doc] = {"bm25": bm25[doc], "vectorstore": vector[doc], "delta": vector[doc] - bm25[doc]}
    return diffs


def build_summary(
    *,
    bm25_path: Path,
    collection_name: str,
    actual_collection: str,
    bm25_rows: list[dict[str, Any]],
    vector_rows: list[dict[str, Any]],
    bm25_only: list[dict[str, Any]],
    vector_only: list[dict[str, Any]],
    collection_metadata: dict[str, Any],
) -> dict[str, Any]:
    bm25_ids = {chunk_id(row) for row in bm25_rows if chunk_id(row)}
    vector_ids = {chunk_id(row) for row in vector_rows if chunk_id(row)}
    intersection = bm25_ids & vector_ids
    denom = max(len(bm25_ids | vector_ids), 1)
    return {
        "bm25_jsonl": str(bm25_path),
        "collection_requested": collection_name,
        "collection": actual_collection,
        "bm25_total_chunks": len(bm25_rows),
        "vectorstore_total_chunks": len(vector_rows),
        "bm25_unique_ids": len(bm25_ids),
        "vectorstore_unique_ids": len(vector_ids),
        "matching_chunk_ids": len(intersection),
        "chunk_id_jaccard": len(intersection) / denom,
        "bm25_only_chunks": len(bm25_only),
        "vectorstore_only_chunks": len(vector_only),
        "bm25_source_doc_distribution": source_doc_distribution(bm25_rows),
        "vectorstore_source_doc_distribution": source_doc_distribution(vector_rows),
        "source_doc_diffs": source_doc_diffs(bm25_rows, vector_rows),
        "bm25_metadata_missing_counts": metadata_missing_counts(bm25_rows),
        "vectorstore_metadata_missing_counts": metadata_missing_counts(vector_rows),
        "bm25_required_retrieval_metadata_missing_counts": dict(
            sorted(Counter(field for row in bm25_rows for field in validate_required_retrieval_metadata(row)).items())
        ),
        "vectorstore_required_retrieval_metadata_missing_counts": dict(
            sorted(Counter(field for row in vector_rows for field in validate_required_retrieval_metadata(row)).items())
        ),
        "collection_fingerprint_status": fingerprint_status(collection_metadata),
        "collection_fingerprint_metadata": collection_extended_fingerprint(collection_metadata),
    }


def write_report(path: Path, summary: dict[str, Any]) -> None:
    def bullets(mapping: dict[str, Any]) -> list[str]:
        if not mapping:
            return ["- none"]
        return [f"- {key or '(empty)'}: {value}" for key, value in mapping.items()]

    lines = [
        "# Corpus Alignment Report",
        "",
        "## Summary",
        f"- bm25_jsonl: `{summary['bm25_jsonl']}`",
        f"- collection: `{summary['collection']}`",
        f"- bm25_total_chunks: {summary['bm25_total_chunks']}",
        f"- vectorstore_total_chunks: {summary['vectorstore_total_chunks']}",
        f"- matching_chunk_ids: {summary['matching_chunk_ids']}",
        f"- chunk_id_jaccard: {summary['chunk_id_jaccard']:.3f}",
        f"- bm25_only_chunks: {summary['bm25_only_chunks']}",
        f"- vectorstore_only_chunks: {summary['vectorstore_only_chunks']}",
        "",
        "## BM25 Source Doc Distribution",
        *bullets(summary["bm25_source_doc_distribution"]),
        "",
        "## Vectorstore Source Doc Distribution",
        *bullets(summary["vectorstore_source_doc_distribution"]),
        "",
        "## Source Doc Diffs",
        *bullets(summary["source_doc_diffs"]),
        "",
        "## BM25 Metadata Missing Counts",
        *bullets(summary["bm25_metadata_missing_counts"]),
        "",
        "## Vectorstore Metadata Missing Counts",
        *bullets(summary["vectorstore_metadata_missing_counts"]),
        "",
        "## Collection Fingerprint",
        f"- present: {summary['collection_fingerprint_status']['present']}",
        f"- matches_active: {summary['collection_fingerprint_status']['matches_active']}",
        f"- active: `{json.dumps(summary['collection_fingerprint_status']['active'], ensure_ascii=False)}`",
        f"- stamped: `{json.dumps(summary['collection_fingerprint_status']['stamped'], ensure_ascii=False)}`",
        f"- extended: `{json.dumps(summary['collection_fingerprint_metadata'], ensure_ascii=False)}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bm25-jsonl", type=Path, required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    bm25_rows = read_jsonl(args.bm25_jsonl)
    vector_rows, actual_collection, collection_metadata = read_chroma_collection(args.collection)

    bm25_by_id = {chunk_id(row): row for row in bm25_rows if chunk_id(row)}
    vector_by_id = {chunk_id(row): row for row in vector_rows if chunk_id(row)}
    bm25_only = [bm25_by_id[key] for key in sorted(set(bm25_by_id) - set(vector_by_id))]
    vector_only = [vector_by_id[key] for key in sorted(set(vector_by_id) - set(bm25_by_id))]
    summary = build_summary(
        bm25_path=args.bm25_jsonl,
        collection_name=args.collection,
        actual_collection=actual_collection,
        bm25_rows=bm25_rows,
        vector_rows=vector_rows,
        bm25_only=bm25_only,
        vector_only=vector_only,
        collection_metadata=collection_metadata,
    )

    (args.output_dir / "corpus_alignment_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "source_doc_distribution.json").write_text(
        json.dumps(
            {
                "bm25": summary["bm25_source_doc_distribution"],
                "vectorstore": summary["vectorstore_source_doc_distribution"],
                "diffs": summary["source_doc_diffs"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_jsonl(args.output_dir / "bm25_only_chunks.jsonl", bm25_only)
    write_jsonl(args.output_dir / "vectorstore_only_chunks.jsonl", vector_only)
    write_report(args.output_dir / "corpus_alignment_report.md", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
