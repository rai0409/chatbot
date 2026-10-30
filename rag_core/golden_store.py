"""The golden questions of a tenant, kept and edited in the console (S4).

A golden question says what a good answer contains (see golden_qa.py). They are written by people, or made from a record that a
person judged (records.py), and they are what an evaluation runs against. One JSON-lines file per tenant
(GOLDEN_QA_DIR). A case is kept exactly as golden_qa.parse_case reads it, plus who wrote it and when; `export_cases` gives the
rows an evaluation takes (the "golden_qa" rows of a profile). Deleting moves the case to a file of deleted cases, so it can be
brought back by hand.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import config
from rag_core import audit_log, golden_qa

_TENANT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_META = ("created_by", "created_at", "updated_by", "updated_at", "origin")
_EDITABLE = ("question", "expected_all", "expected_any", "forbidden_any", "expected_question_type", "group", "origin_change",
             "reference", "tags", "expected_citation", "enabled", "priority")


class GoldenError(ValueError):
    """The case is not valid, or it does not exist, or someone else changed it."""


def golden_dir() -> Path:
    configured = str(getattr(config, "GOLDEN_QA_DIR", "") or "").strip()
    return Path(configured) if configured else Path(config.BASE_DIR) / "data" / "golden_qa"


def _path(tenant_id: str, *, deleted: bool = False) -> Path:
    if not _TENANT_RE.fullmatch(str(tenant_id or "")):
        raise GoldenError("the tenant id is not valid")
    return golden_dir() / f"{tenant_id}{'.deleted' if deleted else ''}.jsonl"


def _load(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _save(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, path)


def _now(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def _checked(row: Mapping[str, Any]) -> Dict[str, Any]:
    case, errors = golden_qa.parse_case(dict(row))
    if case is None:
        raise GoldenError("; ".join(errors))
    return case


def list_entries(tenant_id: str, *, include_disabled: bool = True) -> List[Dict[str, Any]]:
    rows = _load(_path(tenant_id))
    rows = [r for r in rows if include_disabled or r.get("enabled", True)]
    return sorted(rows, key=lambda r: (-int(r.get("priority") or 0), str(r.get("case_id"))))


def get(tenant_id: str, case_id: str) -> Dict[str, Any]:
    for row in _load(_path(tenant_id)):
        if row.get("case_id") == case_id:
            return row
    raise GoldenError(f"case '{case_id}' not found")


def create(tenant_id: str, row: Mapping[str, Any], *, by: str, origin: str = "manual", now: Optional[datetime] = None) -> Dict[str, Any]:
    """Add a case. Without a case_id one is made. The case must check something (golden_qa.parse_case decides)."""
    data = {k: row.get(k) for k in ("case_id", *_EDITABLE) if k in row}
    data["case_id"] = str(data.get("case_id") or "gq-" + uuid.uuid4().hex[:10])
    case = _checked(data)
    path = _path(tenant_id)
    with audit_log._FileLock(path):
        rows = _load(path)
        if any(r.get("case_id") == case["case_id"] for r in rows):
            raise GoldenError(f"case '{case['case_id']}' already exists")
        stamp = _now(now)
        case.update(created_by=str(by)[:100], created_at=stamp, updated_by=str(by)[:100], updated_at=stamp, origin=origin)
        rows.append(case)
        _save(path, rows)
    return case


def update(tenant_id: str, case_id: str, changes: Mapping[str, Any], *, by: str, expected_updated_at: Optional[str] = None,
           now: Optional[datetime] = None) -> Dict[str, Any]:
    path = _path(tenant_id)
    with audit_log._FileLock(path):
        rows = _load(path)
        index = next((i for i, r in enumerate(rows) if r.get("case_id") == case_id), None)
        if index is None:
            raise GoldenError(f"case '{case_id}' not found")
        current = rows[index]
        if expected_updated_at is not None and current.get("updated_at") != expected_updated_at:
            raise GoldenError("the case was changed by someone else; reload it")
        merged = {**{k: current.get(k) for k in ("case_id", *_EDITABLE)}, **{k: changes[k] for k in _EDITABLE if k in changes}}
        case = _checked(merged)
        case.update({k: current.get(k) for k in ("created_by", "created_at", "origin")}, updated_by=str(by)[:100], updated_at=_now(now))
        rows[index] = case
        _save(path, rows)
    return case


def delete(tenant_id: str, case_id: str, *, by: str, now: Optional[datetime] = None) -> None:
    """Move the case to the tenant's file of deleted cases."""
    path = _path(tenant_id)
    with audit_log._FileLock(path):
        rows = _load(path)
        gone = next((r for r in rows if r.get("case_id") == case_id), None)
        if gone is None:
            raise GoldenError(f"case '{case_id}' not found")
        _save(path, [r for r in rows if r is not gone])
        trash = _path(tenant_id, deleted=True)
        _save(trash, _load(trash) + [{**gone, "deleted_by": str(by)[:100], "deleted_at": _now(now)}])


def promote_record(tenant_id: str, record: Mapping[str, Any], *, by: str, case_id: Optional[str] = None,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """Make a golden question from a record a person judged. A 'correct' answer becomes the reference; for a wrong or partial
    one the reviewer's expected answer is the reference (it is required). The case can be refined afterwards."""
    review = record.get("review") or {}
    if not review:
        raise GoldenError("only a record that a person has judged can become a golden question")
    question = str(record.get("question") or "").strip()
    if not question:
        raise GoldenError("the record has no question text (its level kept none, or it was anonymized)")
    reference = str(review.get("expected_answer") or "").strip()
    if not reference and review.get("verdict") == "correct":
        reference = str(record.get("answer_text") or "").strip()
    if not reference:
        raise GoldenError("the judgement needs the expected answer to make a golden question")
    row = {"case_id": case_id, "question": question, "reference": reference, "tags": ["from-record"],
           "expected_citation": bool(record.get("citations_count")), "expected_question_type": str(record.get("question_type") or "")}
    return create(tenant_id, {k: v for k, v in row.items() if v not in (None, "")}, by=by, origin=f"record:{record.get('id')}", now=now)


def export_cases(tenant_id: str) -> List[Dict[str, Any]]:
    """The enabled cases as an evaluation takes them: the 'golden_qa' rows of a profile (metadata left out)."""
    keep = ("case_id", "question", "expected_all", "expected_any", "forbidden_any", "expected_question_type", "group",
            "origin_change", "reference", "tags", "expected_citation", "enabled", "priority")
    return [{k: row.get(k) for k in keep if k in row} for row in list_entries(tenant_id, include_disabled=False)]
