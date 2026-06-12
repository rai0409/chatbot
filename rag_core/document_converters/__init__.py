"""Multi-format document → canonical chunk converters (Prompt018).

Supported: csv, xlsx, docx, pptx (stdlib zip/xml parsers — no optional
dependencies) and pdf (adapter around the existing converter). Output rows
are compatible with the existing canonical JSONL ingest/eval pipeline.
Conversion never touches the vectorstore.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from rag_core.document_converters.csv_converter import convert_csv
from rag_core.document_converters.docx_converter import convert_docx
from rag_core.document_converters.pptx_converter import convert_pptx
from rag_core.document_converters.xlsx_converter import convert_xlsx

SUPPORTED_FORMATS = ("csv", "xlsx", "docx", "pptx", "pdf")

_CONVERTERS = {
    "csv": convert_csv,
    "xlsx": convert_xlsx,
    "docx": convert_docx,
    "pptx": convert_pptx,
}


def detect_format(path: str | Path) -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix in SUPPORTED_FORMATS:
        return suffix
    raise ValueError(
        f"unsupported document format: '.{suffix or '?'}' "
        f"(supported: {', '.join(SUPPORTED_FORMATS)})"
    )


def convert_file_to_canonical_chunks(
    path: str | Path,
    *,
    tenant_id: str = "default",
    source_doc: str | None = None,
    doc_type: str | None = None,
    source_format: str | None = None,
) -> List[Dict]:
    fmt = (source_format or "").strip().lower() or detect_format(path)
    if fmt == "pdf":
        # Lazy import: the PDF adapter pulls in config/fitz machinery.
        from rag_core.document_converters.pdf_adapter import convert_pdf

        return convert_pdf(path, tenant_id=tenant_id, source_doc=source_doc, doc_type=doc_type)
    converter = _CONVERTERS.get(fmt)
    if converter is None:
        raise ValueError(
            f"unsupported document format: '{fmt}' (supported: {', '.join(SUPPORTED_FORMATS)})"
        )
    return converter(path, tenant_id=tenant_id, source_doc=source_doc, doc_type=doc_type)
