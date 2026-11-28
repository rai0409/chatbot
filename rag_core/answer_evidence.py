"""Server-side evidence of one answer (2026-11-27).

Every /chat and /chat/stream answer gets a server-generated request_id and one evidence record, written to the
hash-chained audit log as kind "evidence" (<RUNS_DIR>/audit/evidence_events.jsonl; verified by
scripts/verify_audit_chain.py like the other *_events.jsonl files). The record says which chunks were cited, at
which version, from which index generation and profile, and whether the answer came from the answer cache (then
origin_request_id names the request that built it). Question and answer TEXT is never stored: only sha256 hashes.

Lookup (lookup_evidence) serves /chat/source staleness checks, conversation turns and feedback linking. It reads a
bounded in-process map of the records this process wrote (EVIDENCE_LOOKUP_CACHE_SIZE, default 4096), then the tail
of the current evidence file (EVIDENCE_LOOKUP_TAIL_BYTES, default 4 MiB). Older or rotated-away records are not
found; callers then fall back to their legacy behaviour. AUDIT_EVIDENCE_ENABLED=0 turns the records off.
"""
from __future__ import annotations

import hashlib
import copy
import json
import os
import uuid
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Mapping, Optional

import config
from rag_core import audit_log

KIND = "evidence"
_DEFAULT_CACHE_SIZE = 4096
_DEFAULT_TAIL_BYTES = 4 * 1024 * 1024
_MAX_CITED = 20
_MAX_IDS = 20

_LOCK = Lock()
_RECENT: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def enabled() -> bool:
    return str(os.getenv("AUDIT_EVIDENCE_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"}


def evidence_path() -> Path:
    return Path(config.RUNS_DIR) / "audit" / "evidence_events.jsonl"


def new_request_id() -> str:
    return uuid.uuid4().hex


def sha256_text(text: Any) -> Optional[str]:
    if text is None:
        return None
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(str(os.getenv(name, "")).strip() or default))
    except ValueError:
        return default


def _ids(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v) for v in value if v is not None][:_MAX_IDS]


def _pages(value: Any) -> List[int]:
    out: List[int] = []
    for item in value if isinstance(value, (list, tuple)) else []:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out[:_MAX_IDS]


def cited_from_citations(citations: Any) -> List[Dict[str, Any]]:
    """The evidence entries of a response's citations (no free text: supporting sentences are not kept)."""
    out: List[Dict[str, Any]] = []
    for item in list(citations or [])[:_MAX_CITED]:
        if not isinstance(item, Mapping):
            continue
        chunk_id = item.get("chunk_id")
        entry = {
            "number": item.get("number"),
            "chunk_id": str(chunk_id) if chunk_id is not None else None,
            "member_chunk_ids": _ids(item.get("member_chunk_ids")) or ([str(chunk_id)] if chunk_id else []),
            "source_doc": item.get("source_doc"),
            "source_pages": _pages(item.get("source_pages") or ([item["page"]] if item.get("page") is not None else [])),
            "chunk_sha256": item.get("chunk_sha256"),
            "doc_checksum": item.get("doc_checksum"),
            "index_generation": item.get("index_generation"),
        }
        out.append(entry)
    return out


def cited_source_docs(citations: Any) -> List[str]:
    docs: List[str] = []
    for item in list(citations or []):
        if isinstance(item, Mapping) and item.get("source_doc") and str(item["source_doc"]) not in docs:
            docs.append(str(item["source_doc"]))
    return docs[:_MAX_IDS]


def active_generation_id() -> Optional[str]:
    try:
        from rag_core import index_generation

        pointer = index_generation.active_pointer() or {}
    except Exception:  # noqa: BLE001  an unreadable pointer leaves the field unknown
        return None
    return str(pointer.get("generation_id") or "") or None


