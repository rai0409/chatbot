"""PDF table extraction with interchangeable backends (no PyMuPDF).

Backends (DOCEXTRACT_TABLE_BACKEND, default "auto" = first installed one of
DOCEXTRACT_TABLE_PREFERENCE, default "docling,pdfplumber"):

  docling     IBM Docling + TableFormer (ACCURATE). MIT. Best structure
              recovery (borderless tables, merged cells, scanned pages when
              DOCEXTRACT_DOCLING_OCR is set). Heavy (PyTorch, ~500 MB models),
              slow on CPU.
  pdfplumber  pdfplumber (MIT, pdfminer.six): ruled tables ("lines"), plus
              borderless tables from text alignment ("text") that pass a
              plausibility check. Light; the default when Docling is absent.
  paddle      PaddleOCR PP-StructureV3 (Apache-2.0): layout + table
              recognition on page images, also for scans. No table position
              is returned, so the table text stays in the page text too.
  yomitoku    YomiToku document analyzer (Japanese). CC BY-NC-SA 4.0: needs
              the commercial license (DOCEXTRACT_YOMITOKU_LICENSED=true).

PyMuPDF (AGPL-3.0) is not used: what it did here is done by pdfplumber, and
page images come from pypdfium2 (Apache-2.0 / BSD-3-Clause).

Every backend returns models.Table with rows already cleaned (text cells,
no empty rows/columns, merged header cells repeated). Tables continued on
the next page are joined by merge_continued_tables.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from docextract._util import (
    clean_grid,
    detect_header_rows,
    env_flag,
    env_float,
    env_list,
    env_str,
    fill_merged_header,
    html_tables,
    plausible_borderless,
)
from docextract.models import Table, TableRegion

DEFAULT_PREFERENCE = "docling,pdfplumber"
_MIN_ROWS = 2
_MIN_COLS = 2


class BackendUnavailable(RuntimeError):
    pass


def _importable(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def backend_available(name: str) -> bool:
    return {
        "docling": lambda: _importable("docling"),
        "pdfplumber": lambda: _importable("pdfplumber"),
        "paddle": lambda: _importable("paddleocr") and _importable("paddle"),
        "yomitoku": lambda: _importable("yomitoku") and env_flag("DOCEXTRACT_YOMITOKU_LICENSED"),
    }.get(name, lambda: False)()


def resolve_backend(requested: Optional[str] = None) -> Optional[str]:
    name = (requested or env_str("DOCEXTRACT_TABLE_BACKEND", "auto")).lower()
    if name in ("none", "off", "false"):
        return None
    if name != "auto":
        if not backend_available(name):
            raise BackendUnavailable(f"table backend {name} is not installed")
        return name
    for candidate in env_list("DOCEXTRACT_TABLE_PREFERENCE", DEFAULT_PREFERENCE):
        if backend_available(candidate):
            return candidate
    return None


def _make_table(rows: Sequence[Sequence[Any]], page: int, bbox, backend: str, confidence: float) -> Optional[Table]:
    grid = clean_grid(rows)
    if len(grid) < _MIN_ROWS or (max((len(r) for r in grid), default=0)) < _MIN_COLS:
        return None
    grid = fill_merged_header(grid)
    bbox = tuple(float(v) for v in bbox) if bbox else None
    return Table(
        rows=grid,
        regions=[TableRegion(page=page, bbox=bbox)],
        backend=backend,
        confidence=confidence,
        header_rows=detect_header_rows(grid),
    )


def _borderless_enabled() -> bool:
    return env_flag("DOCEXTRACT_BORDERLESS_TABLES", True)


def _min_confidence() -> float:
    return env_float("DOCEXTRACT_TABLE_MIN_CONFIDENCE", 0.65)


# --- pdfplumber -------------------------------------------------------------------------------


def pdfplumber_page_tables(page, page_number: int = 0, *, borderless: Optional[bool] = None) -> List[Table]:
    """Tables of one pdfplumber page: ruled tables first; borderless ones
    (text alignment) only when the page has no ruled table."""
    borderless = _borderless_enabled() if borderless is None else borderless
    out: List[Table] = []
    try:
        found = page.find_tables() or []
    except Exception:  # noqa: BLE001 - a broken page must not stop the document
        return []
    for table in found:
        try:
            made = _make_table(table.extract(), page_number, table.bbox, "pdfplumber:lines", 0.9)
        except Exception:  # noqa: BLE001
            made = None
        if made:
            out.append(made)
    if out or not borderless:
        return out
    try:
        found = page.find_tables(table_settings={"vertical_strategy": "text", "horizontal_strategy": "text"}) or []
    except Exception:  # noqa: BLE001
        return []
    for table in found:
        try:
            grid = clean_grid(table.extract())
        except Exception:  # noqa: BLE001
            continue
        score = plausible_borderless(grid)
        if score >= _min_confidence():
            made = _make_table(grid, page_number, table.bbox, "pdfplumber:text", score)
            if made:
                out.append(made)
    return out


def _pdfplumber(path: str, pages: Optional[Iterable[int]]) -> List[Table]:
    import pdfplumber

    wanted = set(pages) if pages else None
    out: List[Table] = []
    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages):
            number = index + 1
            if wanted is None or number in wanted:
                out.extend(pdfplumber_page_tables(page, number))
            page.close()
    return out


# --- Docling ------------------------------------------------------------------------------------

_CACHE: Dict[str, Any] = {}


def _docling_converter():
    if "converter" in _CACHE:
        return _CACHE["converter"]
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_table_structure = True
    options.table_structure_options.mode = (
        TableFormerMode.FAST if env_str("DOCEXTRACT_DOCLING_MODE") == "fast" else TableFormerMode.ACCURATE
    )
    options.table_structure_options.do_cell_matching = True
    ocr = env_str("DOCEXTRACT_DOCLING_OCR", "none").lower()
    options.do_ocr = ocr != "none"
    if ocr == "tesseract":
        from docling.datamodel.pipeline_options import TesseractCliOcrOptions

        options.ocr_options = TesseractCliOcrOptions(lang=["jpn", "eng"])
    elif ocr == "easyocr":
        from docling.datamodel.pipeline_options import EasyOcrOptions

        options.ocr_options = EasyOcrOptions(lang=["ja", "en"])
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    _CACHE["converter"] = converter
    return converter


def _docling(path: str, pages: Optional[Iterable[int]]) -> List[Table]:
    wanted = set(pages) if pages else None
    document = _docling_converter().convert(path).document
    out: List[Table] = []
    for item in getattr(document, "tables", None) or []:
        prov = (getattr(item, "prov", None) or [None])[0]
        number = int(getattr(prov, "page_no", 0) or 0)
        if wanted is not None and number not in wanted:
            continue
        try:
            frame = item.export_to_dataframe(doc=document)
        except TypeError:  # older docling: no doc argument
            frame = item.export_to_dataframe()
        header = [str(c) for c in frame.columns]
        rows = [header] + [[str(v) for v in row] for row in frame.astype(str).values.tolist()]
        if all(h.isdigit() for h in header):  # no header detected: pandas numbered the columns
            rows = rows[1:]
        bbox = None
        if prov is not None and getattr(prov, "bbox", None) is not None:
            try:
                page_height = float(document.pages[number].size.height)
                box = prov.bbox.to_top_left_origin(page_height=page_height)
                bbox = (box.l, box.t, box.r, box.b)
            except Exception:  # noqa: BLE001 - position is optional
                bbox = None
        made = _make_table(rows, number, bbox, "docling", 0.95)
        if made:
            out.append(made)
    return out


# --- page-image backends (PaddleOCR PP-StructureV3, YomiToku) -----------------------------------


def _page_images(path: str, pages: Optional[Iterable[int]], dpi: int):
    """(page number, BGR numpy array) rendered with pypdfium2."""
    import numpy as np
    import pypdfium2 as pdfium

    wanted = set(pages) if pages else None
    pdf = pdfium.PdfDocument(path)
    try:
        for index in range(len(pdf)):
            if wanted is None or index + 1 in wanted:
                image = pdf[index].render(scale=dpi / 72.0).to_pil().convert("RGB")
                yield index + 1, np.array(image)[:, :, ::-1].copy()
    finally:
        pdf.close()


def _paddle(path: str, pages: Optional[Iterable[int]]) -> List[Table]:
    from paddleocr import PPStructureV3

    if "paddle" not in _CACHE:
        _CACHE["paddle"] = PPStructureV3(lang=env_str("DOCEXTRACT_PADDLE_LANG", "japan"))
    pipeline = _CACHE["paddle"]
    out: List[Table] = []
    for number, array in _page_images(path, pages, int(env_float("DOCEXTRACT_RENDER_DPI", 200))):
        for res in pipeline.predict(array) or []:
            data = res.json.get("res", res.json) if hasattr(res, "json") and isinstance(res.json, dict) else res
            for table in data.get("table_res_list") or []:
                for grid in html_tables(str(table.get("pred_html") or "")):
                    made = _make_table(grid, number, None, "paddle", 0.85)
                    if made:
                        out.append(made)
    return out


def _yomitoku(path: str, pages: Optional[Iterable[int]]) -> List[Table]:
    from yomitoku import DocumentAnalyzer

    if "yomitoku" not in _CACHE:
        _CACHE["yomitoku"] = DocumentAnalyzer(device=env_str("DOCEXTRACT_DEVICE", "cpu"), visualize=False)
    analyzer = _CACHE["yomitoku"]
    dpi = int(env_float("DOCEXTRACT_RENDER_DPI", 200))
    scale = 72.0 / dpi
    out: List[Table] = []
    for number, array in _page_images(path, pages, dpi):
        output = analyzer(array)
        results = output[0] if isinstance(output, tuple) else output
        for table in getattr(results, "tables", None) or []:
            grid: Dict[tuple, str] = {}
            for cell in getattr(table, "cells", None) or []:
                row, col = int(cell.row) - 1, int(cell.col) - 1
                for dr in range(int(getattr(cell, "row_span", 1) or 1)):
                    for dc in range(int(getattr(cell, "col_span", 1) or 1)):
                        grid[(row + dr, col + dc)] = str(getattr(cell, "contents", "") or "")
            if not grid:
                continue
            n_rows = max(k[0] for k in grid) + 1
            n_cols = max(k[1] for k in grid) + 1
            rows = [[grid.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)]
            box = getattr(table, "box", None)
            bbox = tuple(float(v) * scale for v in box[:4]) if box is not None and len(box) >= 4 else None
            made = _make_table(rows, number, bbox, "yomitoku", 0.9)
            if made:
                out.append(made)
    return out


_BACKENDS = {
    "docling": _docling,
    "pdfplumber": _pdfplumber,
    "paddle": _paddle,
    "yomitoku": _yomitoku,
}


# --- cross-page continuation -----------------------------------------------------------------


def merge_continued_tables(tables: List[Table], page_heights: Dict[int, float]) -> List[Table]:
    """Join a table that ends at the bottom of page N with the table that
    starts at the top of page N+1 when both have the same column count; a
    repeated header on the second page is dropped."""
    if not env_flag("DOCEXTRACT_MERGE_CONTINUED_TABLES", True):
        return tables
    ordered = sorted(tables, key=lambda t: (t.page, (t.regions[0].bbox or (0, 0, 0, 0))[1]))
    merged: List[Table] = []
    for table in ordered:
        prev = merged[-1] if merged else None
        if prev is not None and _continues(prev, table, page_heights):
            rows = table.rows
            if rows and prev.rows and rows[0] == prev.rows[0]:
                rows = rows[1:]  # header repeated on the new page
            prev.rows.extend(rows)
            prev.regions.extend(table.regions)
            prev.confidence = min(prev.confidence, table.confidence)
            continue
        merged.append(table)
    return merged


def _continues(prev: Table, table: Table, page_heights: Dict[int, float]) -> bool:
    last, first = prev.regions[-1], table.regions[0]
    if first.page != last.page + 1 or prev.n_cols != table.n_cols:
        return False
    if last.bbox is None or first.bbox is None:
        return False
    last_height = page_heights.get(last.page) or 0
    first_height = page_heights.get(first.page) or 0
    if not last_height or not first_height:
        return False
    return last.bbox[3] >= 0.75 * last_height and first.bbox[1] <= 0.25 * first_height


def _page_heights(path: str) -> Dict[int, float]:
    try:
        if _importable("pypdfium2"):
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(path)
            try:
                return {i + 1: float(pdf[i].get_height()) for i in range(len(pdf))}
            finally:
                pdf.close()
        if _importable("pdfplumber"):
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                return {i + 1: float(page.height) for i, page in enumerate(pdf.pages)}
    except Exception:  # noqa: BLE001
        return {}
    return {}


def extract_pdf_tables(
    path: str,
    *,
    backend: Optional[str] = None,
    pages: Optional[Iterable[int]] = None,
    fallback: bool = True,
) -> Dict[str, Any]:
    """{"backend", "tables": [Table], "errors": [...]} for one PDF.

    With fallback=True, a backend that fails at run time (missing model,
    broken page) falls back to the next installed one in the preference.
    """
    errors: List[str] = []
    first = resolve_backend(backend)
    if first is None:
        return {"backend": None, "tables": [], "errors": ["no table backend installed or tables disabled"]}
    candidates = [first]
    if fallback:
        candidates += [b for b in env_list("DOCEXTRACT_TABLE_PREFERENCE", DEFAULT_PREFERENCE) if b != first and backend_available(b)]
    for name in candidates:
        try:
            tables = _BACKENDS[name](str(path), pages)
        except Exception as exc:  # noqa: BLE001 - try the next backend
            errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:200]}")
            continue
        tables = merge_continued_tables(tables, _page_heights(str(path)))
        return {"backend": name, "tables": tables, "errors": errors}
    return {"backend": None, "tables": [], "errors": errors}
