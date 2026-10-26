"""Pre-ingestion cleanup of canonical chunk rows, with a quantitative report.

Steps (each switchable, each counted in the report):
1. display-safe cleanup (invisible characters, whitespace) of text fields;
2. joining Japanese lines broken by PDF layout (単一改行 between Japanese
   characters; paragraph breaks are kept);
3. removing header/footer lines that repeat across pages of one document
   (digits masked, so "- 3 -" and "- 4 -" count as the same line);
4. attaching extracted quantities (money, dates, counts, ...) as metadata.
Stored text is never index-normalized: lowercasing, NFKC and numeral
rewriting happen at tokenization time so citations show the source text.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Set, Tuple

from rag_core.quantities import EXTRACTOR_VERSION, extract_quantities
from rag_core.text_normalize import PROFILE_VERSION, normalize

PREPROCESS_VERSION = "ingest-preprocess-v1"
_TEXT_FIELDS = ("text", "searchable_text", "display_text")
_JA_CHAR = r"[ぁ-んァ-ヴー一-龥々〆〤、。「」『』（）]"
_BROKEN_LINE_RE = re.compile(rf"(?<={_JA_CHAR})\n(?={_JA_CHAR})")
_DIGITS_RE = re.compile(r"\d+")
_MAX_QUANTITIES = 50


@dataclass(frozen=True)
class PreprocessOptions:
    clean_text: bool = True
    join_broken_lines: bool = True
    remove_repeated_lines: bool = True
    # A line is a header/footer when it is short, sits in the first/last
    # `edge_lines` lines of a page, and appears on at least `min_pages` pages
    # covering at least `min_ratio` of the document's pages.
    repeated_line_max_chars: int = 40
    repeated_line_min_pages: int = 3
    repeated_line_min_ratio: float = 0.6
    edge_lines: int = 2
    attach_quantities: bool = True


def _line_key(line: str) -> str:
    return _DIGITS_RE.sub("#", normalize(line, "index"))


def _page_of(row: Dict[str, Any]) -> Any:
    # Only rows with a real page number take part: table/CSV rows carry
    # source_pages [-1], and their repeated "列名: 値" lines are content.
    pages = row.get("source_pages")
    if isinstance(pages, list) and pages and isinstance(pages[0], int) and pages[0] >= 1:
        return pages[0]
    return None


def _edge_lines(text: str, edge: int) -> List[str]:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return lines[:edge] + lines[-edge:] if len(lines) > edge * 2 else lines


def detect_repeated_lines(rows: Iterable[Dict[str, Any]], options: PreprocessOptions) -> Dict[str, Set[str]]:
    """Per doc_id, the masked keys of lines that repeat as headers/footers."""
    pages_by_doc: Dict[str, Set[Any]] = defaultdict(set)
    key_pages: Dict[Tuple[str, str], Set[Any]] = defaultdict(set)
    for row in rows:
        doc_id = str(row.get("doc_id") or row.get("source_doc") or "")
        page = _page_of(row)
        if page is None:
            continue
        pages_by_doc[doc_id].add(page)
        for line in _edge_lines(str(row.get("text") or ""), options.edge_lines):
            if len(line) <= options.repeated_line_max_chars:
                key_pages[(doc_id, _line_key(line))].add(page)
    repeated: Dict[str, Set[str]] = defaultdict(set)
    for (doc_id, key), pages in key_pages.items():
        total = len(pages_by_doc[doc_id])
        if len(pages) >= options.repeated_line_min_pages and len(pages) / max(1, total) >= options.repeated_line_min_ratio:
            repeated[doc_id].add(key)
    return repeated


def _remove_lines(text: str, keys: Set[str], max_chars: int) -> Tuple[str, int]:
    kept: List[str] = []
    removed = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and len(stripped) <= max_chars and _line_key(stripped) in keys:
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept), removed


def preprocess_rows(
    rows: List[Dict[str, Any]], options: PreprocessOptions = PreprocessOptions()
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    repeated = detect_repeated_lines(rows, options) if options.remove_repeated_lines else {}
    report: Dict[str, Any] = {
        "preprocess_version": PREPROCESS_VERSION,
        "normalization_version": PROFILE_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "rows": len(rows),
        "rows_changed": 0,
        "chars_before": 0,
        "chars_after": 0,
        "lines_joined": 0,
        "repeated_lines_removed": 0,
        "repeated_line_keys": {doc: sorted(keys)[:20] for doc, keys in repeated.items()},
        "quantities_by_kind": {},
        "invalid_quantities": 0,
        "empty_after_preprocess": 0,
    }
    kinds: Counter = Counter()
    out: List[Dict[str, Any]] = []
    for row in rows:
        new_row = dict(row)
        doc_id = str(row.get("doc_id") or row.get("source_doc") or "")
        before = str(row.get("text") or "")
        report["chars_before"] += len(before)
        for field in _TEXT_FIELDS:
            value = row.get(field)
            if not isinstance(value, str) or not value:
                continue
            if options.clean_text:
                value = normalize(value, "display")
            if repeated.get(doc_id):
                value, removed = _remove_lines(value, repeated[doc_id], options.repeated_line_max_chars)
                if field == "text":
                    report["repeated_lines_removed"] += removed
            if options.join_broken_lines:
                value, joined = _BROKEN_LINE_RE.subn("", value)
                if field == "text":
                    report["lines_joined"] += joined
            new_row[field] = value
        after = str(new_row.get("text") or "")
        report["chars_after"] += len(after)
        if after != before:
            report["rows_changed"] += 1
        if before and not after.strip():
            report["empty_after_preprocess"] += 1
        if options.attach_quantities:
            quantities = extract_quantities(str(new_row.get("searchable_text") or after))
            kinds.update(q.kind for q in quantities)
            report["invalid_quantities"] += sum(1 for q in quantities if not q.valid)
            new_row["quantities"] = [q.to_dict() for q in quantities[:_MAX_QUANTITIES]]
        new_row["preprocess_version"] = PREPROCESS_VERSION
        out.append(new_row)
    report["quantities_by_kind"] = dict(sorted(kinds.items()))
    return out, report
