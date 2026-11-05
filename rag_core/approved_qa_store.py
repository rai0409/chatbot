"""Managing the approved-QA file from the console (create, edit, approve, reject, delete, import, export).

The live file is the JSONL that chat reads (APPROVED_QA_PATH; rag_core/approved_qa.py). This module is the only
writer the console uses, and it keeps the rules that make the file trustworthy:

- a record is a draft, approved or rejected; chat serves approved records only
- approving needs the question, the answer and at least one valid citation (a source document and pages)
- editing the question, answer, citations or aliases of an approved record takes it back to draft: a changed
  answer is not the answer that was approved. Tags and notes do not
- aliases (other wordings of the question) live in "proposed_aliases" until the record is approved, because the file
  format allows approved_aliases on approved records only
- two approved records of one tenant cannot answer the same question (the checks of validate_approved_qa_records)
- APPROVED_QA_REQUIRE_APPROVER=true: a different person approves (the last editor cannot approve their own record)
- every change needs a reason and a name, keeps the record as it was and as it became (a history file next to the
  live file), and the previous version of the whole file is kept (<file>.prev)
- one writer at a time (file lock shared by every worker process), the file is replaced atomically, and a file with
  lines that cannot be read is never overwritten (the lines would be lost)
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import config
from rag_core import approvals, audit_log
from rag_core.approved_qa import MAX_APPROVED_ALIASES, _normalize_citation, validate_approved_qa_records
from rag_core.question_normalization import normalize_question_for_exact_match

STATUSES = ("draft", "approved", "rejected")
MAX_QUESTION_CHARS = 500
MAX_ANSWER_CHARS = 5000
MAX_ALIAS_CHARS = 500
MAX_TAGS = 20
MAX_CITATIONS = 10
MAX_IMPORT_ROWS = 2000
MAX_IMPORT_BYTES = 5 * 1024 * 1024
_TENANT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_QA_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SOURCE_RE = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f][^\\:*?\"<>|\x00-\x1f]*$")  # a relative document name, no drive or URL
# Content that makes an approved answer a different answer: changing it needs a new approval.
_CONTENT_FIELDS = ("question", "approved_answer", "approved_citations", "approved_aliases", "proposed_aliases")

TEMPLATE_HEADERS = ["質問", "正解回答", "出典文書", "出典ページ", "出典タイトル", "分類", "テナント", "文書版", "有効", "言い換え",
                    "除外質問", "必須キーワード", "備考"]
_TEMPLATE_EXAMPLE = ["架空サービスの申請期限はいつですか？", "架空サービスの申請期限は毎月末日です。", "sample_manual.pdf", "2", "架空サービス利用案内",
                     "手続き", "default", "v1", "有効", "申請はいつまでですか？", "", "", "この行は架空の入力例です。"]


class StoreError(ValueError):
    """The change is not valid (the message says why)."""


class Conflict(StoreError):
    """The record was changed by someone else since it was read."""


def path() -> Path:
    return Path(config.APPROVED_QA_PATH)


def _history_path() -> Path:
    return path().with_name(path().name + ".history.jsonl")


def path_is_default() -> bool:
    """APPROVED_QA_PATH was not set: it still points to the sample path inside eval/cases of the code tree."""
    parts = Path(os.path.normpath(str(path()))).parts
    return parts[-3:] == ("eval", "cases", "approved_qa_sample.jsonl")


def require_approver() -> bool:
    return str(os.getenv("APPROVED_QA_REQUIRE_APPROVER", "")).strip().lower() in {"1", "true", "yes", "on"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identity(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        name, ident = str(value.get("name") or "").strip(), value.get("id") or None
        verified = bool(value.get("verified") and ident)
    else:
        name, ident, verified = str(value or "").strip(), None, False
    if not name or len(name) > 64:
        raise StoreError("the name of the person who does this (1 to 64 characters) is required")
    return {"name": name, "id": ident, "verified": verified}


def _reason(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 300:
        raise StoreError("a reason (1 to 300 characters) is required")
    return text


def _tenant(value: Any) -> str:
    text = str(value or "").strip() or "default"
    if not _TENANT_RE.fullmatch(text):
        raise StoreError("the tenant id is not valid")
    return text


# --- reading and writing the file -----------------------------------------------------------------------------------


def _read() -> Tuple[List[dict], int]:
    """(records, number of lines that could not be read). A missing file is an empty store."""
    target = path()
    records: List[dict] = []
    bad = 0
    try:
        handle = target.open("r", encoding="utf-8")
    except FileNotFoundError:
        return [], 0
    with handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(obj, dict):
                records.append(obj)
            else:
                bad += 1
    return records, bad


def _write(records: List[dict]) -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records), encoding="utf-8")
    if target.exists():
        shutil.copy2(target, target.with_name(target.name + ".prev"))  # one step back for a bad bulk change
    os.replace(tmp, target)


def _log(entries: Iterable[Dict[str, Any]]) -> None:
    history = _history_path()
    with history.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


class _Locked:
    """Read-modify-write under the lock; refuses a file with unreadable lines (they would be dropped)."""

    def __enter__(self):
        if path_is_default():
            raise StoreError("APPROVED_QA_PATH is not set: set it to a data file outside the code tree (for example "
                             "data/approved_qa/live.jsonl) before the console writes approved QA")
        path().parent.mkdir(parents=True, exist_ok=True)
        self._lock = audit_log._FileLock(path())
        self._lock.__enter__()
        self.records, bad = _read()
        if bad:
            self._lock.__exit__(None, None, None)
            raise StoreError(f"the approved-QA file has {bad} unreadable line(s): fix or restore the file first (nothing was changed)")
        return self

    def __exit__(self, *exc):
        self._lock.__exit__(*exc)
        return False


def _errors(records: List[dict]) -> Set[str]:
    return set(validate_approved_qa_records(records))


def _check_no_new_errors(before: List[dict], after: List[dict]) -> None:
    new = sorted(_errors(after) - _errors(before))
    if new:
        raise StoreError(re.sub(r"^line \d+: ", "", new[0]))


# --- input -----------------------------------------------------------------------------------------------------------


def _text_list(values: Any, *, limit: int, chars: int, what: str) -> List[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise StoreError(f"{what} must be a list")
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if len(text) > chars or _CONTROL_RE.search(text):
            raise StoreError(f"{what}: at most {chars} characters, no control characters")
        if text not in out:
            out.append(text)
    if len(out) > limit:
        raise StoreError(f"{what}: at most {limit}")
    return out


def _citation(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise StoreError("a citation is an object")
    source = str(raw.get("source_doc") or "").strip()
    if (not source or not _SOURCE_RE.fullmatch(source) or source.startswith(("/", ".")) or ".." in re.split(r"[\\/]", source)
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", source)):
        raise StoreError("a citation needs a relative source document name (no path to the server, no '..', no URL)")
    pages = raw.get("source_pages") or []
    if not isinstance(pages, list) or any(isinstance(p, bool) or not isinstance(p, int) or p < 1 or p > 100000 for p in pages):
        raise StoreError("citation pages are whole numbers from 1")
    out: Dict[str, Any] = {"source_doc": source, "source_pages": list(pages)}
    for key in ("title", "chunk_id", "version"):
        value = str(raw.get(key) or "").strip()
        if value:
            if len(value) > 300 or _CONTROL_RE.search(value):
                raise StoreError(f"citation {key}: at most 300 characters")
            out[key] = value
    return out


def _clean(payload: Dict[str, Any]) -> Dict[str, Any]:
    question = unicodedata.normalize("NFC", str(payload.get("question") or "").strip())
    answer = unicodedata.normalize("NFC", str(payload.get("approved_answer") or "").strip())
    if not question or not answer:
        raise StoreError("the question and the answer are required")
    if len(question) > MAX_QUESTION_CHARS or len(answer) > MAX_ANSWER_CHARS:
        raise StoreError(f"the question is at most {MAX_QUESTION_CHARS} characters and the answer at most {MAX_ANSWER_CHARS}")
    if _CONTROL_RE.search(question) or _CONTROL_RE.search(answer):
        raise StoreError("control characters are not allowed")
    citations = payload.get("citations") if payload.get("citations") is not None else payload.get("approved_citations")
    if citations is not None and (not isinstance(citations, list) or len(citations) > MAX_CITATIONS):
        raise StoreError(f"citations: a list of at most {MAX_CITATIONS}")
    aliases = payload.get("aliases") if payload.get("aliases") is not None else payload.get("approved_aliases")
    return {
        "question": question,
        "approved_answer": answer,
        "citations": [_citation(c) for c in (citations or [])],
        "aliases": _text_list(aliases, limit=MAX_APPROVED_ALIASES, chars=MAX_ALIAS_CHARS, what="aliases"),
        "tags": _text_list(payload.get("tags"), limit=MAX_TAGS, chars=100, what="tags"),
        "doc_version": str(payload.get("doc_version") or "").strip()[:100] or None,
    }


def _summary(record: dict) -> Dict[str, Any]:
    return {
        "qa_id": record.get("qa_id"), "tenant_id": record.get("tenant_id") or "default", "question": record.get("question"),
        "approved_answer": record.get("approved_answer"), "status": record.get("status"), "tags": record.get("tags") or [],
        "aliases": record.get("approved_aliases") or record.get("proposed_aliases") or [],
        "citations": len(record.get("approved_citations") or []), "updated_at": record.get("updated_at"),
        "updated_by": record.get("updated_by"), "approved_by": record.get("approved_by"),
    }


def _find(records: List[dict], tenant: str, qa_id: str) -> int:
    for index, record in enumerate(records):
        if (record.get("tenant_id") or "default") == tenant and record.get("qa_id") == qa_id:
            return index
    return -1


def _stamp(record: dict, who: Dict[str, Any], now: str) -> None:
    record["updated_at"], record["updated_by"] = now, who["name"]
    record["updated_by_id"], record["updated_by_verified"] = who["id"], who["verified"]


def _move_aliases(record: dict) -> None:
    """Aliases are 'approved_aliases' on an approved record and 'proposed_aliases' on any other."""
    every = list(record.get("approved_aliases") or []) + [a for a in (record.get("proposed_aliases") or [])]
    merged = list(dict.fromkeys(every))
    if record.get("status") == "approved":
        record["approved_aliases"], record["proposed_aliases"] = merged, []
    else:
        record["approved_aliases"], record["proposed_aliases"] = [], merged
    if not record["approved_aliases"]:
        record.pop("approved_aliases")


# --- reading for the console -------------------------------------------------------------------------------------------


def get(tenant_id: str, qa_id: str) -> Optional[dict]:
    records, _bad = _read()
    index = _find(records, _tenant(tenant_id), qa_id)
    return dict(records[index]) if index >= 0 else None


def list_records(*, tenants: Optional[Set[str]] = None, status: Optional[str] = None, query: str = "", limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    records, _bad = _read()
    scoped = [r for r in records if tenants is None or (r.get("tenant_id") or "default") in tenants]
    counts = {s: 0 for s in STATUSES}
    for record in scoped:
        if record.get("status") in counts:
            counts[record["status"]] += 1
    needle = unicodedata.normalize("NFKC", query or "").strip().lower()
    chosen = []
    for record in scoped:
        if status and record.get("status") != status:
            continue
        if needle:
            haystack = unicodedata.normalize("NFKC", " ".join([
                str(record.get("question") or ""), str(record.get("approved_answer") or ""), " ".join(record.get("tags") or []),
                " ".join(record.get("approved_aliases") or []), " ".join(record.get("proposed_aliases") or []),
            ])).lower()
            if needle not in haystack:
                continue
        chosen.append(record)
    chosen.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    return {"total": len(chosen), "counts": counts, "rows": [_summary(r) for r in chosen[offset:offset + limit]]}


def history(tenant_id: str, qa_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    tenant = _tenant(tenant_id)
    rows: List[Dict[str, Any]] = []
    try:
        with _history_path().open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("qa_id") == qa_id and entry.get("tenant_id") == tenant:
                    rows.append(entry)
    except FileNotFoundError:
        return []
    return list(reversed(rows))[: max(1, int(limit))]


def health() -> Dict[str, Any]:
    records, bad = _read()
    by_status = {s: 0 for s in STATUSES}
    for record in records:
        if record.get("status") in by_status:
            by_status[record["status"]] += 1
    return {"total": len(records), "by_status": by_status, "errors": sorted(_errors(records))[:50], "unreadable_lines": bad,
            "enabled": bool(getattr(config, "APPROVED_QA_ENABLED", False)), "path_is_default": path_is_default()}


# --- changes ----------------------------------------------------------------------------------------------------------------


def _content(record: dict) -> Dict[str, Any]:
    return {key: record.get(key) or ([] if key.endswith(("citations", "aliases")) else "") for key in _CONTENT_FIELDS}


def save(payload: Dict[str, Any], *, by: Any, reason: Any, expected_updated_at: Optional[str] = None) -> Dict[str, Any]:
    """Create a record (no qa_id: a new draft) or edit one (qa_id). Returns the record as saved."""
    who, why = _identity(by), _reason(reason)
    tenant = _tenant(payload.get("tenant_id"))
    clean = _clean(payload)
    qa_id = str(payload.get("qa_id") or "").strip()
    if qa_id and not _QA_ID_RE.fullmatch(qa_id):
        raise StoreError("the qa_id is not valid")
    now = _now()
    with _Locked() as locked:
        records = locked.records
        index = _find(records, tenant, qa_id) if qa_id else -1
        if qa_id and index < 0:
            raise StoreError("no such record")
        before = dict(records[index]) if index >= 0 else None
        if before is not None and expected_updated_at and before.get("updated_at") != expected_updated_at:
            raise Conflict("the record was changed by someone else; reload it")
        record = dict(before) if before is not None else {
            "qa_id": "qa-" + uuid.uuid4().hex[:12], "tenant_id": tenant, "status": "draft", "language": "ja",
            "created_at": now, "created_by": who["name"],
        }
        record.update(question=clean["question"], normalized_question=normalize_question_for_exact_match(clean["question"]),
                      approved_answer=clean["approved_answer"], approved_citations=clean["citations"], tags=clean["tags"])
        if clean["doc_version"] is not None or "doc_version" in payload:
            record["doc_version"] = clean["doc_version"]
        if payload.get("aliases") is not None or payload.get("approved_aliases") is not None:
            record["approved_aliases"], record["proposed_aliases"] = [], clean["aliases"]  # _move_aliases places them
        reverted = False
        if before is not None and before.get("status") == "approved":
            if _content({**record, "approved_aliases": [], "proposed_aliases": clean["aliases"]}) != _content(
                    {**before, "approved_aliases": [], "proposed_aliases": list(before.get("approved_aliases") or [])}):
                record["status"], reverted = "draft", True
                record.pop("approved_at", None), record.pop("approved_by", None)
        _move_aliases(record)
        _stamp(record, who, now)
        after = records[:] if index < 0 else records[:index] + [record] + records[index + 1:]
        if index < 0:
            after = records + [record]
        _check_no_new_errors(records, after)
        _write(after)
        _log([{"at": now, "qa_id": record["qa_id"], "tenant_id": tenant, "action": "create" if before is None else "edit", "by": who["name"],
               "reason": why, "before": before, "after": record}])
    _audit("approved_qa_saved", record, who, why)
    return {**record, "reverted_to_draft": True} if reverted else dict(record)


def set_status(tenant_id: str, qa_id: str, status: str, *, by: Any, reason: Any) -> Dict[str, Any]:
    who, why = _identity(by), _reason(reason)
    tenant = _tenant(tenant_id)
    if status not in STATUSES:
        raise StoreError(f"the status is one of: {', '.join(STATUSES)}")
    now = _now()
    with _Locked() as locked:
        records = locked.records
        index = _find(records, tenant, qa_id)
        if index < 0:
            raise StoreError("no such record")
        before = dict(records[index])
        record = dict(before)
        if status == "approved":
            if not record.get("question") or not record.get("approved_answer"):
                raise StoreError("the question and the answer are required to approve")
            citations = record.get("approved_citations") or []
            if not citations or any(_normalize_citation(c) is None for c in citations):
                raise StoreError("an approved record needs at least one valid citation (a source document and pages)")
            if require_approver():
                author = {"name": before.get("updated_by"), "id": before.get("updated_by_id"), "verified": bool(before.get("updated_by_verified"))}
                if approvals.same_person(author, who):
                    raise StoreError("the last editor of a record cannot approve their own record (APPROVED_QA_REQUIRE_APPROVER)")
            record["approved_at"], record["approved_by"] = now, who["name"]
        else:
            record.pop("approved_at", None), record.pop("approved_by", None)
        record["status"] = status
        _move_aliases(record)
        if status != "approved":
            record["review_note"] = why
        _stamp(record, who, now) if status != "approved" else record.update(updated_at=now)
        after = records[:index] + [record] + records[index + 1:]
        _check_no_new_errors(records, after)
        _write(after)
        _log([{"at": now, "qa_id": qa_id, "tenant_id": tenant, "action": "status", "by": who["name"], "reason": why, "before": before, "after": record}])
    _audit("approved_qa_status", record, who, why)
    return dict(record)


def delete(tenant_id: str, qa_id: str, *, by: Any, reason: Any) -> None:
    who, why = _identity(by), _reason(reason)
    tenant = _tenant(tenant_id)
    now = _now()
    with _Locked() as locked:
        records = locked.records
        index = _find(records, tenant, qa_id)
        if index < 0:
            raise StoreError("no such record")
        before = dict(records[index])
        if before.get("status") == "approved":
            raise StoreError("an approved record is served to users: take it back to draft or reject it first, then delete")
        _write(records[:index] + records[index + 1:])
        _log([{"at": now, "qa_id": qa_id, "tenant_id": tenant, "action": "delete", "by": who["name"], "reason": why, "before": before, "after": None}])
    _audit("approved_qa_deleted", before, who, why)


def _audit(action: str, record: dict, who: Dict[str, Any], reason: str) -> None:
    try:
        audit_log.append_audit_event("admin", {"action": action, "qa_id": record.get("qa_id"), "tenant_id": record.get("tenant_id"),
                                               "status": record.get("status"), "actor": who["name"], "verified": who["verified"], "reason": reason})
    except Exception:  # noqa: BLE001 - the history file already holds the change
        pass


# --- import / export -----------------------------------------------------------------------------------------------------------


def template_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(TEMPLATE_HEADERS)
    writer.writerow(_TEMPLATE_EXAMPLE)
    return out.getvalue()


def export_jsonl(*, tenants: Optional[Set[str]] = None, status: Optional[str] = None) -> str:
    records, _bad = _read()
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records
                   if (tenants is None or (r.get("tenant_id") or "default") in tenants) and (not status or r.get("status") == status))


def _split(value: str) -> List[str]:
    text = (value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in re.split(r"[,、;；\n]", text) if part.strip()]


def _decode(content: Any) -> str:
    if isinstance(content, str):
        if len(content.encode("utf-8")) > MAX_IMPORT_BYTES:
            raise StoreError("the file is too large")
        return content.lstrip("﻿")
    raw = bytes(content)
    if len(raw) > MAX_IMPORT_BYTES:
        raise StoreError("the file is too large")
    from rag_core.document_converters.csv_converter import detect_encoding

    try:
        return raw.decode(detect_encoding(raw)).lstrip("﻿")
    except (UnicodeDecodeError, ValueError):
        raise StoreError("the file could not be decoded (UTF-8 or Shift_JIS)") from None


def import_rows(content: Any, *, default_tenant: str, allowed_tenants: Optional[Set[str]], apply: bool, by: Any, reason: Any,
                on_conflict: str = "skip") -> Dict[str, Any]:
    """Read the CSV of the Excel template. apply=False only reports; apply=True writes new drafts (an existing
    question is skipped or, with on_conflict="update", updated). Nothing imported is approved."""
    if len(content) > MAX_IMPORT_BYTES * 2 if isinstance(content, str) else False:
        raise StoreError("the file is too large")
    text = _decode(content)
    who = why = None
    if apply:
        who, why = _identity(by), _reason(reason)
    if on_conflict not in ("skip", "update"):
        raise StoreError("on_conflict is skip or update")
    default = _tenant(default_tenant)
    reader = list(csv.reader(io.StringIO(text, newline="")))
    if not reader or [h.strip() for h in reader[0]] != TEMPLATE_HEADERS:
        raise StoreError("the first line must be the header of the template: " + ",".join(TEMPLATE_HEADERS))
    body = reader[1:]
    if len(body) > MAX_IMPORT_ROWS:
        raise StoreError(f"too many rows (at most {MAX_IMPORT_ROWS})")
    index = {h: i for i, h in enumerate(TEMPLATE_HEADERS)}
    now = _now()
    results: List[Dict[str, Any]] = []
    counts = {"create": 0, "update": 0, "skip": 0, "error": 0}

    with _Locked() as locked:
        records = locked.records[:]
        by_key = {((r.get("tenant_id") or "default"), r.get("normalized_question") or normalize_question_for_exact_match(r.get("question") or "")): i
                  for i, r in enumerate(records)}
        log: List[Dict[str, Any]] = []
        for offset, row in enumerate(body, start=2):
            if not any(cell.strip() for cell in row):
                continue
            cells = {h: (row[i].strip() if i < len(row) else "") for h, i in index.items()}
            question = cells["質問"]
            try:
                if cells["有効"] == "無効":
                    results.append({"line": offset, "action": "skip", "message": "「有効」が無効の行", "question": question})
                    counts["skip"] += 1
                    continue
                tenant = _tenant(cells["テナント"] or default)
                if allowed_tenants is not None and tenant not in allowed_tenants:
                    raise StoreError(f"tenant {tenant}: outside this administrator's tenants")
                pages = [int(p) for p in _split(cells["出典ページ"].strip("[]"))] if cells["出典ページ"] else []
                citations = [{"source_doc": cells["出典文書"], "source_pages": pages, "title": cells["出典タイトル"]}] if cells["出典文書"] else []
                clean = _clean({"question": question, "approved_answer": cells["正解回答"], "citations": citations,
                                "aliases": _split(cells["言い換え"]), "tags": _split(cells["分類"]), "doc_version": cells["文書版"]})
                normalized = normalize_question_for_exact_match(clean["question"])
                existing = by_key.get((tenant, normalized))
                if existing is not None and on_conflict == "skip":
                    results.append({"line": offset, "action": "skip", "message": "同じ質問が既にあります", "question": question})
                    counts["skip"] += 1
                    continue
                if existing is not None:
                    before = dict(records[existing])
                    record = dict(before)
                    record.update(approved_answer=clean["approved_answer"], approved_citations=clean["citations"], tags=clean["tags"],
                                  approved_aliases=[], proposed_aliases=clean["aliases"], status="draft")
                    record.pop("approved_at", None), record.pop("approved_by", None)
                    action = "update"
                else:
                    before = None
                    record = {"qa_id": "qa-" + uuid.uuid4().hex[:12], "tenant_id": tenant, "status": "draft", "language": "ja", "created_at": now,
                              "created_by": who["name"] if who else "import", "question": clean["question"], "normalized_question": normalized,
                              "approved_answer": clean["approved_answer"], "approved_citations": clean["citations"], "tags": clean["tags"],
                              "approved_aliases": [], "proposed_aliases": clean["aliases"]}
                    action = "create"
                if clean["doc_version"]:
                    record["doc_version"] = clean["doc_version"]
                _move_aliases(record)
                record["updated_at"], record["updated_by"] = now, who["name"] if who else "import"
                if existing is not None:
                    records[existing] = record
                else:
                    records.append(record)
                    by_key[(tenant, normalized)] = len(records) - 1
                counts[action] += 1
                results.append({"line": offset, "action": action, "message": "" if citations else "出典がありません（承認の前に追加が必要です）", "question": question})
                log.append({"at": now, "qa_id": record["qa_id"], "tenant_id": tenant, "action": "import-" + action, "by": who["name"] if who else "import",
                            "reason": why or "", "before": before, "after": record})
            except (StoreError, ValueError) as exc:
                counts["error"] += 1
                results.append({"line": offset, "action": "error", "message": str(exc), "question": question})
        if apply and log:
            _check_no_new_errors(locked.records, records)
            _write(records)
            _log(log)
    if apply and log:
        _audit("approved_qa_imported", {"qa_id": None, "tenant_id": default, "status": "draft"}, who, f"{why} ({len(log)} rows)")
    return {"applied": bool(apply), "counts": counts, "rows": results}
