from __future__ import annotations

import codecs
import csv
import io
import re
from pathlib import Path
from typing import Dict, List, Tuple

from rag_core.document_converters.base import table_rows_to_chunks

# Japanese business CSVs arrive as UTF-8 (with or without BOM), Shift_JIS
# (cp932, Excel default), EUC-JP or UTF-16 (Excel "Unicode text").
_CANDIDATE_ENCODINGS = ("utf-8", "cp932", "euc_jp")
_DELIMITERS = ",\t;|"
_SNIFF_BYTES = 64 * 1024
# Characters that legitimately appear in Japanese text; a wrong decoding
# produces many characters outside this set (rare kanji, half-width kana).
_COMMON_RE = re.compile(r"[\x09\x0a\x0d\x20-\x7e　-ヿ一-鿿！-～]")


def _bom_encoding(raw: bytes) -> str | None:
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    return None


def detect_encoding(raw: bytes) -> str:
    """Pick the encoding that decodes strictly and looks most like Japanese text."""
    bom = _bom_encoding(raw)
    if bom:
        return bom
    best = ("utf-8", -1.0)
    for encoding in _CANDIDATE_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "utf-8":
            return "utf-8"  # strict UTF-8 success on non-ASCII is decisive
        score = len(_COMMON_RE.findall(text)) / max(1, len(text))
        if score > best[1]:
            best = (encoding, score)
    if best[1] < 0:
        raise ValueError("could not decode CSV as UTF-8, UTF-16, CP932 or EUC-JP")
    return best[0]


def detect_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=_DELIMITERS).delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample else ""
        counts = {d: first.count(d) for d in _DELIMITERS}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def read_csv_rows(path: str | Path) -> Tuple[List[List[str]], str, str]:
    # Decode the whole file: a truncated sample could end inside a multi-byte
    # character and reject the right encoding. Input size is bounded by
    # INGEST_MAX_FILE_BYTES on the web path.
    raw = Path(path).read_bytes()
    encoding = detect_encoding(raw)
    text = raw.decode(encoding)
    delimiter = detect_delimiter(text[:_SNIFF_BYTES])
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    return rows, encoding, delimiter


def convert_csv(
    path: str | Path,
    *,
    tenant_id: str = "default",
    source_doc: str | None = None,
    doc_type: str | None = None,
) -> List[Dict]:
    path = Path(path)
    source_doc = source_doc or path.name
    rows, encoding, delimiter = read_csv_rows(path)
    if not rows:
        return []
    headers = rows[0]
    data = [(line_no, cells) for line_no, cells in enumerate(rows[1:], start=2)]
    return table_rows_to_chunks(
        headers=headers,
        rows=data,
        source_type="csv",
        source_doc=source_doc,
        location_prefix="",
        tenant_id=tenant_id,
        doc_type=doc_type or "table",
        parser="stdlib_csv",
        start_index=1,
        extra_builder=lambda row_number, values: {
            "sheet_name": None,
            "source_encoding": encoding,
            "source_delimiter": delimiter,
        },
    )
