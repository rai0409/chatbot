from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

import argparse
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import config
from rag_core import extraction
from rag_core.chunking_ja import build_ja_chunk_records
from rag_core.document_converters.base import table_rows_to_chunks
from rag_core.ingest_preprocess import PREPROCESS_VERSION, PreprocessOptions, preprocess_rows


BULLET_RE = re.compile(r"^(\s*([0-9]+[\.)]|[①-⑳]|[-•・※▶]))")
_JA_CHAR = r"[ぁ-んァ-ヴー一-龥々〆〤]"
_JA_EDGE_RE = re.compile(_JA_CHAR)
_OCR_JA_SPACE_RE = re.compile(rf"(?<={_JA_CHAR})[ 　]+(?={_JA_CHAR})")
# A line made only of numbers, units and separators ("100", "2024/4/1", "12,000円").
_NUMERIC_LINE_RE = re.compile(r"^[\d,.\-−+%/:()（）年月日円万億 ]+$")
# Two or more inner spaces or a tab usually mean table columns.
_TABLE_LINE_RE = re.compile(r"\S(?: {2,}|\t)\S|\|")
_SHORT_LINE_CHARS = 12
_SENTENCE_END = "。.!?！？"


def _is_numeric_line(line: str) -> bool:
    return bool(_NUMERIC_LINE_RE.fullmatch(line.strip()))


def _join_separator(prev: str, nxt: str) -> Optional[str]:
    """Separator used to join two physical lines, or None to keep them apart.

    Japanese on both sides joins without a space; anything else joins with a
    space so "100" and "200" never become "100200". Numeric, short and
    table-like lines are never joined.
    """
    if not prev or not nxt:
        return None
    if prev[-1] in _SENTENCE_END:
        return None
    if BULLET_RE.match(nxt):
        return None
    if _is_numeric_line(prev) or _is_numeric_line(nxt):
        return None
    if _TABLE_LINE_RE.search(prev) or _TABLE_LINE_RE.search(nxt):
        return None
    if len(prev) <= _SHORT_LINE_CHARS:
        return None
    if _JA_EDGE_RE.fullmatch(prev[-1]) and _JA_EDGE_RE.fullmatch(nxt[0]):
        return ""
    if prev.endswith("-") and len(prev) >= 2 and prev[-2].isascii() and prev[-2].isalpha() and nxt[0].isascii() and nxt[0].isalpha():
        return "\0"  # English word broken by a hyphen: drop the hyphen
    return " "


