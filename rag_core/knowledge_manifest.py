from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


VALID_SOURCE_TYPES = {"pdf", "approved_qa", "index_jsonl", "eval_case", "other"}
VALID_STATUSES = {"active", "deprecated", "archived"}
DEFAULT_MANIFEST_VERSION = "1"
DEFAULT_CHECKSUM_ALGORITHM = "sha256"
_MAX_TEXT_CHARS = 500


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def bounded_text(value: Any, max_chars: int = _MAX_TEXT_CHARS) -> str:
    text = "" if value is None else str(value)
    text = text.strip()
    if len(text) <= max_chars:
        return text
    suffix = "...[truncated]"
    if max_chars <= len(suffix):
        return text[:max_chars]
    return text[: max_chars - len(suffix)] + suffix


def compute_file_checksum(path: str | Path, algorithm: str = DEFAULT_CHECKSUM_ALGORITHM) -> str:
    if algorithm != "sha256":
        raise ValueError("unsupported checksum algorithm")
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_source_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "source_id": bounded_text(record.get("source_id")),
        "tenant_id": bounded_text(record.get("tenant_id") or "default"),
        "source_type": bounded_text(record.get("source_type") or "other"),
        "source_title": bounded_text(record.get("source_title") or Path(str(record.get("source_path") or "")).name),
        "source_path": bounded_text(record.get("source_path")),
        "version": bounded_text(record.get("version") or "1"),
        "checksum": bounded_text(record.get("checksum")),
        "checksum_algorithm": bounded_text(record.get("checksum_algorithm") or DEFAULT_CHECKSUM_ALGORITHM),
        "status": bounded_text(record.get("status") or "active"),
        "indexed_at": record.get("indexed_at"),
        "updated_at": record.get("updated_at"),
        "category": bounded_text(record.get("category")) if record.get("category") is not None else None,
        "metadata": deepcopy(record.get("metadata")) if isinstance(record.get("metadata"), dict) else {},
    }
    if normalized["indexed_at"] is not None:
        normalized["indexed_at"] = bounded_text(normalized["indexed_at"])
    if normalized["updated_at"] is not None:
        normalized["updated_at"] = bounded_text(normalized["updated_at"])
    return normalized


def validate_source_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("source_id", "source_path", "source_type", "status"):
        if not str(record.get(field) or "").strip():
            errors.append(f"missing_{field}")
    if record.get("source_type") and record.get("source_type") not in VALID_SOURCE_TYPES:
        errors.append("invalid_source_type")
    if record.get("status") and record.get("status") not in VALID_STATUSES:
        errors.append("invalid_status")
    if record.get("checksum_algorithm") and record.get("checksum_algorithm") != DEFAULT_CHECKSUM_ALGORITHM:
        errors.append("invalid_checksum_algorithm")
    if record.get("metadata") is not None and not isinstance(record.get("metadata"), dict):
        errors.append("invalid_metadata")
    return errors


def build_manifest(records: Sequence[dict[str, Any]], warnings: Sequence[str] | None = None) -> dict[str, Any]:
    normalized = [normalize_source_record(record) for record in records]
    return {
        "manifest_version": DEFAULT_MANIFEST_VERSION,
        "generated_at": utc_now_iso(),
        "records": normalized,
        "warnings": list(warnings or []),
    }


def load_manifest(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    payload.setdefault("manifest_version", DEFAULT_MANIFEST_VERSION)
    payload.setdefault("generated_at", None)
    payload.setdefault("records", [])
    payload.setdefault("warnings", [])
    if not isinstance(payload["records"], list):
        raise ValueError("manifest records must be a list")
    payload["records"] = [normalize_source_record(record) for record in payload["records"] if isinstance(record, dict)]
    if not isinstance(payload["warnings"], list):
        payload["warnings"] = []
    return payload


def save_manifest(manifest: dict[str, Any], path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "manifest_version": str(manifest.get("manifest_version") or DEFAULT_MANIFEST_VERSION),
        "generated_at": manifest.get("generated_at") or utc_now_iso(),
        "records": [normalize_source_record(record) for record in manifest.get("records", []) if isinstance(record, dict)],
        "warnings": list(manifest.get("warnings") or []),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def find_record_by_source_id(manifest: dict[str, Any], source_id: str) -> dict[str, Any] | None:
    for record in manifest.get("records", []):
        if record.get("source_id") == source_id:
            return record
    return None


def filter_records(
    records: Iterable[dict[str, Any]],
    *,
    tenant_id: str | None = None,
    status: str | None = None,
    source_type: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records:
        if tenant_id is not None and record.get("tenant_id") != tenant_id:
            continue
        if status is not None and record.get("status") != status:
            continue
        if source_type is not None and record.get("source_type") != source_type:
            continue
        if category is not None and record.get("category") != category:
            continue
        out.append(record)
    return out


def duplicate_source_ids(records: Iterable[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for record in records:
        source_id = str(record.get("source_id") or "")
        if not source_id:
            continue
        if source_id in seen:
            dupes.add(source_id)
        seen.add(source_id)
    return sorted(dupes)


def missing_local_files(records: Iterable[dict[str, Any]], root_dir: str | Path = ".") -> list[str]:
    root = Path(root_dir)
    missing: list[str] = []
    for record in records:
        path = _local_path(root, record.get("source_path"))
        if path is not None and not path.exists():
            missing.append(str(record.get("source_id") or record.get("source_path")))
    return missing


def checksum_mismatches(records: Iterable[dict[str, Any]], root_dir: str | Path = ".") -> list[str]:
    root = Path(root_dir)
    mismatches: list[str] = []
    for record in records:
        path = _local_path(root, record.get("source_path"))
        expected = str(record.get("checksum") or "")
        if path is None or not path.exists() or not expected:
            continue
        try:
            actual = compute_file_checksum(path, str(record.get("checksum_algorithm") or DEFAULT_CHECKSUM_ALGORITHM))
        except Exception:
            mismatches.append(str(record.get("source_id") or record.get("source_path")))
            continue
        if actual != expected:
            mismatches.append(str(record.get("source_id") or record.get("source_path")))
    return mismatches


def _local_path(root: Path, source_path: Any) -> Path | None:
    raw = str(source_path or "").strip()
    if not raw or "://" in raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else root / path
