#!/usr/bin/env python3
"""Analyze current approved-QA and retrieval wiring without changing runtime code."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from rag_core.approved_qa import load_approved_qa
from rag_core.approved_qa_exact_lookup import lookup_approved_qa_exact
from rag_core.retrieval import keyword_index_status


OUT_DIR = Path("artifacts/current_qa_hybrid_analysis")
FIXED_CASES = Path("artifacts/fixed_qa_eval/fixed_qa_cases.jsonl")
EXACT_INDEX = Path("artifacts/fixed_qa_eval/exact_index/approved_qa_exact_index.json")
APPROVED_INGEST = Path("artifacts/fixed_qa_eval/ingest/040219_approved_qa_ingest.jsonl")
CANONICAL_QA = Path("artifacts/fixed_qa_eval/ingest/040219_canonical_qa_pairs.jsonl")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def http_json(url: str, payload: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    try:
        if payload is None:
            req = urllib.request.Request(url)
        else:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": resp.status, "json": json.loads(resp.read().decode("utf-8"))}
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def count_exact_index(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"unique_questions": 0, "items": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"unique_questions": 0, "items": 0}
    return {"unique_questions": len(data), "items": sum(len(v) for v in data.values() if isinstance(v, list))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    fixed_cases = read_jsonl(FIXED_CASES)
    approved_ingest = read_jsonl(APPROVED_INGEST)
    canonical_qa = read_jsonl(CANONICAL_QA)
    exact_counts = count_exact_index(EXACT_INDEX)

    try:
        approved_index = load_approved_qa(config.APPROVED_QA_PATH, tenant_id=args.tenant_id)
        approved_count = len(approved_index.records)
        approved_error = ""
    except Exception as exc:
        approved_count = 0
        approved_error = f"{type(exc).__name__}: {exc}"

    exact_probe = []
    for row in fixed_cases[: args.limit]:
        question = row.get("question") or ""
        hits = lookup_approved_qa_exact(question, limit=3)
        exact_probe.append(
            {
                "case_id": row.get("case_id", ""),
                "exact_hits": len(hits),
                "top_retrieval_source": ((hits[0].get("metadata") or {}).get("retrieval_source") if hits else ""),
            }
        )

    health = http_json(f"{args.base_url}/health")
    debug = http_json(
        f"{args.base_url}/search/debug",
        {
            "query": "Java Plug-in アプレットインストールの警告",
            "top_k": 5,
            "include_context": True,
            "generate_answer": False,
            "include_approved_similar_candidates": True,
        },
        timeout=15,
    )

    debug_sources: Counter[str] = Counter()
    debug_score_fields: Counter[str] = Counter()
    if debug.get("ok"):
        for section in ("before_rerank", "after_rerank", "after_parent_expansion"):
            for hit in (debug.get("json") or {}).get(section, []) or []:
                meta = hit.get("metadata") or {}
                debug_sources[str(meta.get("retrieval_source") or "")] += 1
                score_details = meta.get("score_details") or {}
                for key in ("bm25_score", "rrf_score", "rerank_score", "keyword_score"):
                    if meta.get(key) is not None or score_details.get(key) is not None:
                        debug_score_fields[key] += 1

    keyword_status = keyword_index_status()
    result = {
        "approved_qa_enabled": bool(getattr(config, "APPROVED_QA_ENABLED", False)),
        "approved_qa_path": str(config.APPROVED_QA_PATH),
        "approved_qa_records_for_tenant": approved_count,
        "approved_qa_load_error": approved_error,
        "fixed_qa_cases": len(fixed_cases),
        "approved_qa_ingest_records": len(approved_ingest),
        "canonical_qa_pair_records": len(canonical_qa),
        "exact_index": exact_counts,
        "exact_probe": exact_probe,
        "keyword_index_status": keyword_status,
        "config": {
            "enable_hybrid_retrieval": bool(config.ENABLE_HYBRID_RETRIEVAL),
            "bm25_top_k": config.BM25_TOP_K,
            "vector_top_k": config.VECTOR_TOP_K,
            "hybrid_rrf_k": config.HYBRID_RRF_K,
            "cross_encoder_rerank_enabled": bool(config.CROSS_ENCODER_RERANK_ENABLED),
            "keyword_boost_enabled": bool(config.KEYWORD_BOOST_ENABLED),
        },
        "api": {"health": health, "search_debug": debug},
        "search_debug_retrieval_sources": dict(debug_sources),
        "search_debug_score_fields": dict(debug_score_fields),
        "conclusions": {
            "search_exact_lookup_available_locally": any(item["exact_hits"] for item in exact_probe),
            "chat_uses_exact_index": False,
            "chat_uses_approved_qa_jsonl_when_enabled": True,
            "search_fallback_is_vector_only": True,
            "chat_non_staging_path_uses_hybrid_retrieve": True,
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "current_qa_hybrid_analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = [
        "# Current QA / Hybrid Retrieval Analysis",
        "",
        f"- approved_qa_enabled: {result['approved_qa_enabled']}",
        f"- approved_qa_path: `{result['approved_qa_path']}`",
        f"- approved_qa_records_for_tenant: {approved_count}",
        f"- fixed_qa_cases: {len(fixed_cases)}",
        f"- approved_qa_ingest_records: {len(approved_ingest)}",
        f"- canonical_qa_pair_records: {len(canonical_qa)}",
        f"- exact_index_unique_questions: {exact_counts['unique_questions']}",
        f"- exact_index_items: {exact_counts['items']}",
        f"- keyword_index_loaded: {keyword_status.get('keyword_index_loaded')}",
        f"- keyword_index_records: {keyword_status.get('keyword_index_records')}",
        f"- health_ok: {health.get('ok')}",
        f"- search_debug_ok: {debug.get('ok')}",
        "",
        "## Conclusions",
        "",
        "- `/search` has approved QA exact lookup before vector fallback.",
        "- `/chat` uses `approved_qa.py` JSONL exact lookup when enabled, not `approved_qa_exact_lookup.py`.",
        "- `retrieve_chunks()` is vector-only, so `/search` fallback is vector-only.",
        "- normal non-staging `/chat` retrieval uses `hybrid_retrieve()` in `qa.py`.",
        "- staging `/chat` retrieval uses vector retrieval only.",
        "",
    ]
    if not health.get("ok"):
        md.append(f"- API health check failed: `{health.get('error')}`")
    (OUT_DIR / "current_qa_hybrid_analysis.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
