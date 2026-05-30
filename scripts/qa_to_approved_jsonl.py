from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from rag_core.approved_qa import validate_approved_qa_records
from rag_core.question_normalization import normalize_question_for_exact_match


ALLOWED_STATUSES = {"draft", "approved", "rejected"}


def parse_source_pages(value: Any) -> List[int]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw = str(value).strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                raw_items = parsed if isinstance(parsed, list) else [parsed]
            except Exception:
                raw_items = [raw]
        else:
            raw_items = raw.split(",")

    pages: List[int] = []
    for item in raw_items:
        text = str(item).strip()
        if not text:
            continue
        pages.append(int(text))
    return pages


def _parse_tags(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw = str(value).strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                raw_items = parsed if isinstance(parsed, list) else [parsed]
            except Exception:
                raw_items = raw.split(",")
        else:
            raw_items = raw.split(",")
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _stable_qa_id(tenant_id: str, normalized_question: str, approved_answer: str) -> str:
    payload = f"{tenant_id}\n{normalized_question}\n{approved_answer}".encode("utf-8")
    return "qa_" + hashlib.sha256(payload).hexdigest()[:16]


def _read_csv(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def _read_json(path: Path) -> List[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [dict(item) for item in data]
    if isinstance(data, dict):
        records = data.get("records") or data.get("items") or data.get("qa") or data.get("data")
        if isinstance(records, list):
            return [dict(item) for item in records]
        return [data]
    raise ValueError("JSON input must be an object or array")


def _read_jsonl(path: Path) -> List[dict]:
    records: List[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if raw:
                records.append(json.loads(raw))
    return records


def read_input(path: str | Path, fmt: str) -> List[dict]:
    in_path = Path(path)
    if fmt == "csv":
        return _read_csv(in_path)
    if fmt == "json":
        return _read_json(in_path)
    if fmt == "jsonl":
        return _read_jsonl(in_path)
    raise ValueError(f"unsupported format: {fmt}")


def _flat_citation(row: dict) -> dict | None:
    source_doc = str(row.get("source_doc") or "").strip()
    if not source_doc:
        return None
    citation: Dict[str, Any] = {
        "source_doc": source_doc,
        "source_pages": parse_source_pages(row.get("source_pages")),
    }
    chunk_id = str(row.get("chunk_id") or "").strip()
    title = str(row.get("title") or "").strip()
    if chunk_id:
        citation["chunk_id"] = chunk_id
    if title:
        citation["title"] = title
    return citation


def _normalize_citations(row: dict) -> List[dict]:
    citations = row.get("approved_citations")
    if citations not in (None, ""):
        if isinstance(citations, str):
            citations = json.loads(citations)
        if not isinstance(citations, list):
            raise ValueError("approved_citations must be a list")
        out: List[dict] = []
        for citation in citations:
            if not isinstance(citation, dict):
                raise ValueError("approved_citations entries must be objects")
            item = dict(citation)
            item["source_pages"] = parse_source_pages(item.get("source_pages"))
            out.append(item)
        return out
    flat = _flat_citation(row)
    return [flat] if flat is not None else []


def convert_records(
    rows: Iterable[dict],
    *,
    tenant_id: str = "default",
    status: str = "draft",
    created_at: str | None = None,
) -> Tuple[List[dict], List[str]]:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid --status: {status}")
    timestamp = created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    records: List[dict] = []
    conversion_errors: List[str] = []
    for idx, row in enumerate(rows, start=1):
        try:
            question = str(row.get("question") or "").strip()
            approved_answer = str(row.get("approved_answer") or "").strip()
            row_tenant = str(row.get("tenant_id") or tenant_id).strip() or "default"
            row_status = str(row.get("status") or status).strip() or status
            if row_status not in ALLOWED_STATUSES:
                raise ValueError(f"invalid status: {row_status}")
            normalized = normalize_question_for_exact_match(question)
            qa_id = str(row.get("qa_id") or "").strip() or _stable_qa_id(
                row_tenant, normalized, approved_answer
            )
            record = {
                "qa_id": qa_id,
                "question": question,
                "normalized_question": normalized,
                "approved_answer": approved_answer,
                "approved_citations": _normalize_citations(row),
                "tags": _parse_tags(row.get("tags")),
                "language": str(row.get("language") or "ja").strip() or "ja",
                "tenant_id": row_tenant,
                "doc_version": str(row.get("doc_version") or "").strip(),
                "status": row_status,
                "created_at": str(row.get("created_at") or timestamp).strip(),
                "notes": str(row.get("notes") or "").strip(),
            }
            records.append(record)
        except Exception as exc:
            conversion_errors.append(f"line {idx}: {exc}")
    return records, conversion_errors


def _intake_validation_errors(records: List[dict]) -> List[str]:
    errors = validate_approved_qa_records(records)
    seen: Dict[Tuple[str, str], str] = {}
    for idx, record in enumerate(records, start=1):
        status = str(record.get("status") or "").strip()
        if status not in ALLOWED_STATUSES:
            errors.append(f"line {idx}: invalid status: {status}")
        if not str(record.get("question") or "").strip():
            errors.append(f"line {idx}: missing question")
        if not str(record.get("approved_answer") or "").strip():
            errors.append(f"line {idx}: missing approved_answer")
        citations = record.get("approved_citations")
        if citations is not None and not isinstance(citations, list):
            errors.append(f"line {idx}: approved_citations must be a list")
        tenant_id = str(record.get("tenant_id") or "default").strip() or "default"
        normalized = str(record.get("normalized_question") or "").strip()
        if normalized:
            key = (tenant_id, normalized)
            previous = seen.get(key)
            if previous is not None and previous != str(record.get("qa_id") or ""):
                errors.append(
                    f"line {idx}: duplicate normalized_question for tenant_id={tenant_id}: {normalized}"
                )
            else:
                seen[key] = str(record.get("qa_id") or "")
    return errors


def _valid_record_indexes(records: List[dict], errors: List[str]) -> set[int]:
    bad: set[int] = set()
    for error in errors:
        if error.startswith("line "):
            try:
                line = int(error.split(":", 1)[0].split()[1])
                bad.add(line - 1)
            except Exception:
                continue
    return set(range(len(records))) - bad


def write_jsonl(path: str | Path, records: Iterable[dict]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def convert_file(
    *,
    input_path: str | Path,
    output_path: str | Path,
    fmt: str,
    tenant_id: str = "default",
    status: str = "draft",
    allow_errors: bool = False,
    summary_out: str | Path | None = None,
) -> dict:
    rows = read_input(input_path, fmt)
    records, conversion_errors = convert_records(rows, tenant_id=tenant_id, status=status)
    validation_errors = _intake_validation_errors(records)
    errors = conversion_errors + validation_errors

    if errors and not allow_errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)

    if errors and allow_errors:
        valid_indexes = _valid_record_indexes(records, validation_errors)
        records_to_write = [record for idx, record in enumerate(records) if idx in valid_indexes]
    else:
        records_to_write = records

    write_jsonl(output_path, records_to_write)
    normalized_counts = Counter(
        (record.get("tenant_id"), record.get("normalized_question")) for record in records
    )
    duplicate_count = sum(1 for count in normalized_counts.values() if count > 1)
    summary = {
        "input_count": len(rows),
        "written_count": len(records_to_write),
        "skipped_count": len(rows) - len(records_to_write),
        "duplicate_count": duplicate_count,
        "status_counts": dict(Counter(str(record.get("status") or "") for record in records_to_write)),
        "output_path": str(output_path),
        "errors": errors,
    }
    if summary_out is not None:
        Path(summary_out).parent.mkdir(parents=True, exist_ok=True)
        Path(summary_out).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "Summary: "
        f"input_count={summary['input_count']} "
        f"written_count={summary['written_count']} "
        f"skipped_count={summary['skipped_count']} "
        f"duplicate_count={summary['duplicate_count']} "
        f"status_counts={summary['status_counts']} "
        f"output_path={summary['output_path']}"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert curated Q&A into approved QA JSONL.")
    parser.add_argument("--in", dest="input_path", required=True)
    parser.add_argument("--out", dest="output_path", required=True)
    parser.add_argument("--format", choices=["csv", "json", "jsonl"], required=True)
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--status", default="draft", choices=sorted(ALLOWED_STATUSES))
    parser.add_argument("--allow-errors", action="store_true")
    parser.add_argument("--summary-out")
    args = parser.parse_args()

    convert_file(
        input_path=args.input_path,
        output_path=args.output_path,
        fmt=args.format,
        tenant_id=args.tenant_id,
        status=args.status,
        allow_errors=args.allow_errors,
        summary_out=args.summary_out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
