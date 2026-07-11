from __future__ import annotations

import json
import re
from dataclasses import replace
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from rag_core.question_normalization import normalize_question_for_exact_match
from rag_core.source_metadata import normalize_citation, normalize_source_pages


@dataclass(frozen=True)
class ApprovedCitation:
    source_doc: str
    source_pages: Tuple[int, ...] = field(default_factory=tuple)
    chunk_id: str | None = None
    title: str | None = None
    source_id: str | None = None
    source_title: str | None = None
    source_type: str | None = None
    version: str | None = None
    status: str | None = None
    updated_at: str | None = None
    tenant_id: str | None = None


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
    approved_aliases: Tuple[str, ...] = field(default_factory=tuple)
    match_type: str = "canonical"
    matched_alias: str | None = None


@dataclass(frozen=True)
class ApprovedQAIndex:
    records: Tuple[ApprovedAnswer, ...]
    by_tenant_question: Dict[Tuple[str, str], ApprovedAnswer]
    by_tenant_alias: Dict[Tuple[str, str], Tuple[ApprovedAnswer, str]] = field(default_factory=dict)


MAX_APPROVED_ALIASES = 20
MAX_APPROVED_ALIAS_CHARS = 500
_ALIAS_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _as_str(value: Any) -> str:
    return str(value or "").strip()


def _normalize_pages(value: Any) -> Tuple[int, ...]:
    return tuple(normalize_source_pages(value))


def _normalize_citation(raw: Any) -> ApprovedCitation | None:
    if not isinstance(raw, dict):
        return None
    normalized = normalize_citation(raw)
    source_doc = _as_str(normalized.get("source_doc"))
    if not source_doc:
        return None
    return ApprovedCitation(
        source_doc=source_doc,
        source_pages=_normalize_pages(normalized.get("source_pages")),
        chunk_id=_as_str(normalized.get("chunk_id")) or None,
        title=_as_str(normalized.get("title")) or None,
        source_id=_as_str(normalized.get("source_id")) or None,
        source_title=_as_str(normalized.get("source_title")) or None,
        source_type=_as_str(normalized.get("source_type")) or None,
        version=_as_str(normalized.get("version")) or None,
        status=_as_str(normalized.get("status")) or None,
        updated_at=_as_str(normalized.get("updated_at")) or None,
        tenant_id=_as_str(normalized.get("tenant_id")) or None,
    )


def _record_normalized_question(record: dict) -> str:
    value = _as_str(record.get("normalized_question"))
    if value:
        return normalize_question_for_exact_match(value)
    return normalize_question_for_exact_match(_as_str(record.get("question")))


