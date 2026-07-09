#!/usr/bin/env python3
import argparse
import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


def normalize_eval_text(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    s = unicodedata.normalize("NFKC", s)
    s = s.lower()
    s = re.sub(r"\s+", "", s)
    return s.strip()


def normalize_search_text(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(x)
        for x in path.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]


def extract_text_from_candidate(x: Any) -> str:
    if isinstance(x, str):
        return x
    if not isinstance(x, dict):
        return ""

    keys = [
        "text",
        "content",
        "page_content",
        "chunk",
        "document",
        "body",
        "answer",
        "snippet",
    ]

    for k in keys:
        if x.get(k):
            return str(x.get(k))

    # metadata内に本文があるケース
    meta = x.get("metadata") or x.get("meta") or {}
    if isinstance(meta, dict):
        for k in keys:
            if meta.get(k):
                return str(meta.get(k))

    return json.dumps(x, ensure_ascii=False)


def extract_candidates(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = []
        for key in ["hits", "results", "documents", "matches", "items", "chunks", "data"]:
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    else:
        items = []

    out = []
    for i, item in enumerate(items, start=1):
        text = extract_text_from_candidate(item)

        metadata = {}
        score = None
        if isinstance(item, dict):
            metadata = item.get("metadata") or item.get("meta") or {}
            score = (
                item.get("score")
                if item.get("score") is not None
                else item.get("similarity")
                if item.get("similarity") is not None
                else item.get("distance")
            )

        out.append({
            "rank": i,
            "text": text,
            "text_eval": normalize_eval_text(text),
            "metadata": metadata,
            "score": score,
            "raw": item,
        })

    return out


def call_search(
    search_url: str,
    query: str,
    top_k: int,
    tenant: str = "",
    collection: str = "",
    timeout: int = 30,
) -> Any:
    payload_variants = []

    base = {
        "query": query,
        "top_k": top_k,
        "k": top_k,
        "limit": top_k,
    }

    if tenant:
        base["tenant"] = tenant
        base["tenant_id"] = tenant

    if collection:
        base["collection"] = collection
        base["collection_name"] = collection

    payload_variants.append(base)

    payload_variants.append({
        "q": query,
        "top_k": top_k,
        "tenant": tenant,
        "collection": collection,
    })

    last_error = ""

    for payload in payload_variants:
        try:
            r = requests.post(search_url, json=payload, timeout=timeout)
            if r.status_code >= 400:
                last_error = f"HTTP {r.status_code}: {r.text[:300]}"
                continue
            return r.json()
        except Exception as e:
            last_error = repr(e)

    raise RuntimeError(last_error)


def count_hits(text: str, terms: List[str]) -> int:
    text_eval = normalize_eval_text(text)
    hits = 0
    for term in terms or []:
        t = normalize_eval_text(term)
        if t and t in text_eval:
            hits += 1
    return hits


def evaluate_one(case: Dict[str, Any], candidates: List[Dict[str, Any]], top_k: int) -> Dict[str, Any]:
    expected_answer = case.get("expected_answer", "")
    expected_answer_eval = case.get("expected_answer_eval_text") or normalize_eval_text(expected_answer)

    expected_any = case.get("expected_any", []) or []
    expected_all = case.get("expected_all", []) or []
    expected_min_hits = int(case.get("expected_min_hits") or 0)

    found_rank = None
    match_reason = ""
    best_any_hits = 0
    best_all_hits = 0

    for c in candidates[:top_k]:
        text = c["text"]
        text_eval = c["text_eval"]

        answer_hit = bool(expected_answer_eval and expected_answer_eval in text_eval)
        any_hits = count_hits(text, expected_any)
        all_hits = count_hits(text, expected_all)

        best_any_hits = max(best_any_hits, any_hits)
        best_all_hits = max(best_all_hits, all_hits)

        keyword_hit = False
        if expected_min_hits > 0 and any_hits >= expected_min_hits:
            keyword_hit = True
        if expected_all and all_hits >= len(expected_all):
            keyword_hit = True

        if answer_hit or keyword_hit:
            found_rank = c["rank"]
            match_reason = "expected_answer" if answer_hit else "expected_terms"
            break

    return {
        "case_id": case.get("case_id"),
        "source_pdf": case.get("source_pdf"),
        "source_page": case.get("source_page"),
        "question_type": case.get("question_type"),
        "question": case.get("question"),
        "query_used": case.get("question_search_text") or normalize_search_text(case.get("question")),
        "expected_answer": expected_answer,
        "found_rank": found_rank,
        "match_reason": match_reason,
        "hit_at_1": bool(found_rank and found_rank <= 1),
        "hit_at_3": bool(found_rank and found_rank <= 3),
        "hit_at_5": bool(found_rank and found_rank <= 5),
        "hit_at_k": bool(found_rank and found_rank <= top_k),
        "mrr": (1.0 / found_rank) if found_rank else 0.0,
        "best_expected_any_hits": best_any_hits,
        "best_expected_all_hits": best_all_hits,
        "top_result_preview": candidates[0]["text"][:500] if candidates else "",
        "top_result_score": candidates[0].get("score") if candidates else None,
        "top_result_metadata": candidates[0].get("metadata") if candidates else {},
        "error": "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="artifacts/fixed_qa_eval/fixed_qa_cases.jsonl")
    parser.add_argument("--search-url", default="http://127.0.0.1:8000/search")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--tenant", default="")
    parser.add_argument("--collection", default="")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output-dir", default="artifacts/fixed_qa_eval/retrieval_eval")
    args = parser.parse_args()

    cases = read_jsonl(Path(args.cases))
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []

    for i, case in enumerate(cases, start=1):
        query = case.get("question_search_text") or normalize_search_text(case.get("question"))

        try:
            payload = call_search(
                args.search_url,
                query=query,
                top_k=args.top_k,
                tenant=args.tenant,
                collection=args.collection,
                timeout=args.timeout,
            )
            candidates = extract_candidates(payload)
            result = evaluate_one(case, candidates, args.top_k)
        except Exception as e:
            result = {
                "case_id": case.get("case_id"),
                "source_pdf": case.get("source_pdf"),
                "source_page": case.get("source_page"),
                "question_type": case.get("question_type"),
                "question": case.get("question"),
                "query_used": query,
                "found_rank": None,
                "match_reason": "",
                "hit_at_1": False,
                "hit_at_3": False,
                "hit_at_5": False,
                "hit_at_k": False,
                "mrr": 0.0,
                "best_expected_any_hits": 0,
                "best_expected_all_hits": 0,
                "top_result_preview": "",
                "top_result_score": None,
                "top_result_metadata": {},
                "error": repr(e),
            }

        results.append(result)

        if i % 10 == 0:
            print(f"evaluated {i}/{len(cases)}")

    jsonl_path = out_dir / "retrieval_eval_results.jsonl"
    csv_path = out_dir / "retrieval_eval_results.csv"
    report_path = out_dir / "retrieval_eval_report.md"

    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    fields = [
        "case_id",
        "source_pdf",
        "source_page",
        "question_type",
        "question",
        "found_rank",
        "match_reason",
        "hit_at_1",
        "hit_at_3",
        "hit_at_5",
        "hit_at_k",
        "mrr",
        "best_expected_any_hits",
        "best_expected_all_hits",
        "error",
        "top_result_preview",
    ]

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in fields})

    n = len(results)
    hit1 = sum(1 for r in results if r["hit_at_1"])
    hit3 = sum(1 for r in results if r["hit_at_3"])
    hit5 = sum(1 for r in results if r["hit_at_5"])
    hitk = sum(1 for r in results if r["hit_at_k"])
    errors = sum(1 for r in results if r.get("error"))
    mrr = sum(float(r.get("mrr") or 0) for r in results) / max(1, n)

    misses = [r for r in results if not r["hit_at_k"] or r.get("error")]

    lines = [
        "# Retrieval Evaluation Report",
        "",
        f"- cases: {args.cases}",
        f"- search_url: {args.search_url}",
        f"- top_k: {args.top_k}",
        f"- total_cases: {n}",
        f"- errors: {errors}",
        "",
        "## Metrics",
        "",
        f"- hit@1: {hit1}/{n} = {hit1 / max(1, n):.3f}",
        f"- hit@3: {hit3}/{n} = {hit3 / max(1, n):.3f}",
        f"- hit@5: {hit5}/{n} = {hit5 / max(1, n):.3f}",
        f"- hit@k: {hitk}/{n} = {hitk / max(1, n):.3f}",
        f"- mrr: {mrr:.3f}",
        "",
        "## Output Files",
        "",
        f"- {jsonl_path}",
        f"- {csv_path}",
        f"- {report_path}",
        "",
        "## Missed / Error Cases",
        "",
    ]

    for r in misses[:80]:
        lines.append(f"- {r.get('case_id')} rank={r.get('found_rank')} error={r.get('error')}")
        lines.append(f"  - source: {r.get('source_pdf')} page {r.get('source_page')}")
        lines.append(f"  - Q: {str(r.get('question'))[:180]}")
        lines.append(f"  - top: {str(r.get('top_result_preview'))[:220]}")

    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "total_cases": n,
        "errors": errors,
        "hit_at_1": hit1 / max(1, n),
        "hit_at_3": hit3 / max(1, n),
        "hit_at_5": hit5 / max(1, n),
        "hit_at_k": hitk / max(1, n),
        "mrr": mrr,
        "output_files": [
            str(jsonl_path),
            str(csv_path),
            str(report_path),
        ],
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
