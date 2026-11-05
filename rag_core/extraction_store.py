"""Per-line OCR output of scanned pages, saved for review (step 6-1)."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import config
from rag_core.source_registry import allowed_roots

SCHEMA = 1
DEFAULT_REVIEW_MAX_CONFIDENCE = 80.0
_TENANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ExtractionError(ValueError):
    pass


def store_dir() -> Path:
    raw = str(os.getenv("EXTRACTION_STORE_DIR", "")).strip()
    return Path(raw) if raw else Path(config.BASE_DIR) / "data" / "extraction"


def enabled() -> bool:
    return str(os.getenv("EXTRACTION_STORE_ENABLED", "")).strip().lower() not in {"0", "false", "no", "off"}


def review_max_confidence() -> float:
    try:
        return float(os.getenv("EXTRACTION_REVIEW_MAX_CONFIDENCE", "") or DEFAULT_REVIEW_MAX_CONFIDENCE)
    except ValueError:
        return DEFAULT_REVIEW_MAX_CONFIDENCE


def _tenant(tenant_id: Any) -> str:
    if not isinstance(tenant_id, str) or not _TENANT_RE.fullmatch(tenant_id):
        raise ExtractionError("invalid tenant id")
    return tenant_id


def doc_key(tenant_id: str, source_doc: str) -> str:
    return hashlib.sha256(f"{_tenant(tenant_id)}\0{source_doc}".encode("utf-8")).hexdigest()[:24]


def record_path(tenant_id: str, source_doc: str) -> Path:
    return store_dir() / _tenant(tenant_id) / f"{doc_key(tenant_id, source_doc)}.json"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _inside_allowed_roots(path: Path) -> bool:
    resolved = path.resolve()
    return any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots())


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_record(
    tenant_id: str, source_doc: str, file_sha256: str, ocr_pages: Mapping[Any, Mapping[str, Any]],
    *, file_path: Optional[Path] = None, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """The record of a document from the converter's per-page OCR output (see pdf_to_canonical_jsonl._capture_lines)."""
    limit = review_max_confidence()
    pages: List[Dict[str, Any]] = []
    for page_num in sorted(int(p) for p in ocr_pages):
        captured = ocr_pages[page_num] if page_num in ocr_pages else ocr_pages[str(page_num)]
        lines = []
        for index, line in enumerate(captured.get("lines") or [], start=1):
            confidence = line.get("confidence")
            lines.append({"id": f"p{page_num}-l{index}", "text": str(line.get("text") or ""),
                          "confidence": None if confidence is None else float(confidence), "bbox": line.get("bbox")})
        page_conf = captured.get("confidence")
        weak = page_conf is None or float(page_conf) < limit or any(l["confidence"] is None or l["confidence"] < limit for l in lines)
        pages.append({
            "page": page_num, "dpi": captured.get("dpi"), "engine": captured.get("engine") or "",
            "width_px": captured.get("width_px"), "height_px": captured.get("height_px"),
            "confidence": None if page_conf is None else float(page_conf), "needs_review": bool(weak), "lines": lines,
        })
    usable = file_path is not None and Path(file_path).is_file() and _inside_allowed_roots(Path(file_path))
    return {
        "schema": SCHEMA, "tenant_id": _tenant(tenant_id), "source_doc": source_doc, "file_sha256": file_sha256,
        "saved_at": (now or datetime.now(timezone.utc)).isoformat(),
        "file_path": str(Path(file_path).resolve()) if usable else None, "pages": pages,
    }


def save_record(record: Dict[str, Any]) -> Path:
    path = record_path(record["tenant_id"], record["source_doc"])
    _write_json(path, record)
    return path


def load_record(tenant_id: str, source_doc: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(record_path(tenant_id, source_doc).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ExtractionError("the extraction record is unreadable") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ExtractionError("the extraction record has an unsupported format")
    return data


def save_from_report(tenant_id: str, source_doc: str, file_path: Path, ocr_pages: Mapping[Any, Mapping[str, Any]]) -> bool:
    """Called by the PDF adapter after a conversion. Never raises: the store must not fail an ingestion."""
    if not enabled() or not ocr_pages:
        return False
    try:
        record = build_record(tenant_id, source_doc, _sha256_of(Path(file_path)), ocr_pages, file_path=Path(file_path))
        save_record(record)
        return True
    except Exception:  # noqa: BLE001
        return False


def render_page_png(record: Mapping[str, Any], page_number: int) -> bytes:
    """The page as a PNG at the OCR resolution (the pixel space of the line boxes). ExtractionError when it cannot be."""
    path = Path(record.get("file_path") or "")
    entry = next((p for p in record["pages"] if p["page"] == page_number), None)
    if entry is None or not record.get("file_path") or not path.is_file() or not _inside_allowed_roots(path):
        raise ExtractionError("the page preview is not available")
    try:
        import io

        import pypdfium2 as pdfium
    except ImportError:
        raise ExtractionError("the page preview is not available") from None
    dpi = min(int(entry.get("dpi") or 300), 300)
    try:
        document = pdfium.PdfDocument(str(path))
        try:
            image = document[page_number - 1].render(scale=dpi / 72).to_pil()
        finally:
            document.close()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    except Exception:  # noqa: BLE001
        raise ExtractionError("the page preview is not available") from None
    return buffer.getvalue()
