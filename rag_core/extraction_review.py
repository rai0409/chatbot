"""Review decisions on OCR lines, and their use at the next ingestion (step 6-2)."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from rag_core import extraction_store as store

SCHEMA = 1
MAX_TEXT_CHARS = 2000
CORRECTED, CONFIRMED = "corrected", "confirmed"
ErrorType = store.ExtractionError


def _path(tenant_id: str, source_doc: str) -> Path:
    base = store.record_path(tenant_id, source_doc)
    return base.with_name(base.stem + ".corrections.json")


def load_corrections(tenant_id: str, source_doc: str) -> Dict[str, Any]:
    try:
        data = json.loads(_path(tenant_id, source_doc).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": SCHEMA, "entries": []}
    except (OSError, ValueError) as exc:
        raise ErrorType("the corrections file is unreadable") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("entries"), list):
        raise ErrorType("the corrections file has an unsupported format")
    return data


def _save(tenant_id: str, source_doc: str, data: Dict[str, Any]) -> None:
    store._write_json(_path(tenant_id, source_doc), data)


def _identity(value: Any) -> Dict[str, Any]:
    """{"name", "id", "verified"} of who acts. A plain string is a typed name (not identified)."""
    if isinstance(value, dict):
        name, ident = str(value.get("name") or "").strip(), value.get("id") or None
        verified = bool(value.get("verified") and ident)
    else:
        name, ident, verified = str(value or "").strip(), None, False
    if not name or len(name) > 64:
        raise ErrorType("who decided (1 to 64 characters) is required")
    return {"name": name, "id": ident, "verified": verified}


def _who(value: Any) -> str:
    return _identity(value)["name"]


def require_approval() -> bool:
    """EXTRACTION_REQUIRE_APPROVAL=true: a corrected line enters the knowledge base only after a different
    person approved the correction (the author cannot approve their own)."""
    return str(os.getenv("EXTRACTION_REQUIRE_APPROVAL", "")).strip().lower() in {"1", "true", "yes", "on"}


_THREAD_LOCK = threading.Lock()


@contextmanager
def _locked(tenant_id: str, source_doc: str):
    """One writer at a time per document (threads and processes): the corrections
    file is read, changed and written back, so two decisions would lose one."""
    lock_path = _path(tenant_id, source_doc).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK:
        with open(lock_path, "a+") as handle:
            try:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX)
            except ImportError:  # no flock (Windows): the thread lock still holds
                pass
            yield


def _line(tenant_id: str, source_doc: str, page: int, line_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
    """(page entry, line, occurrence): the occurrence counts earlier lines with the same text on that page."""
    record = store.load_record(tenant_id, source_doc)
    if record is None:
        raise store.ExtractionNotFound("no extraction record for this document")
    for entry in record["pages"]:
        if entry["page"] == page:
            for line, occurrence in _with_occurrence(entry):
                if line["id"] == line_id:
                    return entry, line, occurrence
    raise store.ExtractionNotFound("no such line on that page")


def _with_occurrence(page_entry: Dict[str, Any]):
    seen: Dict[str, int] = {}
    for line in page_entry["lines"]:
        text = line["text"]
        yield line, seen.get(text, 0)
        seen[text] = seen.get(text, 0) + 1


def _entry_id(page: int, before: str, occurrence: int = 0) -> str:
    # The first occurrence keeps the id earlier versions wrote.
    key = f"{page}\0{before}" if not occurrence else f"{page}\0{before}\0{occurrence}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _decide(tenant_id: str, source_doc: str, page: int, line_id: str, state: str, after: Optional[str], by: str,
            note: str, now: Optional[datetime]) -> Dict[str, Any]:
    _page, line, occurrence = _line(tenant_id, source_doc, page, line_id)
    who = _identity(by)
    entry = {"id": _entry_id(page, line["text"], occurrence), "page": page, "before": line["text"], "after": after,
             "state": state, "occurrence": occurrence, "by": who["name"], "by_id": who["id"], "by_verified": who["verified"],
             "at": (now or datetime.now(timezone.utc)).isoformat(), "note": str(note or "")[:500]}
    with _locked(tenant_id, source_doc):
        data = load_corrections(tenant_id, source_doc)
        data["entries"] = [e for e in data["entries"] if e.get("id") != entry["id"]] + [entry]
        _save(tenant_id, source_doc, data)
    return entry


def submit_correction(tenant_id: str, source_doc: str, page: int, line_id: str, text: str, *, by: str, note: str = "",
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    """Replace the text of one line. The replacement must differ from the OCR text (use confirm_line otherwise)."""
    new = str(text or "").strip()
    if not new or len(new) > MAX_TEXT_CHARS or any(ord(c) < 32 and c not in "\t" for c in new):
        raise ErrorType(f"the corrected text must be 1 to {MAX_TEXT_CHARS} characters without control characters")
    _page, line, _occurrence = _line(tenant_id, source_doc, page, line_id)
    if new == line["text"].strip():
        raise ErrorType("the text is unchanged (confirm the line instead)")
    return _decide(tenant_id, source_doc, page, line_id, CORRECTED, new, by, note, now)


def confirm_line(tenant_id: str, source_doc: str, page: int, line_id: str, *, by: str, note: str = "",
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """Say that the OCR text of this line is right as it is."""
    return _decide(tenant_id, source_doc, page, line_id, CONFIRMED, None, by, note, now)


def revert(tenant_id: str, source_doc: str, page: int, line_id: str) -> bool:
    _page, line, occurrence = _line(tenant_id, source_doc, page, line_id)
    wanted = _entry_id(page, line["text"], occurrence)
    with _locked(tenant_id, source_doc):
        data = load_corrections(tenant_id, source_doc)
        kept = [e for e in data["entries"] if e.get("id") != wanted]
        if len(kept) == len(data["entries"]):
            return False
        data["entries"] = kept
        _save(tenant_id, source_doc, data)
    return True


def approve_correction(tenant_id: str, source_doc: str, page: int, line_id: str, *, by: Any, note: str = "",
                       now: Optional[datetime] = None) -> Dict[str, Any]:
    """A second person approves the correction of one line. Not the author (by identifier when both are identified,
    else by name); with APPROVAL_REQUIRE_SSO both must be identified. Correcting the line again clears the approval."""
    import config
    from rag_core import approvals

    approver = _identity(by)
    _page, line, occurrence = _line(tenant_id, source_doc, page, line_id)
    wanted = _entry_id(page, line["text"], occurrence)
    with _locked(tenant_id, source_doc):
        data = load_corrections(tenant_id, source_doc)
        entry = next((e for e in data["entries"] if e.get("id") == wanted), None)
        if entry is None or entry.get("state") != CORRECTED:
            raise ErrorType("only a correction can be approved (this line has none)")
        author = {"name": entry.get("by"), "id": entry.get("by_id"), "verified": bool(entry.get("by_verified"))}
        if approvals.same_person(author, approver):
            raise ErrorType("the author of a correction cannot approve their own correction")
        if getattr(config, "APPROVAL_REQUIRE_SSO", False) and not (author["verified"] and approver["verified"]):
            raise ErrorType("both people must be identified by single sign-on (APPROVAL_REQUIRE_SSO)")
        entry.update(approved_by=approver["name"], approved_by_id=approver["id"], approved_by_verified=approver["verified"],
                     approved_at=(now or datetime.now(timezone.utc)).isoformat(), approval_note=str(note or "")[:500])
        _save(tenant_id, source_doc, data)
    return entry


def apply_corrections(
    pages: Sequence[Dict[str, Any]], tenant_id: str, source_doc: str,
    *, normalize: Callable[[str], str] = lambda text: text,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Put the corrected wording into the OCRed pages before they are chunked. Never raises: on any problem, no change."""
    stats: Dict[str, Any] = {"applied": 0, "unmatched": 0}
    try:
        entries = [e for e in load_corrections(tenant_id, source_doc)["entries"] if e.get("state") == CORRECTED]
    except ErrorType:
        return list(pages), {**stats, "error": True}
    if require_approval():
        # Only approved corrections enter the knowledge base; the rest wait for a second person.
        waiting = [e for e in entries if not e.get("approved_by")]
        entries = [e for e in entries if e.get("approved_by")]
        stats["awaiting_approval"] = len(waiting)
    if not entries:
        return list(pages), stats
    out = []
    for page in pages:
        text = page["text"]
        if page.get("extraction_method") == "ocr_fallback":
            # Later occurrences first: replacing one does not shift the ones before it.
            for entry in sorted(entries, key=lambda e: -int(e.get("occurrence") or 0)):
                if entry["page"] != page["page"]:
                    continue
                before, after = normalize(entry["before"]), normalize(entry["after"])
                replaced = _replace_nth(text, before, after, int(entry.get("occurrence") or 0)) if before else None
                if replaced is not None:
                    text = replaced
                    stats["applied"] += 1
                else:
                    stats["unmatched"] += 1
        out.append({**page, "text": text})
    return out, stats


