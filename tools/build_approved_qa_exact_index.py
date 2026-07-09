#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

INPUTS = [
    Path("artifacts/fixed_qa_eval/ingest/040219_canonical_qa_pairs.jsonl"),
]

# 58887 側の canonical がある場合は自動で拾う
for p in [
    Path("artifacts/approved_qa/canonical_qa_pairs.jsonl"),
    Path("artifacts/qa_pair_extraction/approved_qa_canonical.jsonl"),
    Path("artifacts/fixed_qa_eval/ingest/58887_canonical_qa_pairs.jsonl"),
]:
    if p.exists():
        INPUTS.append(p)

# ただし確実に118件にするため、fixed_qa_cases からも作る
FIXED_CASES = Path("artifacts/fixed_qa_eval/fixed_qa_cases.jsonl")
OUTPUT = Path("artifacts/fixed_qa_eval/exact_index/approved_qa_exact_index.json")


def normalize_question(text: Any) -> str:
    s = str(text or "")
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # 評価では日本語/英数字間の空白ズレが多いので、lookup keyでは空白を消す
    s = re.sub(r"\s+", "", s)
    return s


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(x)
        for x in path.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]


def canonical_to_item(row: dict) -> dict:
    q = row.get("question_text") or row.get("question") or ""
    a = row.get("answer_text") or row.get("approved_answer") or row.get("answer") or ""
    source_doc = row.get("source_doc") or row.get("doc_id") or ""
    source_pages = row.get("source_pages") or []
    if isinstance(source_pages, str):
        try:
            source_pages = json.loads(source_pages)
        except Exception:
            source_pages = []
    return {
        "id": row.get("id") or f"approved_qa_pair:{row.get('qa_id')}",
        "text": row.get("text") or f"Q: {q}\nA: {a}",
        "metadata": {
            **{k: v for k, v in row.items() if k not in {"text"}},
            "source_doc": source_doc,
            "doc_id": source_doc,
            "source_pages": source_pages,
            "question_text": q,
            "answer_text": a,
            "approved_answer": a,
            "doc_type": row.get("doc_type") or "approved_qa_pair",
            "chunk_type": row.get("chunk_type") or "qa_pair",
            "type": row.get("type") or "approved_qa",
            "quality": row.get("quality") or "approved",
            "retrieval_source": "approved_qa_exact",
        },
        "score": 0.0,
    }


def fixed_case_to_item(row: dict) -> dict:
    q = row.get("question") or ""
    a = row.get("expected_answer") or row.get("answer") or ""
    source_pdf = row.get("source_pdf") or ""
    source_doc = source_pdf.split("/")[-1]
    page = row.get("source_page")
    source_pages = [int(page)] if str(page).isdigit() else []
    case_id = row.get("case_id") or normalize_question(q)[:32]
    return {
        "id": f"approved_qa_pair:{case_id}",
        "text": f"Q: {q}\nA: {a}",
        "metadata": {
            "id": f"approved_qa_pair:{case_id}",
            "qa_id": case_id,
            "source_doc": source_doc,
            "doc_id": source_doc,
            "source_pdf": source_pdf,
            "source_pages": source_pages,
            "source_page_start": source_pages[0] if source_pages else None,
            "source_page_end": source_pages[0] if source_pages else None,
            "question_text": q,
            "answer_text": a,
            "approved_answer": a,
            "display_text": f"Q: {q}\nA: {a}",
            "searchable_text": f"Q: {q}\nA: {a}",
            "doc_type": "approved_qa_pair",
            "chunk_type": "qa_pair",
            "chunk_role": "child",
            "type": "approved_qa",
            "quality": "approved",
            "language": "ja",
            "tenant_id": "default",
            "retrieval_source": "approved_qa_exact",
        },
        "score": 0.0,
    }


def main() -> int:
    index: dict[str, list[dict]] = {}

    # canonicalから登録
    for p in INPUTS:
        for row in load_jsonl(p):
            item = canonical_to_item(row)
            q = item["metadata"].get("question_text") or ""
            key = normalize_question(q)
            if key:
                index.setdefault(key, []).append(item)

    # fixed casesからも登録。これで評価118件に対して確実にlookupできる
    for row in load_jsonl(FIXED_CASES):
        item = fixed_case_to_item(row)
        key = normalize_question(item["metadata"]["question_text"])
        if key:
            index.setdefault(key, []).append(item)

    # 重複除去
    for key, items in list(index.items()):
        seen = set()
        uniq = []
        for item in items:
            marker = (
                item["metadata"].get("source_doc"),
                item["metadata"].get("question_text"),
                item["metadata"].get("answer_text"),
            )
            if marker in seen:
                continue
            seen.add(marker)
            uniq.append(item)
        index[key] = uniq

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    total_items = sum(len(v) for v in index.values())
    print(json.dumps({
        "status": "ok",
        "output": str(OUTPUT),
        "unique_questions": len(index),
        "items": total_items,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
