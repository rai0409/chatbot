from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from rag_core.approved_qa import validate_approved_qa_records
from rag_core.question_normalization import normalize_question_for_exact_match


ALLOWED_STATUSES = {"draft", "approved", "rejected"}
QUESTION_MARKERS = r"(?:Q|Ｑ|質問|問)\s*[:：]"
ANSWER_MARKERS = r"(?:A|Ａ|回答|答)\s*[:：]"
QUESTION_LIKE_TERMS = ("ですか", "ますか", "どうしたら", "何ですか", "方法", "手順")


@dataclass(frozen=True)
class Candidate:
    record: Dict[str, Any]
    extraction_method: str
    source_rank: Tuple[int, int, int, int]


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return value
    if raw[0] in "[{":
        try:
            return json.loads(raw)
        except Exception:
            return value
    return value


def parse_source_pages(value: Any) -> List[int]:
    value = parse_jsonish(value)
    if value in (None, "", []):
        return []
    raw_items = value if isinstance(value, list) else [value]
    if isinstance(value, str):
        raw_items = re.split(r"[,、\s]+", value)
    pages: List[int] = []
    for item in raw_items:
        if isinstance(item, bool):
            continue
        text = str(item).strip()
        if not text:
            continue
        try:
            pages.append(int(text))
        except Exception:
            continue
    return pages


def parse_section_path(value: Any) -> List[str]:
    value = parse_jsonish(value)
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [_compact(item) for item in value if _compact(item)]
    if isinstance(value, dict):
        return [_compact(item) for item in value.values() if _compact(item)]
    text = _compact(value)
    if not text:
        return []
    if ">" in text:
        return [_compact(part) for part in text.split(">") if _compact(part)]
    return [text]


def _text_for_answer(row: dict) -> str:
    for key in ("display_text", "text", "searchable_text"):
        text = _compact(row.get(key))
        if text:
            return text
    metadata = parse_jsonish(row.get("metadata"))
    if isinstance(metadata, dict):
        for key in ("display_text", "text", "searchable_text"):
            text = _compact(metadata.get(key))
            if text:
                return text
    return ""


def _source_doc(row: dict) -> str:
    metadata = parse_jsonish(row.get("metadata"))
    if isinstance(metadata, dict):
        value = row.get("source_doc") or metadata.get("source_doc") or metadata.get("doc")
    else:
        value = row.get("source_doc") or row.get("doc")
    return _compact(value)


def _source_pages(row: dict) -> List[int]:
    metadata = parse_jsonish(row.get("metadata"))
    candidates = [row.get("source_pages"), row.get("pages")]
    if isinstance(metadata, dict):
        candidates.extend([metadata.get("source_pages"), metadata.get("pages")])
    for value in candidates:
        pages = parse_source_pages(value)
        if pages:
            return pages
    return []


def _chunk_id(row: dict) -> str:
    return _compact(row.get("id") or row.get("chunk_id"))


def _title(row: dict) -> str:
    metadata = parse_jsonish(row.get("metadata"))
    if isinstance(metadata, dict):
        return _compact(row.get("title") or metadata.get("title"))
    return _compact(row.get("title"))


def _tenant_id(row: dict, default: str) -> str:
    return _compact(row.get("tenant_id")) or default


def _language(row: dict) -> str:
    return _compact(row.get("language")) or "ja"


def _doc_version(row: dict) -> str:
    return _compact(row.get("doc_version"))


def _question_like(text: str) -> bool:
    q = _compact(text)
    if not q:
        return False
    return q.endswith(("?", "？")) or any(term in q for term in QUESTION_LIKE_TERMS)


