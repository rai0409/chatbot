"""docextract: table extraction and Japanese OCR behind one small interface.

No hard dependency: every backend is optional and detected at run time, so
the package can be dropped into another project and used with whatever is
installed there.

    from docextract import extract_pdf_tables, ocr_image
    found = extract_pdf_tables("manual.pdf")          # {"backend", "tables", "errors"}
    for table in found["tables"]:
        print(table.pages, table.header(), table.body()[:3])
    result = ocr_image(png_bytes)                      # OcrResult(text, confidence, engine)
"""

from docextract.images import load_image
from docextract.models import OcrLine, OcrResult, Table, TableRegion
from docextract.ocr import available_engines, ocr_available, ocr_image
from docextract.pdf import PdfUnavailable, open_pdf
from docextract.tables import (
    BackendUnavailable,
    extract_pdf_tables,
    merge_continued_tables,
    pdfplumber_page_tables,
    resolve_backend,
)

__version__ = "1.1.0"

__all__ = [
    "BackendUnavailable",
    "OcrLine",
    "OcrResult",
    "Table",
    "TableRegion",
    "available_engines",
    "extract_pdf_tables",
    "load_image",
    "merge_continued_tables",
    "ocr_available",
    "ocr_image",
    "open_pdf",
    "pdfplumber_page_tables",
    "PdfUnavailable",
    "resolve_backend",
]