def _replace_nth(text: str, before: str, after: str, n: int) -> Optional[str]:
    """`text` with the n-th (0-based) occurrence of `before` replaced; None when there is no such occurrence."""
    index = -1
    for _ in range(n + 1):
        index = text.find(before, index + 1)
        if index < 0:
            return None
    return text[:index] + after + text[index + len(before):]


def _records(tenant_id: str) -> List[Dict[str, Any]]:
    folder = store.store_dir() / store._tenant(tenant_id)
    records = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.name.endswith(".corrections.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("schema") == store.SCHEMA:
            records.append(data)
    return records


def _states(tenant_id: str, source_doc: str) -> Dict[Tuple[int, str, int], str]:
    try:
        return {(e["page"], e["before"], int(e.get("occurrence") or 0)): e["state"]
                for e in load_corrections(tenant_id, source_doc)["entries"]}
    except ErrorType:
        return {}


def approval_state(entry: Optional[Dict[str, Any]]) -> Optional[str]:
    """"awaiting" / "approved" for a correction when approval is required; None otherwise (open, confirmed, or the
    setting is off)."""
    if entry is None or entry.get("state") != CORRECTED or not require_approval():
        return None
    return "approved" if entry.get("approved_by") else "awaiting"


def line_entries(tenant_id: str, source_doc: str) -> Dict[Tuple[int, str, int], Dict[str, Any]]:
    """The decision entry of every decided line, by (page, text, occurrence) like _states."""
    try:
        return {(e["page"], e["before"], int(e.get("occurrence") or 0)): e
                for e in load_corrections(tenant_id, source_doc)["entries"]}
    except ErrorType:
        return {}


def review_items(
    tenant_id: str, *, max_confidence: Optional[float] = None, include_reviewed: bool = False,
    source_doc: Optional[str] = None, limit: int = 100,
) -> List[Dict[str, Any]]:
    """Lines to look at, lowest confidence first. A line without a confidence counts as the lowest."""
    threshold = store.review_max_confidence() if max_confidence is None else float(max_confidence)
    items: List[Dict[str, Any]] = []
    for record in _records(tenant_id):
        if source_doc is not None and record["source_doc"] != source_doc:
            continue
        entries = line_entries(tenant_id, record["source_doc"])
        for page in record["pages"]:
            for line, occurrence in _with_occurrence(page):
                confidence = line["confidence"]
                if confidence is not None and confidence >= threshold:
                    continue
                entry = entries.get((page["page"], line["text"], occurrence))
                state = entry["state"] if entry else "open"
                if state != "open" and not include_reviewed:
                    continue
                items.append({"source_doc": record["source_doc"], "page": page["page"], "line_id": line["id"],
                              "text": line["text"], "confidence": confidence, "state": state,
                              "approval": approval_state(entry)})
    items.sort(key=lambda i: (-1.0 if i["confidence"] is None else i["confidence"], i["source_doc"], i["page"], i["line_id"]))
    return items[:max(1, int(limit))]


def documents(tenant_id: str) -> List[Dict[str, Any]]:
    """One row per document with a record: pages, lines, how many lines are open / corrected / confirmed."""
    rows = []
    for record in _records(tenant_id):
        decided = _states(tenant_id, record["source_doc"])
        threshold = store.review_max_confidence()
        lines = [(p["page"], l, o) for p in record["pages"] for l, o in _with_occurrence(p)]
        weak = [(p, l, o) for p, l, o in lines if l["confidence"] is None or l["confidence"] < threshold]
        states = [decided.get((p, l["text"], o), "open") for p, l, o in weak]
        awaiting = 0
        if require_approval():
            try:
                awaiting = sum(1 for e in load_corrections(tenant_id, record["source_doc"])["entries"]
                               if e.get("state") == CORRECTED and not e.get("approved_by"))
            except ErrorType:
                awaiting = 0
        rows.append({"source_doc": record["source_doc"], "saved_at": record["saved_at"], "pages": len(record["pages"]),
                     "lines": len(lines), "open": states.count("open"), "corrected": states.count(CORRECTED),
                     "confirmed": states.count(CONFIRMED), "awaiting_approval": awaiting,
                     "preview_available": bool(record.get("file_path"))})
    return sorted(rows, key=lambda r: (-r["open"], r["source_doc"]))


def request_reingest(tenant_id: str, source_doc: str) -> List[str]:
    """Ask the sources of the tenant to ingest this document again at their next apply. Returns the source ids."""
    from rag_core import source_apply

    return source_apply.mark_for_reingest(tenant_id, source_doc)
