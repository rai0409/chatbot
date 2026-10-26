"""Adapter around the PDF converter (scripts/pdf_to_canonical_jsonl).

Calls convert_pdf_to_rows() directly (no patched argv, no temporary file),
then annotates source_type/parser. The converter applies the same page-level
preprocessing (header/footer removal) and quantity metadata as the CLI, so
web and CLI ingestion produce identical rows.

Stage 15: scanned (image-only) pages are OCRed here too. INGEST_PDF_OCR:
  auto (default)  OCR pages without a text layer when an OCR engine
                  (Tesseract with jpn, or PaddleOCR) is installed
  on              always; fails when no OCR engine is installed
  off             never (as before)
A PDF that yields no text because it needs OCR and none ran fails with
OcrRequired, so the ingestion job says why instead of "no chunks".
PDF chunks keep their own chunk_role parent/child structure.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List


class OcrRequired(ValueError):
    """The PDF has pages without a text layer and no OCR ran."""


def ocr_mode() -> bool:
    """Whether to OCR pages without text (INGEST_PDF_OCR)."""
    mode = str(os.getenv("INGEST_PDF_OCR", "") or "auto").strip().lower()
    if mode in {"0", "false", "no", "off"}:
        return False
    if mode in {"1", "true", "yes", "on"}:
        return True
    from rag_core import extraction

    try:
        return bool(extraction.load().ocr_available())
    except Exception:  # noqa: BLE001 - docextract missing: no OCR
        return False


def convert_pdf_with_report(
    path: str | Path,
    *,
    tenant_id: str = "default",
    source_doc: str | None = None,
    doc_type: str | None = None,
) -> tuple[List[Dict], Dict]:
    # Lazy import: pulls in config/chunking (and pdfplumber); must not run at
    # package import time.
    from scripts import pdf_to_canonical_jsonl

    rows, report = pdf_to_canonical_jsonl.convert_pdf_to_rows(
        Path(path),
        doc_type=doc_type or "policy",
        tenant_id=tenant_id or "default",
        ocr=ocr_mode(),
        source_doc=source_doc,
    )
    if not rows and report.get("pages_low_text_without_ocr"):
        raise OcrRequired(
            f"{len(report['pages_low_text_without_ocr'])} page(s) without a text layer and no OCR "
            "(install Tesseract with jpn or PaddleOCR; INGEST_PDF_OCR)"
        )
    for row in rows:
        row.setdefault("source_type", "pdf")
        row.setdefault("parser", "pdf_to_canonical_jsonl")
    return rows, report


def convert_pdf(
    path: str | Path,
    *,
    tenant_id: str = "default",
    source_doc: str | None = None,
    doc_type: str | None = None,
) -> List[Dict]:
    rows, _report = convert_pdf_with_report(path, tenant_id=tenant_id, source_doc=source_doc, doc_type=doc_type)
    return rows