def validate_approved_qa_records(records: list[dict]) -> list[str]:
    errors: List[str] = []
    seen: Dict[Tuple[str, str], str] = {}
    canonical_owners: Dict[Tuple[str, str], Tuple[str, str, int]] = {}
    alias_owners: Dict[Tuple[str, str], Tuple[str, str, int]] = {}
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
        normalized = _record_normalized_question(record)
        if normalized:
            canonical_owners[(tenant_id, normalized)] = (qa_id, answer, idx)

        raw_aliases = record.get("approved_aliases")
        if raw_aliases is not None:
            if not isinstance(raw_aliases, list):
                errors.append(f"line {idx}: approved_aliases must be a list")
            else:
                if status != "approved" and raw_aliases:
                    errors.append(f"line {idx}: approved_aliases are only allowed on approved records")
                if len(raw_aliases) > MAX_APPROVED_ALIASES:
                    errors.append(
                        f"line {idx}: approved_aliases exceeds maximum count {MAX_APPROVED_ALIASES}"
                    )
                local_aliases: Dict[str, str] = {}
                for aidx, alias in enumerate(raw_aliases, start=1):
                    if not isinstance(alias, str):
                        errors.append(f"line {idx}: approved_aliases[{aidx}] must be a string")
                        continue
                    if not alias.strip():
                        errors.append(f"line {idx}: approved_aliases[{aidx}] must not be empty")
                        continue
                    if len(alias) > MAX_APPROVED_ALIAS_CHARS:
                        errors.append(
                            f"line {idx}: approved_aliases[{aidx}] exceeds {MAX_APPROVED_ALIAS_CHARS} characters"
                        )
                    if _ALIAS_CONTROL_RE.search(alias):
                        errors.append(f"line {idx}: approved_aliases[{aidx}] contains a control character")
                    alias_normalized = normalize_question_for_exact_match(alias)
                    if not alias_normalized:
                        errors.append(f"line {idx}: approved_aliases[{aidx}] normalizes to empty")
                        continue
                    if alias_normalized == normalized:
                        errors.append(
                            f"line {idx}: approved_aliases[{aidx}] duplicates the canonical question after normalization"
                        )
                    if alias_normalized in local_aliases:
                        errors.append(
                            f"line {idx}: duplicate approved_aliases after normalization: {alias_normalized}"
                        )
                    else:
                        local_aliases[alias_normalized] = alias.strip()
                    key = (tenant_id, alias_normalized)
                    previous = alias_owners.get(key)
                    if previous is not None and previous[0] != qa_id:
                        errors.append(
                            f"line {idx}: approved alias conflicts with qa_id={previous[0]} "
                            f"for tenant_id={tenant_id}: {alias_normalized}"
                        )
                    else:
                        alias_owners[key] = (qa_id, answer, idx)

        if status != "approved":
            continue

        citations = record.get("approved_citations")
        if not isinstance(citations, list) or not citations:
            errors.append(f"line {idx}: approved record must include approved_citations")
        else:
            for cidx, citation in enumerate(citations, start=1):
                if _normalize_citation(citation) is None:
                    errors.append(f"line {idx}: invalid approved_citations[{cidx}]")

        if not normalized:
            errors.append(f"line {idx}: missing normalized_question")
            continue
        key = (tenant_id, normalized)
        previous_qa_id = seen.get(key)
        if previous_qa_id is not None:
            previous_answer = next(
                (_as_str(item.get("approved_answer")) for item in records
                 if isinstance(item, dict) and _as_str(item.get("qa_id")) == previous_qa_id),
                "",
            )
            if previous_qa_id != qa_id:
                errors.append(
                    f"line {idx}: duplicate normalized_question for tenant_id={tenant_id}: {normalized}"
                )
            elif previous_answer != answer:
                errors.append(
                    f"line {idx}: answer conflict for qa_id={qa_id} tenant_id={tenant_id}: {normalized}"
                )
        else:
            seen[key] = qa_id

    for key, (alias_qa_id, alias_answer, alias_line) in alias_owners.items():
        canonical = canonical_owners.get(key)
        if canonical is not None and canonical[0] != alias_qa_id:
            errors.append(
                f"line {alias_line}: approved alias conflicts with canonical question "
                f"qa_id={canonical[0]} for tenant_id={key[0]}: {key[1]}"
            )
            if canonical[1] != alias_answer:
                errors.append(
                    f"line {alias_line}: answer conflict between approved alias qa_id={alias_qa_id} "
                    f"and canonical qa_id={canonical[0]} for tenant_id={key[0]}"
                )
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
        approved_aliases=tuple(
            alias.strip() for alias in (record.get("approved_aliases") or [])
            if isinstance(alias, str) and alias.strip()
        ),
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
    by_alias: Dict[Tuple[str, str], Tuple[ApprovedAnswer, str]] = {}
    for record in records:
        answer = _to_answer(record)
        if answer is None or answer.tenant_id != tenant_id:
            continue
        key = (answer.tenant_id, answer.normalized_question)
        if key not in by_key:
            by_key[key] = answer
            answers.append(answer)
            for alias in answer.approved_aliases:
                alias_key = (answer.tenant_id, normalize_question_for_exact_match(alias))
                by_alias[alias_key] = (answer, alias)
    return ApprovedQAIndex(records=tuple(answers), by_tenant_question=by_key, by_tenant_alias=by_alias)


def lookup_approved_answer(
    index: ApprovedQAIndex,
    question: str,
    tenant_id: str = "default",
) -> ApprovedAnswer | None:
    normalized = normalize_question_for_exact_match(question)
    canonical = index.by_tenant_question.get((tenant_id, normalized))
    if canonical is not None:
        return canonical
    alias_match = index.by_tenant_alias.get((tenant_id, normalized))
    if alias_match is None:
        return None
    answer, matched_alias = alias_match
    return replace(answer, match_type="alias", matched_alias=matched_alias)
