#!/usr/bin/env python3
"""Analyze retrieval_source and score metadata returned by /search_debug or exact lookup."""

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

from rag_core.approved_qa_exact_lookup import lookup_approved_qa_exact
from rag_core.retrieval import keyword_index_status


OUT_DIR = Path("artifacts/current_qa_hybrid_analysis")
FIXED_CASES = Path("artifacts/fixed_qa_eval/fixed_qa_cases.jsonl")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    try:
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


def score_field_counts(hit: dict[str, Any]) -> Counter[str]:
    out: Counter[str] = Counter()
    meta = hit.get("metadata") or {}
    details = meta.get("score_details") or {}
    for key in ("bm25_score", "rrf_score", "rerank_score", "keyword_score"):
        if meta.get(key) is not None or details.get(key) is not None:
            out[key] += 1
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    cases = read_jsonl(FIXED_CASES)[: args.limit]
    source_counts: Counter[str] = Counter()
    score_counts: Counter[str] = Counter()
    per_case = []
    api_errors = 0
    for case in cases:
        question = case.get("question", "")
        exact_hits = lookup_approved_qa_exact(question, limit=5)
        if exact_hits:
            top_source = (exact_hits[0].get("metadata") or {}).get("retrieval_source") or ""
            source_counts[str(top_source)] += 1
            per_case.append({"case_id": case.get("case_id", ""), "mode": "local_exact", "top_source": top_source})
            continue
        response = post_json(
            f"{args.base_url}/search/debug",
            {"query": question, "top_k": 5, "include_context": True, "generate_answer": False},
            args.timeout,
        )
        if not response.get("ok"):
            api_errors += 1
            per_case.append({"case_id": case.get("case_id", ""), "mode": "api_error", "error": response.get("error")})
            continue
        data = response.get("json") or {}
        hits = data.get("before_rerank") or []
        top = hits[0] if hits else {}
        meta = top.get("metadata") or {}
        top_source = str(meta.get("retrieval_source") or "")
        source_counts[top_source] += 1
        for hit in hits:
            score_counts.update(score_field_counts(hit))
        per_case.append({"case_id": case.get("case_id", ""), "mode": "search_debug", "top_source": top_source})

    status = keyword_index_status()
    result = {
        "cases": len(cases),
        "api_errors": api_errors,
        "keyword_index_status": status,
        "retrieval_source_counts": dict(source_counts),
        "score_field_counts": dict(score_counts),
        "per_case": per_case,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "retrieval_source_analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = [
        "# Retrieval Source Analysis",
        "",
        f"- cases: {len(cases)}",
        f"- api_errors: {api_errors}",
        f"- keyword_index_loaded: {status.get('keyword_index_loaded')}",
        f"- keyword_index_records: {status.get('keyword_index_records')}",
        "",
        "## Retrieval Source Counts",
        "",
    ]
    for key, value in sorted(source_counts.items()):
        md.append(f"- {key}: {value}")
    md.extend(["", "## Score Field Counts", ""])
    for key, value in sorted(score_counts.items()):
        md.append(f"- {key}: {value}")
    (OUT_DIR / "retrieval_source_analysis.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
