from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from rag_core.question_normalization import normalize_question_for_exact_match


@dataclass(frozen=True)
class ApprovedCitation:
    source_doc: str
    source_pages: Tuple[int, ...] = field(default_factory=tuple)
    chunk_id: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class ApprovedAnswer:
    qa_id: str
    question: str
    normalized_question: str
    approved_answer: str
    approved_citations: Tuple[ApprovedCitation, ...]
    tenant_id: str
    language: str
    doc_version: str | None = None
    tags: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ApprovedQAIndex:
    records: Tuple[ApprovedAnswer, ...]
    by_tenant_question: Dict[Tuple[str, str], ApprovedAnswer]


def _as_str(value: Any) -> str:
    return str(value or "").strip()


def _normalize_pages(value: Any) -> Tuple[int, ...]:
    if value is None:
        return tuple()
    raw = value if isinstance(value, list) else [value]
    pages: List[int] = []
    for item in raw:
        try:
            pages.append(int(item))
        except Exception:
            continue
    return tuple(pages)


def _normalize_citation(raw: Any) -> ApprovedCitation | None:
    if not isinstance(raw, dict):
        return None
    source_doc = _as_str(raw.get("source_doc"))
    if not source_doc:
        return None
    return ApprovedCitation(
        source_doc=source_doc,
        source_pages=_normalize_pages(raw.get("source_pages")),
        chunk_id=_as_str(raw.get("chunk_id")) or None,
        title=_as_str(raw.get("title")) or None,
    )


def _record_normalized_question(record: dict) -> str:
    value = _as_str(record.get("normalized_question"))
    if value:
        return normalize_question_for_exact_match(value)
    return normalize_question_for_exact_match(_as_str(record.get("question")))


def validate_approved_qa_records(records: list[dict]) -> list[str]:
    errors: List[str] = []
    seen: Dict[Tuple[str, str], str] = {}
    for idx, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            errors.append(f"line {idx}: record must be an object")
            continue

        qa_id = _as_str(record.get("qa_id"))
        question = _as_str(record.get("question"))
        answer = _as_str(record.get("approved_answer"))
        tenant_id = _as_str(record.get("tenant_id")) or "default"
        status = _as_str(record.get("status"))

        if not qa_id:
            errors.append(f"line {idx}: missing qa_id")
        if not question:
            errors.append(f"line {idx}: missing question")
        if not answer:
            errors.append(f"line {idx}: missing approved_answer")
        if status != "approved":
            continue

        citations = record.get("approved_citations")
        if not isinstance(citations, list) or not citations:
            errors.append(f"line {idx}: approved record must include approved_citations")
        else:
            for cidx, citation in enumerate(citations, start=1):
                if _normalize_citation(citation) is None:
                    errors.append(f"line {idx}: invalid approved_citations[{cidx}]")

        normalized = _record_normalized_question(record)
        if not normalized:
            errors.append(f"line {idx}: missing normalized_question")
            continue
        key = (tenant_id, normalized)
        previous_qa_id = seen.get(key)
        if previous_qa_id is not None and previous_qa_id != qa_id:
            errors.append(
                f"line {idx}: duplicate normalized_question for tenant_id={tenant_id}: {normalized}"
            )
        else:
            seen[key] = qa_id
    return errors


def _to_answer(record: dict) -> ApprovedAnswer | None:
    if _as_str(record.get("status")) != "approved":
        return None
    citations = tuple(
        citation
        for citation in (_normalize_citation(raw) for raw in record.get("approved_citations") or [])
        if citation is not None
    )
    if not citations:
        return None
    return ApprovedAnswer(
        qa_id=_as_str(record.get("qa_id")),
        question=_as_str(record.get("question")),
        normalized_question=_record_normalized_question(record),
        approved_answer=_as_str(record.get("approved_answer")),
        approved_citations=citations,
        tenant_id=_as_str(record.get("tenant_id")) or "default",
        language=_as_str(record.get("language")) or "ja",
        doc_version=_as_str(record.get("doc_version")) or None,
        tags=tuple(str(x).strip() for x in (record.get("tags") or []) if str(x).strip()),
    )


def _read_jsonl(path: Path) -> List[dict]:
    records: List[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            records.append(json.loads(raw))
    return records


def load_approved_qa(path: str | Path, tenant_id: str = "default") -> ApprovedQAIndex:
    records = _read_jsonl(Path(path))
    errors = validate_approved_qa_records(records)
    if errors:
        raise ValueError("invalid approved QA records: " + "; ".join(errors))

    answers: List[ApprovedAnswer] = []
    by_key: Dict[Tuple[str, str], ApprovedAnswer] = {}
    for record in records:
        answer = _to_answer(record)
        if answer is None or answer.tenant_id != tenant_id:
            continue
        key = (answer.tenant_id, answer.normalized_question)
        if key not in by_key:
            by_key[key] = answer
            answers.append(answer)
    return ApprovedQAIndex(records=tuple(answers), by_tenant_question=by_key)


def lookup_approved_answer(
    index: ApprovedQAIndex,
    question: str,
    tenant_id: str = "default",
) -> ApprovedAnswer | None:
    normalized = normalize_question_for_exact_match(question)
    return index.by_tenant_question.get((tenant_id, normalized))