def _extract_explicit_qa(text: str) -> Tuple[str, str] | None:
    pattern = re.compile(
        rf"{QUESTION_MARKERS}\s*(?P<q>.+?)\s*{ANSWER_MARKERS}\s*(?P<a>.+)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None
    question = _compact(match.group("q"))
    answer = _compact(match.group("a"))
    return (question, answer) if question and answer else None


def _strip_duplicated_question(answer: str, question: str) -> str:
    out = _compact(answer)
    q = _compact(question)
    if not out or not q:
        return out
    variants = {
        q,
        q.rstrip("?？"),
        re.sub(r"[?？。!！]+$", "", q),
    }
    for variant in sorted(variants, key=len, reverse=True):
        if variant and out.startswith(variant):
            out = out[len(variant):].lstrip(" 　:：?？。-—ー")
            break
    return _compact(out)


def _procedure_question(section: str, style: str = "手順") -> str:
    core = re.sub(r"[?？。!！]+$", "", _compact(section))
    if style == "方法":
        return f"{core}の方法は？"
    return f"{core}の手順を教えてください"


def _stable_qa_id(
    *,
    tenant_id: str,
    normalized_question: str,
    source_doc: str,
    chunk_id: str,
    answer: str,
) -> str:
    answer_hash = hashlib.sha256(answer.encode("utf-8")).hexdigest()[:16]
    payload = f"{tenant_id}\n{normalized_question}\n{source_doc}\n{chunk_id}\n{answer_hash}".encode("utf-8")
    return "qa_" + hashlib.sha256(payload).hexdigest()[:16]


def _candidate_record(
    row: dict,
    *,
    question: str,
    answer: str,
    extraction_method: str,
    tenant_id: str,
    status: str,
    created_at: str,
) -> Dict[str, Any]:
    normalized = normalize_question_for_exact_match(question)
    source_doc = _source_doc(row)
    chunk_id = _chunk_id(row)
    title = _title(row)
    citation = {
        "source_doc": source_doc,
        "source_pages": _source_pages(row),
    }
    if chunk_id:
        citation["chunk_id"] = chunk_id
    if title:
        citation["title"] = title
    return {
        "qa_id": _stable_qa_id(
            tenant_id=tenant_id,
            normalized_question=normalized,
            source_doc=source_doc,
            chunk_id=chunk_id,
            answer=answer,
        ),
        "question": _compact(question),
        "normalized_question": normalized,
        "approved_answer": _compact(answer),
        "approved_citations": [citation],
        "tags": ["candidate", extraction_method],
        "language": _language(row),
        "tenant_id": tenant_id,
        "doc_version": _doc_version(row),
        "status": status,
        "created_at": created_at,
        "notes": "generated_from_canonical_jsonl",
    }


def _source_rank(row: dict, method: str, answer: str, max_answer_chars: int) -> Tuple[int, int, int, int]:
    method_rank = {"faq": 0, "section_path": 1, "procedure": 2}.get(method, 9)
    role_rank = 0 if _compact(row.get("chunk_role")).lower() == "parent" else 1
    source_rank = 0 if _source_doc(row) and _source_pages(row) else 1
    answer_len = len(answer)
    length_rank = 0 if 10 <= answer_len <= max_answer_chars else 1
    return (method_rank, role_rank, source_rank, length_rank)


def _candidate_from_pair(
    row: dict,
    *,
    question: str,
    answer: str,
    extraction_method: str,
    tenant_id: str,
    status: str,
    created_at: str,
    min_answer_chars: int,
    max_question_chars: int,
    max_answer_chars: int,
    allow_empty_answer: bool,
) -> Candidate | None:
    question = _compact(question)
    answer = _compact(answer)
    if not question or len(question) > max_question_chars:
        return None
    if not answer and not allow_empty_answer:
        return None
    if len(answer) < min_answer_chars and not allow_empty_answer:
        return None
    if len(answer) > max_answer_chars:
        answer = answer[:max_answer_chars].rstrip()
    record = _candidate_record(
        row,
        question=question,
        answer=answer,
        extraction_method=extraction_method,
        tenant_id=tenant_id,
        status=status,
        created_at=created_at,
    )
    return Candidate(
        record=record,
        extraction_method=extraction_method,
        source_rank=_source_rank(row, extraction_method, answer, max_answer_chars),
    )


def extract_candidates_from_row(
    row: dict,
    *,
    tenant_id: str,
    status: str,
    created_at: str,
    min_answer_chars: int,
    max_question_chars: int,
    max_answer_chars: int,
    include_procedure_candidates: bool,
    allow_empty_answer: bool,
) -> List[Candidate]:
    text = _text_for_answer(row)
    if not text:
        return []
    candidates: List[Candidate] = []
    explicit = _extract_explicit_qa(text)
    if explicit:
        candidate = _candidate_from_pair(
            row,
            question=explicit[0],
            answer=explicit[1],
            extraction_method="faq",
            tenant_id=tenant_id,
            status=status,
            created_at=created_at,
            min_answer_chars=min_answer_chars,
            max_question_chars=max_question_chars,
            max_answer_chars=max_answer_chars,
            allow_empty_answer=allow_empty_answer,
        )
        if candidate:
            candidates.append(candidate)

    section_path = parse_section_path(row.get("section_path"))
    question = section_path[-1] if section_path else ""
    if question and _question_like(question):
        answer = _strip_duplicated_question(text, question)
        candidate = _candidate_from_pair(
            row,
            question=question,
            answer=answer,
            extraction_method="section_path",
            tenant_id=tenant_id,
            status=status,
            created_at=created_at,
            min_answer_chars=min_answer_chars,
            max_question_chars=max_question_chars,
            max_answer_chars=max_answer_chars,
            allow_empty_answer=allow_empty_answer,
        )
        if candidate:
            candidates.append(candidate)

    if include_procedure_candidates and section_path:
        section = section_path[-1]
        if section and not _question_like(section):
            candidate = _candidate_from_pair(
                row,
                question=_procedure_question(section),
                answer=text,
                extraction_method="procedure",
                tenant_id=tenant_id,
                status=status,
                created_at=created_at,
                min_answer_chars=min_answer_chars,
                max_question_chars=max_question_chars,
                max_answer_chars=max_answer_chars,
                allow_empty_answer=allow_empty_answer,
            )
            if candidate:
                candidates.append(candidate)
    return candidates


def _read_jsonl(path: str | Path) -> List[dict]:
    rows: List[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if raw:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    rows.append(obj)
    return rows


def _dedupe(candidates: Iterable[Candidate]) -> Tuple[List[Candidate], int]:
    best: Dict[Tuple[str, str], Candidate] = {}
    duplicate_count = 0
    for candidate in candidates:
        record = candidate.record
        key = (record["tenant_id"], record["normalized_question"])
        current = best.get(key)
        if current is None:
            best[key] = candidate
            continue
        duplicate_count += 1
        if candidate.source_rank < current.source_rank:
            best[key] = candidate
    return list(best.values()), duplicate_count


def _validate_candidates(records: List[dict]) -> List[str]:
    errors = validate_approved_qa_records(records)
    seen: set[Tuple[str, str]] = set()
    for idx, record in enumerate(records, start=1):
        for key in ("qa_id", "question", "normalized_question", "approved_answer"):
            if not _compact(record.get(key)):
                errors.append(f"line {idx}: missing {key}")
        if not isinstance(record.get("approved_citations"), list) or not record.get("approved_citations"):
            errors.append(f"line {idx}: missing approved_citations")
        key = (_compact(record.get("tenant_id")) or "default", _compact(record.get("normalized_question")))
        if key in seen:
            errors.append(f"line {idx}: duplicate normalized_question for tenant")
        seen.add(key)
    return errors


def write_jsonl(path: str | Path, records: Iterable[dict]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def convert_canonical_jsonl(
    *,
    input_path: str | Path,
    output_path: str | Path,
    tenant_id: str = "default",
    status: str = "draft",
    max_question_chars: int = 300,
    max_answer_chars: int = 3000,
    min_answer_chars: int = 10,
    limit: int | None = None,
    include_procedure_candidates: bool = False,
    allow_empty_answer: bool = False,
    summary_out: str | Path | None = None,
) -> dict:
    if status not in {"draft", "approved", "rejected"}:
        raise ValueError(f"invalid status: {status}")
    rows = _read_jsonl(input_path)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    raw_candidates: List[Candidate] = []
    rows_with_candidates = 0
    for row in rows:
        row_tenant = _compact(row.get("tenant_id")) or tenant_id
        row_candidates = extract_candidates_from_row(
            row,
            tenant_id=row_tenant,
            status=status,
            created_at=created_at,
            min_answer_chars=min_answer_chars,
            max_question_chars=max_question_chars,
            max_answer_chars=max_answer_chars,
            include_procedure_candidates=include_procedure_candidates,
            allow_empty_answer=allow_empty_answer,
        )
        if row_candidates:
            rows_with_candidates += 1
        raw_candidates.extend(row_candidates)

    deduped, duplicate_count = _dedupe(raw_candidates)
    if limit is not None:
        deduped = deduped[: max(0, int(limit))]
    records = [candidate.record for candidate in deduped]
    errors = _validate_candidates(records)
    if errors:
        raise ValueError("invalid generated approved QA candidates: " + "; ".join(errors))
    write_jsonl(output_path, records)

    method_counts = Counter(candidate.extraction_method for candidate in deduped)
    status_counts = Counter(record.get("status") for record in records)
    summary = {
        "input_count": len(rows),
        "candidate_count": len(raw_candidates),
        "written_count": len(records),
        "skipped_count": len(rows) - rows_with_candidates,
        "duplicate_count": duplicate_count,
        "extraction_method_counts": dict(method_counts),
        "status_counts": dict(status_counts),
        "output_path": str(output_path),
    }
    if summary_out is not None:
        Path(summary_out).parent.mkdir(parents=True, exist_ok=True)
        Path(summary_out).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "Summary: "
        f"input_count={summary['input_count']} "
        f"candidate_count={summary['candidate_count']} "
        f"written_count={summary['written_count']} "
        f"skipped_count={summary['skipped_count']} "
        f"duplicate_count={summary['duplicate_count']} "
        f"extraction_method_counts={summary['extraction_method_counts']} "
        f"status_counts={summary['status_counts']} "
        f"output_path={summary['output_path']}"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate draft approved-QA candidates from canonical JSONL.")
    parser.add_argument("--in", dest="input_path", required=True)
    parser.add_argument("--out", dest="output_path", required=True)
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--status", default="draft", choices=["draft", "approved", "rejected"])
    parser.add_argument("--max-question-chars", type=int, default=300)
    parser.add_argument("--max-answer-chars", type=int, default=3000)
    parser.add_argument("--min-answer-chars", type=int, default=10)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--include-procedure-candidates", action="store_true")
    parser.add_argument("--allow-empty-answer", action="store_true")
    parser.add_argument("--summary-out")
    args = parser.parse_args()
    convert_canonical_jsonl(
        input_path=args.input_path,
        output_path=args.output_path,
        tenant_id=args.tenant_id,
        status=args.status,
        max_question_chars=args.max_question_chars,
        max_answer_chars=args.max_answer_chars,
        min_answer_chars=args.min_answer_chars,
        limit=args.limit,
        include_procedure_candidates=args.include_procedure_candidates,
        allow_empty_answer=args.allow_empty_answer,
        summary_out=args.summary_out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