def build_evidence(
    *,
    request_id: str,
    tenant_id: str,
    payload: Mapping[str, Any],
    trace: Optional[Mapping[str, Any]] = None,
    collection: Optional[str] = None,
    cache_key_digest: Optional[str] = None,
    served_from_cache: bool = False,
    origin: Optional[Mapping[str, Any]] = None,
    trace_id: Optional[str] = None,
    streamed: bool = False,
    access_scope: Optional[str] = None,
) -> Dict[str, Any]:
    """One evidence record. payload is the response body; trace the pipeline trace (None on a cache hit)."""
    trace = trace or {}
    origin = origin or {}
    if served_from_cache:
        snapshot = copy.deepcopy(origin["evidence"])
        snapshot.update(request_id=str(request_id), client_trace_id=str(trace_id)[:200] if trace_id else None,
                        pipeline_request_id=None, served_from_cache=True,
                        origin_request_id=snapshot["request_id"],
                        origin_generation_id=snapshot["active_generation_id"], streamed=bool(streamed))
        return snapshot
    selected = trace.get("selected_context_chunk_ids")
    if selected is None:
        selected = origin.get("selected_context_chunk_ids")
    record: Dict[str, Any] = {
        "request_id": str(request_id),
        # The client's trace id is kept for correlation only; it is never the evidence id.
        "client_trace_id": str(trace_id)[:200] if trace_id else None,
        "pipeline_request_id": trace.get("request_id"),
        "tenant_id": tenant_id,
        "answer_sha256": sha256_text(payload.get("answer_text") or ""),
        "answer_mode": payload.get("answer_mode"),
        "approved_qa_id": payload.get("approved_qa_id"),
        "cited": cited_from_citations(payload.get("citations")),
        "access_scope": access_scope,
        "selected_context_chunk_ids": _ids(selected),
        "collection": collection or None,
        "active_generation_id": trace.get("active_generation_id"),
        "rag_profile_id": payload.get("rag_profile_id") or trace.get("rag_profile_id"),
        "rag_profile_version": trace.get("rag_profile_version") or origin.get("rag_profile_version"),
        "cache_key_digest": cache_key_digest,
        "served_from_cache": bool(served_from_cache),
        "origin_request_id": origin.get("request_id") if served_from_cache else None,
        "origin_generation_id": origin.get("generation_id") if served_from_cache else None,
        "streamed": bool(streamed),
    }
    # Only use metadata captured in the pipeline for the cited identity. Ambiguous
    # duplicate IDs and legacy traces are unknown, never filled from the corpus.
    captured = trace.get("cited") or []
    for entry in record["cited"]:
        matches = [x for x in captured if isinstance(x, Mapping) and x.get("chunk_id") == entry["chunk_id"]]
        meta = matches[0] if len(matches) == 1 else {}
        for key in ("source_row_sha256", "doc_checksum", "index_generation"):
            entry[key] = meta.get(key)
    return record


_SNAPSHOT_FIELDS = ("request_id", "tenant_id", "answer_sha256", "answer_mode", "approved_qa_id",
                    "cache_key_digest", "cited", "selected_context_chunk_ids", "collection",
                    "active_generation_id", "rag_profile_id", "rag_profile_version", "access_scope")
_CITED_FIELDS = {"number", "chunk_id", "member_chunk_ids", "source_doc", "source_pages", "chunk_sha256",
                 "source_row_sha256", "doc_checksum", "index_generation"}


