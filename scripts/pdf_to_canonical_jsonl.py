from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Iterable, List

import config


BULLET_RE = re.compile(r"^(\s*([0-9]+[\.)]|[①-⑳]|[-•・※▶]))")


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\r", "\n")
    lines = text.splitlines()
    out: List[str] = []
    buf = ""
    for raw in lines:
        line = raw.strip()
        if line == "":
            if buf:
                out.append(buf)
                buf = ""
            continue
        if not buf:
            buf = line
            continue
        if _should_join(buf, line):
            buf = buf + line
        else:
            out.append(buf)
            buf = line
    if buf:
        out.append(buf)
    text = "\n\n".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def _should_join(prev: str, nxt: str) -> bool:
    if not prev:
        return False
    if prev[-1] in "。.!?！？":
        return False
    if BULLET_RE.match(nxt):
        return False
    return True


def _is_garbled(text: str) -> bool:
    if not text:
        return False
    compact = re.sub(r"\s+", "", text)
    total = len(compact)
    if total == 0:
        return False
    replacement_ratio = compact.count("\ufffd") / total
    control_ratio = len(re.findall(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", text)) / max(
        1, len(text)
    )
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


def _ensure_ocr_available() -> None:
    try:
        import pytesseract  # noqa: F401
        from pytesseract import get_tesseract_version  # noqa: F401
    except Exception:
        print("OCR requires pytesseract. Install requirements-pdf.txt.", file=sys.stderr)
        raise SystemExit(2)
    try:
        from pytesseract import get_tesseract_version

        _ = get_tesseract_version()
    except Exception:
        print("OCR requires system tesseract and language packs.", file=sys.stderr)
        raise SystemExit(2)


def _ocr_page(page, dpi: int, lang: str) -> str:
    from PIL import Image
    import pytesseract
    import fitz  # type: ignore

    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    return pytesseract.image_to_string(img, lang=lang)


def _resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = config.BASE_DIR / p
    return p


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-chars", type=int, default=1800)
    parser.add_argument("--overlap", type=int, default=200)
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--ocr-lang", default="jpn")
    parser.add_argument("--min-text-chars", type=int, default=30)
    parser.add_argument("--dpi", type=int, default=200)
    args = parser.parse_args()

    try:
        import fitz  # type: ignore
    except Exception:
        print("PyMuPDF is required. Install requirements-pdf.txt.", file=sys.stderr)
        return 1

    if args.ocr:
        _ensure_ocr_available()

    pdf_path = _resolve_path(args.pdf)
    out_path = _resolve_path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc_id = pdf_path.name
    chunk_index = 0
    with fitz.open(pdf_path) as doc, open(out_path, "w", encoding="utf-8") as out_f:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_num = page_idx + 1
            raw_text = page.get_text("text")
            norm_text = _normalize_text(raw_text)
            needs_ocr = len(norm_text) < args.min_text_chars or _is_garbled(norm_text)
            if args.ocr and needs_ocr:
                ocr_text = _ocr_page(page, dpi=args.dpi, lang=args.ocr_lang)
                norm_text = _normalize_text(ocr_text)
            if not norm_text:
                continue
            for chunk in _split_with_overlap(
                norm_text, max_chars=args.max_chars, overlap=args.overlap
            ):
                rec = {
                    "id": f"{doc_id}:p{page_num}:c{chunk_index}",
                    "text": chunk,
                    "source_doc": doc_id,
                    "source_pages": [page_num],
                    "doc_id": doc_id,
                    "chunk_index": chunk_index,
                    "searchable": 1,
                    "type": "pdf",
                    "quality": "high",
                }
                out_f.write(
                    f"{json.dumps(rec, ensure_ascii=False)}\n"
                )
                chunk_index += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
