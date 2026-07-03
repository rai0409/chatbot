#!/usr/bin/env python3
"""Evaluate normal-document vector-only retrieval against hybrid retrieval."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_core.retrieval import RetrievedChunk, hybrid_retrieve, keyword_index_status, vector_retrieve


DEFAULT_CASES = Path("artifacts/normal_retrieval_eval/normal_retrieval_cases.jsonl")
DEFAULT_OUTPUT_DIR = Path("artifacts/normal_retrieval_eval")
DEFAULT_COLLECTION = os.getenv("NORMAL_RETRIEVAL_COLLECTION", "chatbot_chunks_v1")


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
        rows.append(obj)
    return rows


def normalize_pages(value: Any) -> set[int]:
    if value is None or value == "":
        return set()
    if isinstance(value, int):
        return {value}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return normalize_pages(parsed)
        except Exception:
            return {int(x) for x in value.replace("[", "").replace("]", "").split(",") if x.strip().isdigit()}
    if isinstance(value, Iterable):
        out = set()
        for item in value:
            try:
                out.add(int(item))
            except Exception:
                pass
        return out
    return set()


def get_meta(chunk: RetrievedChunk) -> dict[str, Any]:
    return dict(chunk.metadata or {})


def chunk_pages(chunk: RetrievedChunk) -> set[int]:
    return normalize_pages(get_meta(chunk).get("source_pages"))


def chunk_doc(chunk: RetrievedChunk) -> str:
    return str(get_meta(chunk).get("source_doc") or get_meta(chunk).get("doc_id") or "")


def keyword_coverage(text: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    haystack = text.lower()
    hits = sum(1 for kw in keywords if str(kw).lower() in haystack)
    return hits / len(keywords)


def find_rank(chunks: list[RetrievedChunk], expected_doc: str) -> int:
    for idx, chunk in enumerate(chunks, start=1):
        if chunk_doc(chunk) == expected_doc:
            return idx
    return 0


def has_page_match(chunk: RetrievedChunk, expected_pages: set[int]) -> bool:
    if not expected_pages:
        return False
    return bool(chunk_pages(chunk) & expected_pages)


def find_page_rank(chunks: list[RetrievedChunk], expected_doc: str, expected_pages: set[int]) -> int:
    if not expected_pages:
        return 0
    for idx, chunk in enumerate(chunks, start=1):
        if chunk_doc(chunk) == expected_doc and has_page_match(chunk, expected_pages):
            return idx
    return 0


def score_value(meta: dict[str, Any], key: str) -> Any:
    details = meta.get("score_details") or {}
    if key in meta:
        return meta.get(key)
    return details.get(key, "")


def evaluate_case(case: dict[str, Any], mode: str, chunks: list[RetrievedChunk], top_k: int) -> dict[str, Any]:
    expected_doc = str(case.get("expected_source_doc") or "")
    expected_pages = normalize_pages(case.get("expected_source_pages"))
    expected_keywords = [str(x) for x in case.get("expected_keywords") or [] if str(x).strip()]
    rank = find_rank(chunks, expected_doc)
    page_rank = find_page_rank(chunks, expected_doc, expected_pages)
    top = chunks[0] if chunks else None
    top_meta = get_meta(top) if top else {}
    top_text = top.text if top else ""
    top_hits = []
    for idx, chunk in enumerate(chunks[:top_k], start=1):
        meta = get_meta(chunk)
        top_hits.append(
            {
                "rank": idx,
                "source_doc": chunk_doc(chunk),
                "source_pages": sorted(chunk_pages(chunk)),
                "retrieval_source": str(meta.get("retrieval_source") or ""),
                "bm25_score": score_value(meta, "bm25_score"),
                "vector_distance": score_value(meta, "vector_distance"),
                "rrf_score": score_value(meta, "rrf_score"),
                "keyword_score": score_value(meta, "keyword_score"),
                "hybrid_rank_score": score_value(meta, "hybrid_rank_score"),
                "rerank_score": score_value(meta, "rerank_score"),
                "page_evidence_boost": score_value(meta, "page_evidence_boost"),
                "page_anchor_boost": score_value(meta, "page_anchor_boost"),
                "page_cluster_boost": score_value(meta, "page_cluster_boost"),
                "score_before_page_adjust": score_value(meta, "score_before_page_adjust"),
                "score_after_page_adjust": score_value(meta, "score_after_page_adjust"),
                "keyword_coverage": keyword_coverage(chunk.text, expected_keywords),
                "chunk_id": str(meta.get("id") or ""),
            }
        )
    best_keyword_coverage = 0.0
    for chunk in chunks[:top_k]:
        best_keyword_coverage = max(best_keyword_coverage, keyword_coverage(chunk.text, expected_keywords))
    top_keyword_coverage = keyword_coverage(top_text, expected_keywords) if top else 0.0

    return {
        "case_id": case.get("case_id", ""),
        "question": case.get("question", ""),
        "mode": mode,
        "rank": rank,
        "hit_at_1": rank == 1,
        "hit_at_3": 0 < rank <= 3,
        "hit_at_5": 0 < rank <= 5,
        "mrr": (1.0 / rank) if rank else 0.0,
        "expected_source_doc": expected_doc,
        "top_source_doc": chunk_doc(top) if top else "",
        "matched_source_doc_rank": rank,
        "expected_pages": sorted(expected_pages),
        "matched_pages": sorted(chunk_pages(chunks[page_rank - 1])) if page_rank else [],
        "page_match_at_5": 0 < page_rank <= 5,
        "expected_keywords": expected_keywords,
        "keyword_coverage": top_keyword_coverage,
        "best_keyword_coverage_at_5": best_keyword_coverage,
        "top_text": top_text[:1200],
        "top_metadata": top_meta,
        "top_hits": top_hits,
        "retrieval_source": str(top_meta.get("retrieval_source") or ""),
        "bm25_score": score_value(top_meta, "bm25_score"),
        "rrf_score": score_value(top_meta, "rrf_score"),
        "keyword_score": score_value(top_meta, "keyword_score"),
        "rerank_score": score_value(top_meta, "rerank_score"),
        "bm25_score_present_at_5": any(present(hit.get("bm25_score")) for hit in top_hits),
        "rrf_score_present_at_5": any(present(hit.get("rrf_score")) for hit in top_hits),
        "keyword_score_present_at_5": any(present(hit.get("keyword_score")) for hit in top_hits),
        "error": "",
    }


def evaluate_mode(
    cases: list[dict[str, Any]],
    *,
    mode: str,
    top_k: int,
    collection_name: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        try:
            if mode == "vector_only":
                chunks = vector_retrieve(
                    str(case.get("question") or ""),
                    client=None,
                    top_k=top_k,
                    allowed_types=["pdf"],
                    collection_name=collection_name,
                    create_collection_if_missing=False,
                )
            elif mode == "hybrid":
                chunks = hybrid_retrieve(
                    str(case.get("question") or ""),
                    client=None,
                    top_k=top_k,
                    allowed_types=["pdf"],
                    collection_name=collection_name,
                    create_collection_if_missing=False,
                )
            else:
                raise ValueError(f"unknown mode: {mode}")
            rows.append(evaluate_case(case, mode, chunks, top_k))
        except Exception as exc:
            rows.append(
                {
                    "case_id": case.get("case_id", ""),
                    "question": case.get("question", ""),
                    "mode": mode,
                    "rank": 0,
                    "hit_at_1": False,
                    "hit_at_3": False,
                    "hit_at_5": False,
                    "mrr": 0.0,
                    "expected_source_doc": case.get("expected_source_doc", ""),
                    "top_source_doc": "",
                    "matched_source_doc_rank": 0,
                    "expected_pages": normalize_pages(case.get("expected_source_pages")),
                    "matched_pages": [],
                    "page_match_at_5": False,
                    "expected_keywords": case.get("expected_keywords") or [],
                    "keyword_coverage": 0.0,
                    "best_keyword_coverage_at_5": 0.0,
                    "top_text": "",
                    "top_metadata": {},
                    "top_hits": [],
                    "retrieval_source": "",
                    "bm25_score": "",
                    "rrf_score": "",
                    "keyword_score": "",
                    "rerank_score": "",
                    "bm25_score_present_at_5": False,
                    "rrf_score_present_at_5": False,
                    "keyword_score_present_at_5": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")


def _json_default(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def summarize(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    total = len(rows)
    errors = sum(1 for row in rows if row.get("error"))
    denom = max(total, 1)
    return {
        f"{prefix}_errors": errors,
        f"{prefix}_hit@1": sum(1 for row in rows if row.get("hit_at_1")) / denom,
        f"{prefix}_hit@3": sum(1 for row in rows if row.get("hit_at_3")) / denom,
        f"{prefix}_hit@5": sum(1 for row in rows if row.get("hit_at_5")) / denom,
        f"{prefix}_mrr": sum(float(row.get("mrr") or 0.0) for row in rows) / denom,
        f"{prefix}_source_doc_match@1": sum(1 for row in rows if row.get("matched_source_doc_rank") == 1) / denom,
        f"{prefix}_source_doc_match@5": sum(
            1 for row in rows if 0 < int(row.get("matched_source_doc_rank") or 0) <= 5
        )
        / denom,
        f"{prefix}_page_match@5": sum(1 for row in rows if row.get("page_match_at_5")) / denom,
        f"{prefix}_keyword_coverage_avg": sum(float(row.get("keyword_coverage") or 0.0) for row in rows) / denom,
        f"{prefix}_best_keyword_coverage@5_avg": sum(
            float(row.get("best_keyword_coverage_at_5") or 0.0) for row in rows
        )
        / denom,
    }


def present(value: Any) -> bool:
    return value not in ("", None, [], {})


def compare_rows(vector_rows: list[dict[str, Any]], hybrid_rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    vector_by_id = {str(row.get("case_id")): row for row in vector_rows}
    hybrid_by_id = {str(row.get("case_id")): row for row in hybrid_rows}
    improved: list[str] = []
    regressed: list[str] = []
    unchanged: list[str] = []
    still_failed: list[str] = []
    for case_id, v in vector_by_id.items():
        h = hybrid_by_id.get(case_id, {})
        v_rank = int(v.get("matched_source_doc_rank") or 0)
        h_rank = int(h.get("matched_source_doc_rank") or 0)
        if v_rank == 0 and h_rank == 0:
            still_failed.append(case_id)
        elif v_rank == 0 and h_rank > 0:
            improved.append(case_id)
        elif v_rank > 0 and h_rank == 0:
            regressed.append(case_id)
        elif h_rank < v_rank:
            improved.append(case_id)
        elif h_rank > v_rank:
            regressed.append(case_id)
        else:
            unchanged.append(case_id)
    return {
        "improved_by_hybrid": improved,
        "regressed_by_hybrid": regressed,
        "unchanged": unchanged,
        "still_failed": still_failed,
    }


def build_comparison(
    cases: list[dict[str, Any]],
    vector_rows: list[dict[str, Any]],
    hybrid_rows: list[dict[str, Any]],
    collection_name: str,
    top_k: int,
) -> dict[str, Any]:
    cmp = compare_rows(vector_rows, hybrid_rows)
    top_retrieval_sources = Counter(str(row.get("retrieval_source") or "") for row in hybrid_rows)
    top5_retrieval_sources: Counter[str] = Counter()
    for row in hybrid_rows:
        for hit in row.get("top_hits") or []:
            top5_retrieval_sources[str(hit.get("retrieval_source") or "")] += 1
    return {
        "total_cases": len(cases),
        "collection": collection_name,
        "top_k": top_k,
        "keyword_index_status": keyword_index_status(),
        **summarize(vector_rows, "vector"),
        **summarize(hybrid_rows, "hybrid"),
        "retrieval_source_distribution": dict(sorted(top_retrieval_sources.items())),
        "retrieval_source_distribution_at_5": dict(sorted(top5_retrieval_sources.items())),
        "bm25_score_presence": sum(1 for row in hybrid_rows if present(row.get("bm25_score"))),
        "rrf_score_presence": sum(1 for row in hybrid_rows if present(row.get("rrf_score"))),
        "keyword_score_presence": sum(1 for row in hybrid_rows if present(row.get("keyword_score"))),
        "rerank_score_presence": sum(1 for row in hybrid_rows if present(row.get("rerank_score"))),
        "bm25_score_presence_at_5": sum(1 for row in hybrid_rows if row.get("bm25_score_present_at_5")),
        "rrf_score_presence_at_5": sum(1 for row in hybrid_rows if row.get("rrf_score_present_at_5")),
        "keyword_score_presence_at_5": sum(1 for row in hybrid_rows if row.get("keyword_score_present_at_5")),
        **cmp,
    }


def write_report(path: Path, comparison: dict[str, Any], vector_rows: list[dict[str, Any]], hybrid_rows: list[dict[str, Any]]) -> None:
    vector_by_id = {str(row.get("case_id")): row for row in vector_rows}
    hybrid_by_id = {str(row.get("case_id")): row for row in hybrid_rows}

    def case_lines(title: str, ids: list[str]) -> list[str]:
        lines = [f"## {title}"]
        if not ids:
            return lines + ["- none"]
        for case_id in ids:
            v = vector_by_id.get(case_id, {})
            h = hybrid_by_id.get(case_id, {})
            lines.append(
                f"- {case_id}: vector_rank={v.get('matched_source_doc_rank')}, "
                f"hybrid_rank={h.get('matched_source_doc_rank')}, "
                f"hybrid_source={h.get('retrieval_source')}, expected={h.get('expected_source_doc')}, "
                f"top={h.get('top_source_doc')}"
            )
        return lines

    lines = [
        "# Normal Retrieval Vector vs Hybrid Evaluation",
        "",
        "## Executive Summary",
        f"- total_cases: {comparison['total_cases']}",
        f"- collection: {comparison['collection']}",
        f"- keyword_index_loaded: {comparison['keyword_index_status'].get('keyword_index_loaded')}",
        f"- vector_hit@1: {comparison['vector_hit@1']:.3f}",
        f"- vector_hit@5: {comparison['vector_hit@5']:.3f}",
        f"- vector_mrr: {comparison['vector_mrr']:.3f}",
        f"- hybrid_hit@1: {comparison['hybrid_hit@1']:.3f}",
        f"- hybrid_hit@5: {comparison['hybrid_hit@5']:.3f}",
        f"- hybrid_mrr: {comparison['hybrid_mrr']:.3f}",
        "",
        "## Hybrid Runtime Signals",
        f"- top1_retrieval_source_distribution: `{json.dumps(comparison['retrieval_source_distribution'], ensure_ascii=False)}`",
        f"- top5_retrieval_source_distribution: `{json.dumps(comparison['retrieval_source_distribution_at_5'], ensure_ascii=False)}`",
        f"- top1_bm25_score_presence: {comparison['bm25_score_presence']}",
        f"- top5_bm25_score_presence: {comparison['bm25_score_presence_at_5']}",
        f"- top1_rrf_score_presence: {comparison['rrf_score_presence']}",
        f"- top5_rrf_score_presence: {comparison['rrf_score_presence_at_5']}",
        f"- top1_keyword_score_presence: {comparison['keyword_score_presence']}",
        f"- top5_keyword_score_presence: {comparison['keyword_score_presence_at_5']}",
        "",
    ]
    lines.extend(case_lines("Improved By Hybrid", comparison["improved_by_hybrid"]))
    lines.extend([""])
    lines.extend(case_lines("Regressed By Hybrid", comparison["regressed_by_hybrid"]))
    lines.extend([""])
    lines.extend(case_lines("Still Failed", comparison["still_failed"]))
    lines.extend(
        [
            "",
            "## Commercial Judgment",
            "- This evaluates normal PDF chunks only and bypasses approved QA exact lookup.",
            "- Hybrid quality is acceptable only if it improves or preserves source_doc/page matching without increasing false positives.",
            "- Use this report as a measurement baseline; do not treat approved QA exact scores as normal retrieval quality.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    cases = read_jsonl(args.cases)
    vector_rows = evaluate_mode(cases, mode="vector_only", top_k=args.top_k, collection_name=args.collection)
    hybrid_rows = evaluate_mode(cases, mode="hybrid", top_k=args.top_k, collection_name=args.collection)
    comparison = build_comparison(cases, vector_rows, hybrid_rows, args.collection, args.top_k)

    write_jsonl(args.output_dir / "vector_only_results.jsonl", vector_rows)
    write_jsonl(args.output_dir / "hybrid_results.jsonl", hybrid_rows)
    (args.output_dir / "vector_vs_hybrid_comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_report(args.output_dir / "vector_vs_hybrid_comparison.md", comparison, vector_rows, hybrid_rows)
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