def origin_for_cache(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Copy only the saved evidence fields into a versioned, JSON-compatible snapshot."""
    snapshot = {key: copy.deepcopy(record[key]) for key in _SNAPSHOT_FIELDS}
    return {"version": 2, "request_id": snapshot["request_id"], "evidence": snapshot}


def valid_cache_origin(origin: Any, *, payload: Mapping[str, Any], tenant_id: str, cache_key_digest: str, access_scope: Optional[str] = None) -> bool:
    if not isinstance(origin, dict) or type(origin.get("version")) is not int or origin.get("version") != 2:
        return False
    snap = origin.get("evidence")
    if not isinstance(snap, dict) or set(snap) != set(_SNAPSHOT_FIELDS):
        return False
    if (not isinstance(snap["request_id"], str) or not snap["request_id"]
            or origin.get("request_id") != snap["request_id"] or snap["tenant_id"] != tenant_id
            or snap["answer_sha256"] != sha256_text(payload.get("answer_text") or "")
            or snap["cache_key_digest"] != cache_key_digest or access_scope is None
            or snap["access_scope"] != access_scope):
        return False
    if not isinstance(snap["cited"], list) or not isinstance(snap["selected_context_chunk_ids"], list):
        return False
    if not all(isinstance(x, str) for x in snap["selected_context_chunk_ids"]):
        return False
    if not all(isinstance(item, dict) and set(item) == _CITED_FIELDS
               and isinstance(item["member_chunk_ids"], list) and all(isinstance(x, str) for x in item["member_chunk_ids"])
               and isinstance(item["source_pages"], list) and all(type(x) is int for x in item["source_pages"])
               for item in snap["cited"]):
        return False
    payload_cited = cited_from_citations(payload.get("citations"))
    if len(payload_cited) != len(snap["cited"]):
        return False
    for stored, actual in zip(snap["cited"], payload_cited):
        # Internal row hash is deliberately absent from the public payload.
        for key in ("number", "chunk_id", "member_chunk_ids", "source_doc", "source_pages", "chunk_sha256", "doc_checksum", "index_generation"):
            if stored.get(key) != actual.get(key):
                return False
    if audit_log._bounded(snap) != snap:
        return False
    for item in snap["cited"]:
        if item["number"] is not None and type(item["number"]) is not int:
            return False
        for key in ("chunk_id", "source_doc", "chunk_sha256", "source_row_sha256", "doc_checksum", "index_generation"):
            if item[key] is not None and not isinstance(item[key], str):
                return False
    for key in ("answer_mode", "approved_qa_id", "collection", "active_generation_id", "rag_profile_id", "rag_profile_version"):
        if snap[key] is not None and not isinstance(snap[key], (str, int)):
            return False
    try:
        json.dumps(snap, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True


def _remember(record: Dict[str, Any]) -> None:
    size = _int_env("EVIDENCE_LOOKUP_CACHE_SIZE", _DEFAULT_CACHE_SIZE)
    if size <= 0:
        return
    with _LOCK:
        _RECENT[str(record.get("request_id"))] = dict(record)
        _RECENT.move_to_end(str(record.get("request_id")))
        while len(_RECENT) > size:
            _RECENT.popitem(last=False)


def record_evidence(record: Dict[str, Any]) -> bool:
    """Append the record to the evidence audit chain (failures are counted by audit_log, never raised)."""
    if not enabled():
        return False
    # Keep the in-memory and cached copy identical to the bounded audit row.
    bounded = audit_log._bounded(record)
    if not audit_log.append_evidence_audit_event(bounded):
        return False
    record.clear()
    record.update(bounded)
    _remember(record)
    return True


def clear_recent() -> None:
    with _LOCK:
        _RECENT.clear()


def _tail_rows(path: Path, max_bytes: int) -> Iterable[Dict[str, Any]]:
    """Rows of the last max_bytes of the file, newest first."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - max_bytes)
            handle.seek(start)
            data = handle.read()
    except OSError:
        return
    lines = data.splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # a partial first line
    for raw in reversed(lines):
        try:
            row = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(row, dict):
            yield row


def lookup_evidence_diagnostic(request_id: Optional[str], tenant_id: str) -> Optional[Dict[str, Any]]:
    """The evidence record of request_id when it belongs to tenant_id; None when unknown or of another tenant."""
    rid = str(request_id or "").strip()
    if not rid or len(rid) > 128:
        return None
    with _LOCK:
        cached = _RECENT.get(rid)
    if cached is None:
        for row in _tail_rows(evidence_path(), _int_env("EVIDENCE_LOOKUP_TAIL_BYTES", _DEFAULT_TAIL_BYTES)):
            if row.get("kind") == KIND and str(row.get("request_id") or "") == rid:
                cached = row
                break
    if cached is None or str(cached.get("tenant_id") or "") != str(tenant_id or ""):
        return None
    return dict(cached)


def lookup_evidence(request_id: Optional[str], tenant_id: str, *, access_scope: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return evidence only when both tenant and known retrieval scope match."""
    if access_scope is None:
        return None
    record = lookup_evidence_diagnostic(request_id, tenant_id)
    if record is None or "access_scope" not in record or record["access_scope"] != access_scope:
        return None
    return record


def cited_entry(record: Mapping[str, Any], *, number: Optional[int] = None,
                chunk_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The cited entry by citation number, else the entry whose chunk (or merged member) is chunk_id."""
    cited = [c for c in record.get("cited") or [] if isinstance(c, Mapping)]
    if number is not None:
        for entry in cited:
            if entry.get("number") == number:
                return dict(entry)
        return None
    if chunk_id:
        for entry in cited:
            if entry.get("chunk_id") == chunk_id:
                return dict(entry)
        for entry in cited:
            if chunk_id in (entry.get("member_chunk_ids") or []):
                return dict(entry)
    return None
