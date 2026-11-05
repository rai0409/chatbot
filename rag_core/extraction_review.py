"""Review decisions on OCR lines, and their use at the next ingestion (step 6-2)."""
from __future__ import annotations

import hashlib
import json
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


def _who(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 64:
        raise ErrorType("who decided (1 to 64 characters) is required")
    return text


def _line(tenant_id: str, source_doc: str, page: int, line_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    record = store.load_record(tenant_id, source_doc)
    if record is None:
        raise ErrorType("no extraction record for this document")
    for entry in record["pages"]:
        if entry["page"] == page:
            for line in entry["lines"]:
                if line["id"] == line_id:
                    return entry, line
    raise ErrorType("no such line on that page")


def _entry_id(page: int, before: str) -> str:
    return hashlib.sha256(f"{page}\0{before}".encode("utf-8")).hexdigest()[:12]


def _decide(tenant_id: str, source_doc: str, page: int, line_id: str, state: str, after: Optional[str], by: str,
            note: str, now: Optional[datetime]) -> Dict[str, Any]:
    _page, line = _line(tenant_id, source_doc, page, line_id)
    entry = {"id": _entry_id(page, line["text"]), "page": page, "before": line["text"], "after": after, "state": state,
             "by": _who(by), "at": (now or datetime.now(timezone.utc)).isoformat(), "note": str(note or "")[:500]}
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
    _page, line = _line(tenant_id, source_doc, page, line_id)
    if new == line["text"].strip():
        raise ErrorType("the text is unchanged (confirm the line instead)")
    return _decide(tenant_id, source_doc, page, line_id, CORRECTED, new, by, note, now)


def confirm_line(tenant_id: str, source_doc: str, page: int, line_id: str, *, by: str, note: str = "",
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """Say that the OCR text of this line is right as it is."""
    return _decide(tenant_id, source_doc, page, line_id, CONFIRMED, None, by, note, now)


def revert(tenant_id: str, source_doc: str, page: int, line_id: str) -> bool:
    _page, line = _line(tenant_id, source_doc, page, line_id)
    wanted = _entry_id(page, line["text"])
    data = load_corrections(tenant_id, source_doc)
    kept = [e for e in data["entries"] if e.get("id") != wanted]
    if len(kept) == len(data["entries"]):
        return False
    data["entries"] = kept
    _save(tenant_id, source_doc, data)
    return True


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
    if not entries:
        return list(pages), stats
    out = []
    for page in pages:
        text = page["text"]
        if page.get("extraction_method") == "ocr_fallback":
            for entry in entries:
                if entry["page"] != page["page"]:
                    continue
                before, after = normalize(entry["before"]), normalize(entry["after"])
                if before and before in text:
                    text = text.replace(before, after, 1)
                    stats["applied"] += 1
                else:
                    stats["unmatched"] += 1
        out.append({**page, "text": text})
    return out, stats


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


def _states(tenant_id: str, source_doc: str) -> Dict[Tuple[int, str], str]:
    try:
        return {(e["page"], e["before"]): e["state"] for e in load_corrections(tenant_id, source_doc)["entries"]}
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
        decided = _states(tenant_id, record["source_doc"])
        for page in record["pages"]:
            for line in page["lines"]:
                confidence = line["confidence"]
                if confidence is not None and confidence >= threshold:
                    continue
                state = decided.get((page["page"], line["text"]), "open")
                if state != "open" and not include_reviewed:
                    continue
                items.append({"source_doc": record["source_doc"], "page": page["page"], "line_id": line["id"],
                              "text": line["text"], "confidence": confidence, "state": state})
    items.sort(key=lambda i: (-1.0 if i["confidence"] is None else i["confidence"], i["source_doc"], i["page"], i["line_id"]))
    return items[:max(1, int(limit))]


def documents(tenant_id: str) -> List[Dict[str, Any]]:
    """One row per document with a record: pages, lines, how many lines are open / corrected / confirmed."""
    rows = []
    for record in _records(tenant_id):
        decided = _states(tenant_id, record["source_doc"])
        threshold = store.review_max_confidence()
        lines = [(p["page"], l) for p in record["pages"] for l in p["lines"]]
        weak = [(p, l) for p, l in lines if l["confidence"] is None or l["confidence"] < threshold]
        states = [decided.get((p, l["text"]), "open") for p, l in weak]
        rows.append({"source_doc": record["source_doc"], "saved_at": record["saved_at"], "pages": len(record["pages"]),
                     "lines": len(lines), "open": states.count("open"), "corrected": states.count(CORRECTED),
                     "confirmed": states.count(CONFIRMED), "preview_available": bool(record.get("file_path"))})
    return sorted(rows, key=lambda r: (-r["open"], r["source_doc"]))


def request_reingest(tenant_id: str, source_doc: str) -> List[str]:
    """Ask the sources of the tenant to ingest this document again at their next apply. Returns the source ids."""
    from rag_core import source_apply

    return source_apply.mark_for_reingest(tenant_id, source_doc)
