from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping


_EXTENSION_SOURCE_TYPES = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}


def ensure_source_file_alias(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    out = dict(metadata or {})
    source_doc = _clean_string(out.get("source_doc"))
    source_file = _clean_string(out.get("source_file"))
    if not source_file and source_doc:
        out["source_file"] = source_doc
    if not source_doc and source_file:
        out["source_doc"] = source_file
    return out


def normalize_pages(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    out = dict(metadata or {})
    pages = _page_list(out.get("source_pages"))
    if not pages:
        pages = _page_list(out.get("pageno"))
    if pages:
        out["source_pages"] = pages
        if out.get("source_page_start") in (None, "", []):
            out["source_page_start"] = pages[0]
        if out.get("source_page_end") in (None, "", []):
            out["source_page_end"] = pages[-1]
    return out


def normalize_doc_type(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    out = dict(metadata or {})
    if not _clean_string(out.get("doc_type")):
        out["doc_type"] = "document"
    return out


def normalize_chunk_type(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    out = dict(metadata or {})
    if not _clean_string(out.get("chunk_type")):
        out["chunk_type"] = "text"
    return out


def build_display_text(record: Mapping[str, Any] | None) -> str:
    if not isinstance(record, Mapping):
        return ""
    value = _clean_string(record.get("display_text"))
    if value:
        return value
    return str(record.get("text") or "")


def build_searchable_text(record: Mapping[str, Any] | None) -> str:
    if not isinstance(record, Mapping):
        return ""
    value = _clean_string(record.get("searchable_text"))
    if value:
        return _normalize_text(value)
    display = build_display_text(record)
    if display:
        return _normalize_text(display)
    return _normalize_text(record.get("text"))


def normalize_source_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    out = ensure_source_file_alias(metadata)
    out = normalize_pages(out)
    out = normalize_doc_type(out)
    out = normalize_chunk_type(out)

    if not _clean_string(out.get("source_type")):
        out["source_type"] = _infer_source_type(out)
    if not _clean_string(out.get("parser")):
        out["parser"] = _clean_string(out.get("source_parser")) or _clean_string(out.get("type")) or "unknown"
    if not _clean_string(out.get("tenant_id")):
        out["tenant_id"] = "default"
    return out


def validate_required_retrieval_metadata(metadata: Mapping[str, Any] | None) -> list[str]:
    raw = dict(metadata or {})
    missing: list[str] = []
    for key in (
        "source_doc",
        "source_file",
        "source_type",
        "parser",
        "doc_type",
        "chunk_type",
        "tenant_id",
        "source_pages",
        "source_page_start",
        "source_page_end",
    ):
        value = raw.get(key)
        if value is None or value == "" or value == []:
            missing.append(key)
    return missing


def normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(record)
    chunk_id = _clean_string(out.get("chunk_id")) or _clean_string(out.get("id"))
    if chunk_id and not _clean_string(out.get("chunk_id")):
        out["chunk_id"] = chunk_id
    out = normalize_source_metadata(out)
    out["display_text"] = build_display_text(out)
    out["searchable_text"] = build_searchable_text(out)
    return out


def _infer_source_type(metadata: Mapping[str, Any]) -> str:
    source = _clean_string(metadata.get("source_file")) or _clean_string(metadata.get("source_doc"))
    suffix = Path(source).suffix.lower()
    if suffix in _EXTENSION_SOURCE_TYPES:
        return _EXTENSION_SOURCE_TYPES[suffix]
    existing_type = _clean_string(metadata.get("type"))
    return existing_type or "unknown"


def _page_list(value: Any) -> list[int]:
    if value is None or value == "":
        return []
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, float):
        return [int(value)] if value.is_integer() else []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            return _page_list(parsed)
        parts = [p.strip() for p in text.split(",")] if "," in text else [text]
        pages: list[int] = []
        for part in parts:
            if part:
                pages.extend(_page_list(_coerce_int(part)))
        return pages
    if isinstance(value, (list, tuple, set)):
        pages = []
        for item in value:
            pages.extend(_page_list(item))
        return pages
    return []


def _coerce_int(value: Any) -> Any:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
