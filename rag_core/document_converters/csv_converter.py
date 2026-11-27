from __future__ import annotations

import codecs
import csv
import io
import re
from pathlib import Path
from typing import Dict, List, Tuple

from rag_core.document_converters.base import table_rows_to_chunks
from rag_core.document_converters.table_layout import find_layout

# Japanese business CSVs arrive as UTF-8 (with or without BOM), Shift_JIS
# (cp932, Excel default), EUC-JP or UTF-16 (Excel "Unicode text"; some
# exporters write UTF-16 without a BOM, detected from the NUL-byte pattern).
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


def _utf16_without_bom(raw: bytes) -> str | None:
    """'utf-16-le' / 'utf-16-be' for UTF-16 text saved without a BOM, else None.

    ASCII characters (delimiters, digits, line breaks: every CSV has many)
    are encoded with a zero high byte, so NUL bytes pile up at odd offsets
    (little endian) or even offsets (big endian). UTF-8, CP932 and EUC-JP
    text never contains NUL. The guess must also decode strictly and read
    as Japanese/ASCII text."""
    sample = raw[: _SNIFF_BYTES - (_SNIFF_BYTES % 2)]
    if len(sample) < 4 or b"\x00" not in sample:
        return None
    half = len(sample) // 2
    even_nulls, odd_nulls = sample[0::2].count(0), sample[1::2].count(0)
    if odd_nulls >= max(2, 0.05 * half) and even_nulls <= 0.25 * odd_nulls:
        candidate = "utf-16-le"
    elif even_nulls >= max(2, 0.05 * half) and odd_nulls <= 0.25 * even_nulls:
        candidate = "utf-16-be"
    else:
        return None
    if len(raw) % 2:
        return None
    try:
        text = raw.decode(candidate)
    except UnicodeDecodeError:
        return None
    score = len(_COMMON_RE.findall(text)) / max(1, len(text))
    return candidate if score >= 0.6 else None


def detect_encoding(raw: bytes) -> str:
    """Pick the encoding that decodes strictly and looks most like Japanese text."""
    bom = _bom_encoding(raw)
    if bom:
        return bom
    utf16 = _utf16_without_bom(raw)
    if utf16:
        return utf16
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
    numbered = [(line_no, cells) for line_no, cells in enumerate(rows, start=1)]
    layout = find_layout(numbered)
    headers = rows[layout.header_index]
    data = numbered[layout.header_index + 1:]
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
        table_title=layout.title,
        summary_rows=layout.summary_rows,
        extra_builder=lambda row_number, values: {
            "sheet_name": None,
            "source_encoding": encoding,
            "source_delimiter": delimiter,
        },
    )
