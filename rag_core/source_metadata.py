from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable


_MAX_STRING_CHARS = 500
_TRUNCATED_SUFFIX = "...[truncated]"

_STRING_FIELDS = {
    "source_id",
    "source_title",
    "source_type",
    "source_doc",
    "source_path",
    "chunk_id",
    "parent_chunk_id",
    "version",
    "checksum",
    "checksum_algorithm",
    "status",
    "updated_at",
    "tenant_id",
    "category",
    "doc_version",
    "doc_type",
    "chunk_type",
    "title",
}

_SOURCE_METADATA_FIELDS = (
    "source_id",
    "source_title",
    "source_type",
    "source_doc",
    "source_path",
    "source_pages",
    "chunk_id",
    "parent_chunk_id",
    "version",
    "checksum",
    "checksum_algorithm",
    "status",
    "updated_at",
    "tenant_id",
    "category",
    "doc_version",
    "doc_type",
    "chunk_type",
)

_CITATION_FIELDS = (
    "source_id",
    "source_title",
    "source_type",
    "source_doc",
    "source_pages",
    "chunk_id",
    "parent_chunk_id",
    "title",
    "version",
    "status",
    "updated_at",
    "tenant_id",
    "doc_version",
    "doc_type",
    "chunk_type",
)


def bounded_safe_string(value: Any, max_chars: int = _MAX_STRING_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    if max_chars <= len(_TRUNCATED_SUFFIX):
        return text[:max_chars]
    return text[: max_chars - len(_TRUNCATED_SUFFIX)] + _TRUNCATED_SUFFIX


def normalize_source_pages(value: Any) -> list[int]:
    pages: list[int] = []
    for item in _page_items(value):
        if isinstance(item, bool):
            continue
        try:
            text = str(item).strip()
            if not text:
                continue
            pages.append(int(text))
        except Exception:
            continue
    return pages


def build_source_metadata_from_manifest_record(record: dict) -> dict:
    if not isinstance(record, dict):
        return {}
    raw = {
        "source_id": record.get("source_id"),
        "source_title": record.get("source_title"),
        "source_type": record.get("source_type"),
        "source_path": record.get("source_path"),
        "source_doc": record.get("source_doc") or _basename(record.get("source_path")),
        "source_pages": record.get("source_pages"),
        "version": record.get("version"),
        "checksum": record.get("checksum"),
        "checksum_algorithm": record.get("checksum_algorithm"),
        "status": record.get("status"),
        "updated_at": record.get("updated_at"),
        "tenant_id": record.get("tenant_id"),
        "category": record.get("category"),
        "doc_version": record.get("doc_version"),
        "doc_type": record.get("doc_type"),
        "chunk_type": record.get("chunk_type"),
    }
    return _normalize_known_fields(raw, _SOURCE_METADATA_FIELDS)


def normalize_source_metadata(raw: dict | None, manifest_record: dict | None = None) -> dict:
    base = build_source_metadata_from_manifest_record(manifest_record or {})
    explicit = _normalize_known_fields(raw or {}, _SOURCE_METADATA_FIELDS)
    return merge_source_metadata(base, explicit)


def normalize_citation(raw: dict | None, manifest_record: dict | None = None) -> dict:
    base = build_source_metadata_from_manifest_record(manifest_record or {})
    explicit = _normalize_known_fields(raw or {}, _CITATION_FIELDS)
    merged = merge_source_metadata(base, explicit)
    return {key: merged[key] for key in _CITATION_FIELDS if key in merged}


def merge_source_metadata(base: dict | None, extra: dict | None) -> dict:
    merged: Dict[str, Any] = {}
    for payload in (base or {}, extra or {}):
        if not isinstance(payload, dict):
            continue
        normalized = _normalize_known_fields(payload, _SOURCE_METADATA_FIELDS + ("title",))
        for key, value in normalized.items():
            merged[key] = value
    return merged


def _normalize_known_fields(raw: dict, allowed_fields: Iterable[str]) -> dict:
    if not isinstance(raw, dict):
        return {}
    allowed = set(allowed_fields)
    out: Dict[str, Any] = {}
    for key in allowed:
        if key not in raw:
            continue
        value = raw.get(key)
        if key == "source_pages":
            out[key] = normalize_source_pages(value)
            continue
        if key in _STRING_FIELDS:
            if isinstance(value, (dict, list, tuple, set)):
                continue
            text = bounded_safe_string(value)
            if text:
                out[key] = text
    return out


def _page_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return parsed
        if "," in text:
            return [part.strip() for part in text.split(",")]
        return [text]
    return [value]


def _basename(value: Any) -> str:
    text = bounded_safe_string(value)
    if not text:
        return ""
    if "://" in text:
        return text.rsplit("/", 1)[-1]
    return Path(text).name
