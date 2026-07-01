#!/usr/bin/env python3
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

INPUT = Path("artifacts/qa_pair_extraction_040219_table/qa_pair_cases_040219_table.jsonl")
OUTPUT = Path("artifacts/fixed_qa_eval/ingest/040219_approved_qa_ingest.jsonl")

def make_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    rows = [
        json.loads(x)
        for x in INPUT.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]

    now = datetime.now(timezone.utc).isoformat()

    with OUTPUT.open("w", encoding="utf-8") as f:
        for i, r in enumerate(rows, start=1):
            q = str(r.get("question") or "").strip()
            a = str(r.get("expected_answer") or "").strip()
            page = int(r.get("source_page") or 0)
            topic = str(r.get("source_topic") or "").strip()
            case_id = str(r.get("case_id") or f"biscfaq_{i:04d}")
            qa_id = "biscfaq_" + make_id(case_id + q + a)

            if not q or not a:
                raise ValueError(f"missing q/a at row {i}: {case_id}")

            citation = {
                "source_doc": "040219e-biscfaq.pdf",
                "source_pages": [page],
                "chunk_id": qa_id,
                "title": "電子入札システム FAQ",
            }

            item = {
                # converter必須
                "qa_id": qa_id,
                "question": q,
                "approved_answer": a,
                "status": "approved",
                "approved_citations": [citation],

                # metadata
                "source_doc": "040219e-biscfaq.pdf",
                "source_pages": [page],
                "title": "電子入札システム FAQ",
                "question_item": topic,
                "source_question_no": i,
                "language": "ja",
                "tenant_id": "default",
                "doc_version": "v1",
                "reviewed_by": "rai",
                "reviewed_at": now,
                "review_notes": "bulk approved from reviewed QA extraction",

                # 互換・検索用
                "doc_id": "040219e-biscfaq.pdf",
                "source_pdf": "pdfs/040219e-biscfaq.pdf",
                "question_text": q,
                "normalized_question": q,
                "answer": a,
                "answer_text": a,
                "quality": "approved",
                "type": "approved_qa",
                "doc_type": "approved_qa_pair",
                "chunk_type": "qa_pair",
                "searchable": 1,
                "display_text": f"Q: {q}\nA: {a}",
                "searchable_text": f"Q: {q}\nA: {a}",
                "extraction_method": "biscfaq_table_qa_to_canonical_jsonl",
            }

            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(json.dumps({
        "status": "ok",
        "input": str(INPUT),
        "output": str(OUTPUT),
        "rows": len(rows),
    }, ensure_ascii=False, indent=2))

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
