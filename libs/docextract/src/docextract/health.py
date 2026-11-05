"""What is installed, which versions, which licenses (python -m docextract check)."""

from __future__ import annotations

import importlib.metadata
from typing import Any, Dict, List

from docextract import images, ocr, tables
from docextract._util import env_flag

# License notes are a summary for operators; the distribution's own license
# files are authoritative and must be checked before shipping a product.
LICENSES = {
    "docling": "MIT (models: see the model cards)",
    "PyMuPDF": "AGPL-3.0 or Artifex commercial license (not used by docextract)",
    "pdfplumber": "MIT",
    "pypdfium2": "Apache-2.0 / BSD-3-Clause",
    "paddleocr": "Apache-2.0",
    "paddlepaddle": "Apache-2.0",
    "pytesseract": "Apache-2.0 (Tesseract: Apache-2.0)",
    "yomitoku": "CC BY-NC-SA 4.0 (commercial license required for business use)",
    "Pillow": "MIT-CMU (HPND)",
}


def _version(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def report() -> Dict[str, Any]:
    packages = {
        name: {"version": _version(dist), "license": LICENSES.get(name, "")}
        for name, dist in (
            ("docling", "docling"),
            ("PyMuPDF", "PyMuPDF"),
            ("pdfplumber", "pdfplumber"),
            ("pypdfium2", "pypdfium2"),
            ("paddleocr", "paddleocr"),
            ("paddlepaddle", "paddlepaddle"),
            ("pytesseract", "pytesseract"),
            ("yomitoku", "yomitoku"),
            ("Pillow", "Pillow"),
        )
    }
    langs = sorted(ocr.tesseract_languages())
    warnings: List[str] = []
    try:
        table_backend = tables.resolve_backend()
    except tables.BackendUnavailable as exc:
        table_backend = None
        warnings.append(str(exc))
    if packages["PyMuPDF"]["version"]:
        warnings.append("PyMuPDF (AGPL-3.0) is installed but not used: remove it from the product image")
    if not packages["pdfplumber"]["version"] or not packages["pypdfium2"]["version"]:
        warnings.append("pdfplumber and pypdfium2 are required for PDF text and rendering (libs/docextract[light])")
    if packages["yomitoku"]["version"] and not env_flag("DOCEXTRACT_YOMITOKU_LICENSED"):
        warnings.append("yomitoku is installed but disabled (no commercial license flag)")
    if langs and "jpn" not in langs:
        warnings.append("tesseract has no jpn model (install tesseract-ocr-jpn)")
    if "jpn" in langs and "jpn_vert" not in langs:
        warnings.append("tesseract has no jpn_vert model: vertical text is read as horizontal")
    configured_ocr = ocr.configured_engines()
    if not ocr.ocr_available(configured_ocr):
        warnings.append(f"no configured OCR engine is installed ({','.join(configured_ocr)})")
    tool = images.converter()
    if tool is None:
        warnings.append("no EMF/WMF converter (LibreOffice or Inkscape): Office vector images are skipped")
    return {
        "table_backend": table_backend,
        "table_backends_installed": [b for b in ("docling", "pdfplumber", "paddle", "yomitoku") if tables.backend_available(b)],
        "paddle_ocr_version": ocr.paddle_ocr_version(),
        "ocr_engines_configured": configured_ocr,
        "ocr_engines_installed": ocr.available_engines(),
        "tesseract_languages": langs,
        "vector_image_converter": tool[0] if tool else None,
        "packages": packages,
        "warnings": warnings,
    }
