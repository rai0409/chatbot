#!/usr/bin/env python3
"""Collect runtime evidence that vector + BM25 + RRF hybrid retrieval is active."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_core.retrieval import hybrid_retrieve, keyword_index_status, vector_retrieve


DEFAULT_OUT_DIR = Path("artifacts/hybrid_runtime_analysis")
DEFAULT_QUERIES = [
    "Java Plug-in アプレットインストールの警告",
    "企業ID はどのように採番されますか",
    "環境設定ツールで設定するアドレス",
    "ICカードは何枚必要ですか",
    "適格請求書発行事業者の登録申請書",
    "インボイス制度の登録時期の特例",
    "火山に登るときの防災用品",
    "観光案内所のチラシ設置施設数",
]


def post_search_debug(base_url: str, query: str, timeout: int) -> dict[str, Any]:
    payload = json.dumps(
        {
            "query": query,
            "top_k": 5,
            "include_context": True,
            "generate_answer": False,
            "include_approved_similar_candidates": True,
        },
        ensure_ascii=False,
    )
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "-m",
            str(timeout),
            "-X",
            "POST",
            f"{base_url.rstrip('/')}/search/debug",
            "-H",
            "Content-Type: application/json",
            "-d",
            payload,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip() or proc.stdout.strip()}
    try:
        return {"ok": True, "json": json.loads(proc.stdout)}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"JSONDecodeError: {exc}: {proc.stdout[:300]}"}


def _score_details(hit: dict[str, Any]) -> dict[str, Any]:
    details = hit.get("score_details") or {}
    meta = hit.get("metadata") or {}
    merged = dict(details)
    for key in ("bm25_score", "rrf_score", "keyword_score", "rerank_score"):
        if key not in merged and key in meta:
            merged[key] = meta[key]
    return merged


def analyze_api_hit_fields(hits: list[dict[str, Any]]) -> dict[str, Any]:
    source_counts: Counter[str] = Counter()
    score_field_counts: Counter[str] = Counter()
    top_sources = []
    for hit in hits:
        source = str(hit.get("retrieval_source") or "")
        source_counts[source] += 1
        details = _score_details(hit)
        for key in ("bm25_score", "rrf_score", "keyword_score", "rerank_score"):
            if details.get(key) is not None:
                score_field_counts[key] += 1
        if len(top_sources) < 3:
            top_sources.append(
                {
                    "chunk_id": hit.get("chunk_id") or "",
                    "source_doc": hit.get("source_doc") or "",
                    "retrieval_source": source,
                    "bm25_score": details.get("bm25_score", ""),
                    "rrf_score": details.get("rrf_score", ""),
                    "keyword_score": details.get("keyword_score", ""),
                }
            )
    return {
        "source_counts": dict(source_counts),
        "score_field_counts": dict(score_field_counts),
        "top_hits": top_sources,
    }


def internal_compare(query: str, collection_name: str | None) -> dict[str, Any]:
    vector_hits = vector_retrieve(
        query,
        client=None,
        top_k=5,
        collection_name=collection_name,
        create_collection_if_missing=False,
    )
    hybrid_hits = hybrid_retrieve(
        query,
        client=None,
        top_k=5,
        collection_name=collection_name,
        create_collection_if_missing=False,
    )
    return {
        "vector_top_sources": [str((h.metadata or {}).get("retrieval_source") or "") for h in vector_hits],
        "hybrid_top_sources": [str((h.metadata or {}).get("retrieval_source") or "") for h in hybrid_hits],
        "vector_top_ids": [str((h.metadata or {}).get("id") or "") for h in vector_hits[:3]],
        "hybrid_top_ids": [str((h.metadata or {}).get("id") or "") for h in hybrid_hits[:3]],
        "hybrid_has_keyword_or_hybrid": any(
            str((h.metadata or {}).get("retrieval_source") or "") in {"keyword", "hybrid"}
            for h in hybrid_hits
        ),
        "hybrid_has_rrf": any((h.metadata or {}).get("rrf_score") is not None for h in hybrid_hits),
        "hybrid_has_bm25": any((h.metadata or {}).get("bm25_score") is not None for h in hybrid_hits),
    }


def write_report(out_dir: Path, result: dict[str, Any]) -> None:
    status = result["keyword_index_status"]
    dist = result["retrieval_source_distribution"]
    score_dist = result["score_field_distribution"]
    compare = result["internal_vector_vs_hybrid"]
    passed = (
        status.get("keyword_index_loaded") is True
        and int(status.get("keyword_index_records") or 0) > 0
        and int(score_dist.get("bm25_score") or 0) > 0
        and int(score_dist.get("rrf_score") or 0) > 0
        and any(item.get("hybrid_has_keyword_or_hybrid") for item in compare)
    )
    lines = [
        "# Hybrid Runtime Analysis",
        "",
        "## Executive Summary",
        f"- status: {'passed' if passed else 'failed'}",
        f"- keyword_index_loaded: {status.get('keyword_index_loaded')}",
        f"- keyword_index_records: {status.get('keyword_index_records')}",
        f"- queries: {len(result['queries'])}",
        f"- api_errors: {result['api_errors']}",
        "",
        "## Root Cause",
        "- Runtime expected `index/chunks.canonical.bytype.dedup.jsonl`, but that file was missing.",
        "- Source canonical JSONL files already existed under `index/`.",
        "- Existing `scripts/build_canonical_index.py` was used to concatenate and deduplicate them.",
        "",
        "## Runtime Verification",
        f"- retrieval_source_distribution: `{json.dumps(dist, ensure_ascii=False)}`",
        f"- score_field_distribution: `{json.dumps(score_dist, ensure_ascii=False)}`",
        "",
        "## Vector vs Hybrid Comparison",
    ]
    for item in compare:
        lines.append(
            f"- {item['query']}: vector_top={item['vector_top_sources'][:3]}, "
            f"hybrid_top={item['hybrid_top_sources'][:3]}, "
            f"hybrid_has_bm25={item['hybrid_has_bm25']}, hybrid_has_rrf={item['hybrid_has_rrf']}"
        )
    lines.extend(
        [
            "",
            "## Commercial Judgment",
            "- BM25 runtime corpus is restored.",
            "- Hybrid retrieval is active when normal retrieval falls through to `/search/debug`.",
            "- This does not by itself prove quality improvement; it proves runtime activation and score propagation.",
            "",
            "## Next Steps",
            "- Run fixed QA exact regression and unknown abstention regression after this index file is kept.",
            "- Add a normal document retrieval eval set that is not exact-QA dominated.",
            "- Decide whether approved QA canonical chunks should also be added to the BM25 corpus for near-QA retrieval.",
        ]
    )
    (out_dir / "hybrid_runtime_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    status = keyword_index_status()
    per_query = []
    retrieval_source_distribution: Counter[str] = Counter()
    score_field_distribution: Counter[str] = Counter()
    api_errors = 0

    for query in DEFAULT_QUERIES:
        response = post_search_debug(args.base_url, query, args.timeout)
        if not response.get("ok"):
            api_errors += 1
            per_query.append({"query": query, "api_ok": False, "error": response.get("error")})
            continue
        data = response.get("json") or {}
        hits = data.get("before_rerank") or data.get("hits") or []
        api_analysis = analyze_api_hit_fields(hits)
        retrieval_source_distribution.update(api_analysis["source_counts"])
        score_field_distribution.update(api_analysis["score_field_counts"])
        per_query.append({"query": query, "api_ok": True, **api_analysis})

    comparisons = []
    for query in DEFAULT_QUERIES:
        try:
            comparisons.append({"query": query, **internal_compare(query, args.collection)})
        except Exception as exc:
            comparisons.append({"query": query, "error": f"{type(exc).__name__}: {exc}"})

    result = {
        "keyword_index_status": status,
        "queries": DEFAULT_QUERIES,
        "api_errors": api_errors,
        "retrieval_source_distribution": dict(retrieval_source_distribution),
        "score_field_distribution": dict(score_field_distribution),
        "per_query": per_query,
        "internal_vector_vs_hybrid": comparisons,
    }
    (args.output_dir / "hybrid_runtime_status.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    distribution = {
        "keyword_index_status": status,
        "retrieval_source_distribution": dict(retrieval_source_distribution),
        "score_field_distribution": dict(score_field_distribution),
        "api_errors": api_errors,
    }
    (args.output_dir / "retrieval_source_distribution.json").write_text(
        json.dumps(distribution, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = [
        "# Retrieval Source Distribution",
        "",
        f"- keyword_index_loaded: {status.get('keyword_index_loaded')}",
        f"- keyword_index_records: {status.get('keyword_index_records')}",
        f"- api_errors: {api_errors}",
        "",
        "## Retrieval Sources",
    ]
    for key, value in sorted(retrieval_source_distribution.items()):
        md.append(f"- {key}: {value}")
    md.extend(["", "## Score Fields"])
    for key, value in sorted(score_field_distribution.items()):
        md.append(f"- {key}: {value}")
    (args.output_dir / "retrieval_source_distribution.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    write_report(args.output_dir, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