def _normalize_text(text: str, *, ocr: bool = False) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if ocr:
        # OCR inserts spaces between Japanese characters.
        text = _OCR_JA_SPACE_RE.sub("", text)
    out: List[str] = []
    buf = ""
    for raw in text.split("\n"):
        line = raw.strip()
        if line == "":
            # Blank lines are paragraph breaks; chunking splits on them.
            if buf:
                out.append(buf)
                buf = ""
            if out and out[-1] != "":
                out.append("")
            continue
        if not buf:
            buf = line
            continue
        sep = _join_separator(buf, line)
        if sep is None:
            out.append(buf)
            buf = line
        elif sep == "\0":
            buf = buf[:-1] + line
        else:
            buf = buf + sep + line
    if buf:
        out.append(buf)
    text = "\n".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def _is_garbled(text: str) -> bool:
    if not text:
        return False
    compact = re.sub(r"\s+", "", text)
    total = len(compact)
    if total == 0:
        return False
    replacement_ratio = compact.count("�") / total
    control_ratio = len(re.findall(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", text)) / max(1, len(text))
    letters = len(re.findall(r"[A-Za-z0-9ぁ-んァ-ン一-龥]", text))
    letter_ratio = letters / max(1, len(text))
    if replacement_ratio > 0.02 or control_ratio > 0.02:
        return True
    if letter_ratio < 0.1:
        return True
    return False


def _split_with_overlap(text: str, max_chars: int, overlap: int) -> Iterable[str]:
    if max_chars <= 0 or len(text) <= max_chars:
        yield text
        return
    overlap = max(0, min(overlap, max_chars - 1))
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + max_chars)
        chunk = text[start:end]
        if chunk.strip():
            yield chunk.strip()
        if end >= n:
            break
        start = max(0, end - overlap)


def _dx():
    # libs/docextract: table backends and OCR engines (stage 10).
    return extraction.load()


def _ensure_ocr_available() -> None:
    if not _dx().ocr_available():
        raise RuntimeError(
            "OCR requires an OCR engine (PaddleOCR, or Tesseract with the jpn model). "
            "Check with: python -m docextract check"
        )


def _render(page, dpi: int, clip=None):
    # pypdfium2 through docextract (PyMuPDF is no longer used: AGPL-3.0).
    return page.render(dpi=dpi, clip=clip)


def _tesseract_lang(lang: str) -> Optional[str]:
    # --ocr-lang still selects the Tesseract model; the default "jpn" lets
    # docextract use "jpn+eng" when the English model is installed too.
    return None if lang in ("", "jpn") else lang


def _ocr_page_result(page, dpi: int, lang: str):
    return _dx().ocr_image(_render(page, dpi), tesseract_lang=_tesseract_lang(lang))


def _ocr_page(page, dpi: int, lang: str) -> str:
    return _ocr_page_result(page, dpi, lang).text


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


def _page_tables(page, page_number: int = 0) -> List[Dict[str, Any]]:
    """Tables of one page as {"bbox", "rows"}: ruled tables first, then
    borderless ones that pass docextract's plausibility check (pdfplumber)."""
    raw = getattr(page, "raw", page)
    return [{"bbox": t.regions[0].bbox, "rows": t.rows} for t in _dx().pdfplumber_page_tables(raw, page_number)]


def _text_outside(page, boxes: Sequence[Tuple[float, float, float, float]]) -> str:
    # Page text without the characters inside a table (the table becomes
    # row chunks instead of a run of cell values).
    return page.text(exclude=boxes)


def _image_boxes(page, min_area: float) -> List[Tuple[float, float, float, float]]:
    # Images covering at least min_area of the page (icons and logos are skipped).
    return page.image_boxes(min_area)


def _ocr_regions_result(page, boxes, dpi: int, lang: str) -> Tuple[str, Optional[float]]:
    texts: List[str] = []
    confidences: List[float] = []
    for bbox in boxes:
        result = _dx().ocr_image(_render(page, dpi, clip=bbox), tesseract_lang=_tesseract_lang(lang))
        if result.ok:
            texts.append(result.text)
            if result.confidence is not None:
                confidences.append(result.confidence)
    mean = round(sum(confidences) / len(confidences), 1) if confidences else None
    return "\n\n".join(texts), mean


def _ocr_regions(page, boxes, dpi: int, lang: str) -> str:
    return _ocr_regions_result(page, boxes, dpi, lang)[0]


def _min_ocr_confidence() -> float:
    # Pages whose OCR confidence (0-100) is below this are reported and their
    # chunks get quality="low" (PDF_OCR_MIN_CONFIDENCE, default 60).
    return _env_float("PDF_OCR_MIN_CONFIDENCE", 60.0)


def _table_backend() -> Optional[str]:
    # PDF_TABLES=false turns tables off; otherwise docextract chooses
    # (DOCEXTRACT_TABLE_BACKEND, default auto: docling > pdfplumber).
    if not _env_flag("PDF_TABLES", True):
        return None
    return _dx().resolve_backend()


def _resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = config.BASE_DIR / p
    return p


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _add_production_metadata(
    rec: Dict[str, Any],
    *,
    tenant_id: str,
    doc_version: str,
    language: str,
    extraction_method: str,
    doc_checksum: str = "",
) -> Dict[str, Any]:
    rec["tenant_id"] = tenant_id
    rec["doc_version"] = doc_version
    rec["language"] = language
    rec["extraction_method"] = extraction_method
    if doc_checksum:
        rec["doc_checksum"] = doc_checksum
    return rec


def _coerce_unique_chunk_index(
    rec: Dict[str, Any],
    *,
    used_indices: Set[int],
    next_index: int,
) -> int:
    raw_index = rec.get("chunk_index")
    try:
        chunk_index = int(raw_index)
    except Exception:
        chunk_index = next_index

    if chunk_index in used_indices:
        chunk_index = next_index
        while chunk_index in used_indices:
            chunk_index += 1

    rec["chunk_index"] = chunk_index
    used_indices.add(chunk_index)
    return max(next_index, chunk_index + 1)


def _id_prefix(tenant_id: str) -> str:
    # Default-tenant ids keep their historical form; other tenants are
    # prefixed so the same file name in two tenants never shares an id.
    tenant = str(tenant_id or "").strip() or "default"
    return "" if tenant == "default" else f"{tenant}:"


def _extract_pages(
    pdf_path: Path,
    *,
    ocr: bool,
    ocr_lang: str,
    min_text_chars: int,
    dpi: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    dx = _dx()
    if ocr:
        _ensure_ocr_available()

    pages: List[Dict[str, Any]] = []
    report: Dict[str, Any] = {
        "pages_total": 0,
        "pages_text": 0,
        "pages_ocr": 0,
        "pages_skipped_empty": [],
        "pages_low_text_without_ocr": [],
        "pages_garbled": [],
        "pages_image_ocr": [],
        "pages_low_ocr_confidence": [],
        "pages_vertical": [],
        "ocr_confidence": {},
        "pdf_engine": "pdfplumber+pypdfium2",
        "table_backend": None,
        "table_errors": [],
    }
    # PDF_OCR_IMAGE_MIN_AREA (default 0.1, only with --ocr): pages that have
    # a text layer also get the OCR text of images covering that share of the page.
    image_min_area = _env_float("PDF_OCR_IMAGE_MIN_AREA", 0.1)
    min_confidence = _min_ocr_confidence()
    backend = _table_backend()
    report["table_backend"] = backend
    found_tables: List[Any] = []
    regions_by_page: Dict[int, List[Any]] = {}
    if backend and backend != "pdfplumber":
        # Whole-document backends (docling, paddle, yomitoku) run once; a
        # backend failing at run time falls back to the next one.
        result = dx.extract_pdf_tables(str(pdf_path), backend=backend)
        report["table_backend"] = result["backend"]
        report["table_errors"] = result["errors"]
        found_tables = list(result["tables"])
        for table in found_tables:
            for region in table.regions:
                regions_by_page.setdefault(region.page, []).append(region.bbox)
    page_heights: Dict[int, float] = {}
    try:
        opened = dx.open_pdf(pdf_path)
        doc = opened.__enter__()
    except dx.PdfUnavailable as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        report["pages_total"] = len(doc)
        for page in doc.pages:
            page_num = page.number
            try:
                page_heights[page_num] = page.height
                if backend == "pdfplumber":
                    page_found = page.tables()
                    found_tables.extend(page_found)
                    for table in page_found:
                        regions_by_page.setdefault(page_num, []).append(table.regions[0].bbox)
                has_tables = page_num in regions_by_page
                boxes = [b for b in regions_by_page.get(page_num, []) if b]
                if page.is_vertical():
                    # 縦書き: read top-to-bottom, right-to-left (docextract.pdf).
                    report["pages_vertical"].append(page_num)
                norm_text = _normalize_text(_text_outside(page, boxes) if boxes else page.text())
                garbled = _is_garbled(norm_text)
                # A page that is mostly a (located) table has little text outside
                # it; that is not a scanned page (OCR would duplicate the table).
                needs_ocr = (len(norm_text) < min_text_chars or garbled) and not boxes
                method = "text"
                confidence: Optional[float] = None
                if garbled:
                    report["pages_garbled"].append(page_num)
                if needs_ocr and ocr:
                    result = _ocr_page_result(page, dpi=dpi, lang=ocr_lang)
                    norm_text = _normalize_text(result.text, ocr=True)
                    confidence = result.confidence
                    method = "ocr_fallback"
                    report["pages_ocr"] += 1
                elif needs_ocr:
                    # Previously these pages were kept or dropped silently; they
                    # are now listed so scanned pages are visible in the report.
                    report["pages_low_text_without_ocr"].append(page_num)
                elif ocr and image_min_area > 0:
                    image_boxes = _image_boxes(page, image_min_area)
                    if image_boxes:
                        image_text, confidence = _ocr_regions_result(page, image_boxes, dpi=dpi, lang=ocr_lang)
                        image_text = _normalize_text(image_text, ocr=True)
                        if image_text:
                            norm_text = f"{norm_text}\n\n{image_text}".strip()
                            method = "text+ocr_images"
                            report["pages_image_ocr"].append(page_num)
                if confidence is not None:
                    report["ocr_confidence"][str(page_num)] = confidence
                    if confidence < min_confidence:
                        report["pages_low_ocr_confidence"].append(page_num)
                if not norm_text:
                    if not has_tables:
                        report["pages_skipped_empty"].append(page_num)
                    continue
                if method == "text":
                    report["pages_text"] += 1
                pages.append({"page": page_num, "text": norm_text, "extraction_method": method, "ocr_confidence": confidence})
            finally:
                page.close()  # long PDFs: free each page's parsed objects
    finally:
        opened.__exit__(None, None, None)
    tables = dx.merge_continued_tables(found_tables, page_heights) if backend == "pdfplumber" else found_tables
    report["tables_across_pages"] = sum(1 for t in tables if len(t.pages) > 1)
    # Popped by convert_pdf_to_rows (docextract Table objects, not JSON).
    report["_tables"] = tables
    return pages, report


def convert_pdf_to_rows(
    pdf_path: str | Path,
    *,
    doc_type: str = "policy",
    title: str = "",
    lang: str = "ja",
    chunking: str = "ja_doc_type",
    tenant_id: str = "default",
    doc_version: str = "v1",
    max_chars: int = 1800,
    overlap: int = 200,
    ocr: bool = False,
    ocr_lang: str = "jpn",
    min_text_chars: int = 30,
    dpi: int = 300,
    preprocess: bool = True,
    source_doc: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Convert one PDF into canonical chunk rows plus an extraction report.

    Pages are preprocessed together first (repeated header/footer lines,
    whitespace) so header detection can compare pages; the heading context is
    carried from one page to the next so a section that crosses a page break
    keeps its section path.
    """
    path = Path(pdf_path)
    tenant = str(tenant_id or "").strip() or "default"
    # source_doc: the document's name when the file name is not (uploads carry
    # a random prefix); chunk ids are built from it, so they stay stable.
    doc_id = str(source_doc or "").strip() or path.name
    checksum = _file_sha256(path)
    pages, report = _extract_pages(path, ocr=ocr, ocr_lang=ocr_lang, min_text_chars=min_text_chars, dpi=dpi)
    tables_found = report.pop("_tables", []) or []

    if preprocess and pages:
        page_rows = [
            {"doc_id": doc_id, "source_doc": doc_id, "source_pages": [p["page"]], "text": p["text"]}
            for p in pages
        ]
        cleaned, page_report = preprocess_rows(
            page_rows,
            PreprocessOptions(join_broken_lines=False, attach_quantities=False),
        )
        for page, row in zip(pages, cleaned):
            page["text"] = str(row.get("text") or "").strip()
        report["page_preprocess"] = {
            k: page_report.get(k) for k in ("repeated_lines_removed", "repeated_line_keys", "rows_changed")
        }

    rows: List[Dict[str, Any]] = []
    chunk_index = 0
    inherited_path: List[str] = []
    prefix = _id_prefix(tenant)
    for page in pages:
        page_num = page["page"]
        text = page["text"]
        if not text:
            report["pages_skipped_empty"].append(page_num)
            continue
        if chunking == "fixed":
            records = []
            for chunk in _split_with_overlap(text, max_chars=max_chars, overlap=overlap):
                records.append(
                    {
                        "id": f"{prefix}{doc_id}:p{page_num}:c{chunk_index}",
                        "text": chunk,
                        "source_doc": doc_id,
                        "source_pages": [page_num],
                        "doc_id": doc_id,
                        "chunk_index": chunk_index,
                        "searchable": 1,
                        "type": "pdf",
                        "quality": "high",
                    }
                )
                chunk_index += 1
        else:
            records = build_ja_chunk_records(
                doc_id=doc_id,
                source_doc=doc_id,
                text=text,
                doc_type=doc_type,
                title=title,
                source_pages=[page_num],
                base_chunk_index=chunk_index,
                tenant_id=tenant,
                inherited_section_path=inherited_path or None,
            )
            used_indices: Set[int] = set(range(chunk_index))
            for rec in records:
                chunk_index = _coerce_unique_chunk_index(rec, used_indices=used_indices, next_index=chunk_index)
            if records:
                inherited_path = list(records[-1].get("section_path") or [])
        for rec in records:
            _add_production_metadata(
                rec,
                tenant_id=tenant,
                doc_version=doc_version,
                language=lang,
                extraction_method=page["extraction_method"],
                doc_checksum=checksum,
            )
            if page.get("ocr_confidence") is not None:
                rec["ocr_confidence"] = page["ocr_confidence"]
                if page["ocr_confidence"] < _min_ocr_confidence():
                    rec["quality"] = "low"
        rows.extend(records)

    tables_extracted = 0
    for table_no, table in enumerate(sorted(tables_found, key=lambda t: t.page), start=1):
        body_start = max(1, table.header_rows) + 1
        records = table_rows_to_chunks(
            headers=table.header(),
            rows=[(row_no, cells) for row_no, cells in enumerate(table.body(), start=body_start)],
            source_type="pdf",
            source_doc=doc_id,
            location_prefix=f"p{table.page}:t{table_no}:",
            tenant_id=tenant,
            doc_type=doc_type,
            parser=f"table:{table.backend}",
            start_index=chunk_index,
            extra_builder=lambda row_number, values, _p=table.pages, _t=table_no, _c=table.confidence: {
                "source_pages": list(_p),
                "table_index": _t,
                "table_confidence": _c,
            },
        )
        for rec in records:
            if rec["chunk_type"] == "row":
                rec["chunk_type"] = "table_row"
            rec["title"] = title or doc_id
            _add_production_metadata(
                rec,
                tenant_id=tenant,
                doc_version=doc_version,
                language=lang,
                extraction_method="pdf_table",
                doc_checksum=checksum,
            )
        chunk_index += len(records)
        tables_extracted += 1
        rows.extend(records)
    report["tables_extracted"] = tables_extracted

    if preprocess and rows:
        rows, chunk_report = preprocess_rows(
            rows,
            PreprocessOptions(clean_text=False, join_broken_lines=False, remove_repeated_lines=False),
        )
        report["quantities_by_kind"] = chunk_report.get("quantities_by_kind")
        report["invalid_quantities"] = chunk_report.get("invalid_quantities")
    report.update(
        {
            "source_doc": doc_id,
            "tenant_id": tenant,
            "doc_checksum": checksum,
            "chunks": len(rows),
            "preprocess_version": PREPROCESS_VERSION if preprocess else None,
        }
    )
    return rows, report


def write_jsonl_atomic(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    # Readers never see a half-written file: write next to it, then replace.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as f:
        for rec in rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", default=None, help="write the extraction report (JSON) here")
    parser.add_argument("--doc-type", default="policy")
    parser.add_argument("--title", default="")
    parser.add_argument("--lang", default="ja")
    parser.add_argument("--chunking", choices=("fixed", "ja_doc_type"), default="ja_doc_type")
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--doc-version", default="v1")
    parser.add_argument("--max-chars", type=int, default=1800)
    parser.add_argument("--overlap", type=int, default=200)
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--ocr-lang", default="jpn")
    parser.add_argument("--min-text-chars", type=int, default=30)
    parser.add_argument("--dpi", type=int, default=300, help="OCR rendering resolution (Tesseract works best near 300)")
    parser.add_argument("--no-preprocess", action="store_true", help="skip header/footer removal and quantities")
    args = parser.parse_args(argv)

    try:
        rows, report = convert_pdf_to_rows(
            _resolve_path(args.pdf),
            doc_type=args.doc_type,
            title=args.title,
            lang=args.lang,
            chunking=args.chunking,
            tenant_id=args.tenant_id,
            doc_version=args.doc_version,
            max_chars=args.max_chars,
            overlap=args.overlap,
            ocr=args.ocr,
            ocr_lang=args.ocr_lang,
            min_text_chars=args.min_text_chars,
            dpi=args.dpi,
            preprocess=not args.no_preprocess,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2 if "OCR" in str(exc) else 1

    write_jsonl_atomic(_resolve_path(args.out), rows)
    if args.report:
        report_path = _resolve_path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: report.get(k) for k in ("pages_total", "pages_text", "pages_ocr", "table_backend", "tables_extracted", "chunks")}
    summary["pages_low_ocr_confidence"] = len(report.get("pages_low_ocr_confidence") or [])
    summary["pages_low_text_without_ocr"] = len(report.get("pages_low_text_without_ocr") or [])
    summary["pages_skipped_empty"] = len(report.get("pages_skipped_empty") or [])
    print(json.dumps(summary, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
