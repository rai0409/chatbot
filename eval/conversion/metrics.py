"""Metric functions for conversion accuracy (pure, no converter imports).

Every metric works on normalized strings: NFKC, whitespace removed. Values
that must match *exactly* (typed cells) are compared after stripping only.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Labels the converters put in front of text they add to a slide / paragraph;
# they are presentation, not content, and are dropped before comparing.
_LABEL_PREFIX_RE = re.compile(r"^(ノート|画像内テキスト|グラフ|系列|項目|図表)\s*[:：]\s*")


def norm(text: Any) -> str:
    return "".join(unicodedata.normalize("NFKC", str(text if text is not None else "")).split())


def strip_labels(text: str) -> str:
    return "\n".join(_LABEL_PREFIX_RE.sub("", line) for line in str(text or "").splitlines())


# --- edit distance -------------------------------------------------------------------------------------


def levenshtein(a: str, b: str) -> int:
    """Plain O(len(a)*len(b)) edit distance (fixture documents are small)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


@functools.lru_cache(maxsize=1)
def _char_accuracy_impl():
    """docextract's char_accuracy when importable (same definition), else None (the impl name is reported)."""
    try:
        from docextract.benchmark import char_accuracy  # type: ignore

        return char_accuracy
    except ImportError:  # optional library
        try:
            import sys
            from pathlib import Path

            src = Path(__file__).resolve().parents[2] / "libs" / "docextract" / "src"
            if src.is_dir() and str(src) not in sys.path:
                sys.path.append(str(src))
            from docextract.benchmark import char_accuracy  # type: ignore

            return char_accuracy
        except ImportError:
            return None


def text_scores(expected: str, actual: str) -> Dict[str, Any]:
    """CER (= edits / len(expected), may exceed 1) and normalized similarity."""
    e, a = norm(expected), norm(actual)
    distance = levenshtein(e, a)
    cer = (distance / len(e)) if e else (0.0 if not a else 1.0)
    similarity = 1.0 - distance / max(len(e), len(a)) if (e or a) else 1.0
    impl = _char_accuracy_impl()
    char_acc = impl(e, a) if impl is not None else max(0.0, 1.0 - cer)
    return {
        "cer": round(cer, 6),
        "normalized_levenshtein_similarity": round(similarity, 6),
        "char_accuracy": round(float(char_acc), 6),
        "char_accuracy_impl": "docextract.benchmark.char_accuracy" if impl is not None else "eval.conversion.metrics",
        "expected_chars": len(e),
        "actual_chars": len(a),
        "edit_distance": distance,
    }


# --- reading order -------------------------------------------------------------------------------------


def kendall_tau(order: Sequence[float]) -> Optional[float]:
    """Kendall tau-a between 0..n-1 and the given positions (ties count as neither)."""
    n = len(order)
    if n < 2:
        return None
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            if order[j] > order[i]:
                concordant += 1
            elif order[j] < order[i]:
                discordant += 1
    return (concordant - discordant) / (n * (n - 1) / 2)


def reading_order(blocks: Sequence[str], actual: str) -> Dict[str, Any]:
    """Position of every expected block in the actual linear text -> Kendall tau.

    A block is located by its normalized text, or by its first 12 characters
    when the whole block is not found verbatim. Blocks not found at all are
    reported as missing and left out of tau."""
    haystack = norm(actual)
    positions: List[float] = []
    missing: List[str] = []
    for block in blocks:
        needle = norm(block)
        if not needle:
            continue
        pos = haystack.find(needle)
        if pos < 0 and len(needle) > 12:
            pos = haystack.find(needle[:12])
        if pos < 0:
            missing.append(block)
        else:
            positions.append(pos)
    tau = kendall_tau(positions)
    return {
        "kendall_tau": None if tau is None else round(tau, 6),
        "blocks_expected": len([b for b in blocks if norm(b)]),
        "blocks_found": len(positions),
        "blocks_missing": missing,
    }


# --- tables --------------------------------------------------------------------------------------------


def expand_merged(cells: Sequence[Sequence[Any]], merged: Iterable[Dict[str, int]] = ()) -> List[List[str]]:
    """Copy the value of every merged range's top-left cell into the covered cells.

    merged: [{"row": r, "col": c, "rowspan": n, "colspan": m}] (0-based)."""
    grid = [[str(v if v is not None else "") for v in row] for row in cells]
    for m in merged or ():
        r0, c0 = int(m["row"]), int(m["col"])
        value = grid[r0][c0]
        for r in range(r0, r0 + int(m.get("rowspan", 1))):
            while len(grid) <= r:
                grid.append([])
            for c in range(c0, c0 + int(m.get("colspan", 1))):
                while len(grid[r]) <= c:
                    grid[r].append("")
                if not grid[r][c]:
                    grid[r][c] = value
    return grid


def combined_headers(grid: Sequence[Sequence[str]], header_rows: int) -> List[str]:
    """One label per column: the header rows joined with a space (repeats dropped)."""
    width = max((len(r) for r in grid), default=0)
    labels: List[str] = []
    for c in range(width):
        parts: List[str] = []
        for r in range(max(1, header_rows)):
            value = str(grid[r][c]).strip() if r < len(grid) and c < len(grid[r]) else ""
            if value and (not parts or parts[-1] != value):
                parts.append(value)
        labels.append(" ".join(parts))
    return labels


def expected_table_pairs(table: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(header, value) pairs of a truth table's data rows."""
    grid = expand_merged(table.get("cells") or [], table.get("merged") or [])
    header_rows = int(table.get("header_rows", 1))
    skip = int(table.get("title_rows", 0))
    body = grid[skip:]
    headers = combined_headers(body, header_rows)
    pairs: List[Tuple[str, str]] = []
    for row in body[header_rows:]:
        for c, value in enumerate(row):
            if str(value).strip():
                label = headers[c] if c < len(headers) and headers[c] else f"col{c + 1}"
                pairs.append((label, str(value)))
    return pairs


def _split_pair(line: str) -> Optional[Tuple[str, str]]:
    if ": " not in line:
        return None
    header, value = line.split(": ", 1)
    return header, value


def chunk_table_pairs(chunk: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(header, value) pairs a row / table_row / qa_pair chunk carries."""
    if chunk.get("chunk_type") == "qa_pair":
        from rag_core.document_converters.base import detect_qa_columns

        columns = list(chunk.get("columns") or [])
        qa = detect_qa_columns(columns)
        text = str(chunk.get("text") or "")
        question = text.split("\nA: ", 1)[0].removeprefix("Q: ")
        answer = text.split("\nA: ", 1)[1] if "\nA: " in text else ""
        pairs: List[Tuple[str, str]] = []
        if qa is not None:
            pairs += [(columns[qa[0]], question), (columns[qa[1]], answer)]
            known = {columns[qa[0]], columns[qa[1]]}
            for line in str(chunk.get("searchable_text") or "").splitlines():
                pair = _split_pair(line)
                if pair and pair[0] in columns and pair[0] not in known:
                    pairs.append(pair)
        return pairs
    return [p for p in (_split_pair(line) for line in str(chunk.get("text") or "").split("\n")) if p]


def prf(expected: Sequence[Any], actual: Sequence[Any]) -> Dict[str, Optional[float]]:
    """Multiset precision / recall / F1 (None when both sides are empty)."""
    e, a = Counter(expected), Counter(actual)
    if not e and not a:
        return {"precision": None, "recall": None, "f1": None, "tp": 0, "expected": 0, "actual": 0}
    tp = sum((e & a).values())
    precision = tp / sum(a.values()) if a else 0.0
    recall = tp / sum(e.values()) if e else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6),
            "tp": tp, "expected": sum(e.values()), "actual": sum(a.values())}


def table_scores(tables: Sequence[Dict[str, Any]], table_chunks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    expected = [(norm(h), norm(v)) for t in tables for h, v in expected_table_pairs(t)]
    actual = [(norm(h), norm(v)) for c in table_chunks for h, v in chunk_table_pairs(c)]
    scores = prf(expected, actual)
    # Value-only F1 separates "value lost" from "value under the wrong header".
    values = prf([v for _h, v in expected], [v for _h, v in actual])
    scores["value_only_f1"] = values["f1"]
    missing = list((Counter(expected) - Counter(actual)).elements())[:10]
    extra = list((Counter(actual) - Counter(expected)).elements())[:10]
    scores["missing_examples"] = [f"{h}: {v}" for h, v in missing]
    scores["unexpected_examples"] = [f"{h}: {v}" for h, v in extra]
    return scores


def typed_cell_scores(typed: Sequence[Dict[str, Any]], table_chunks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Exact match of number/date cells: the value must appear unchanged (strip only)."""
    pairs = [(norm(h), v.strip()) for c in table_chunks for h, v in chunk_table_pairs(c)]
    matched = 0
    misses: List[Dict[str, Any]] = []
    by_kind: Dict[str, List[int]] = {}
    for cell in typed:
        want = str(cell.get("value", "")).strip()
        header = cell.get("header")
        want_header = None if header is None else norm(header)
        hit = any(v == want and (want_header is None or h == want_header) for h, v in pairs)
        matched += hit
        by_kind.setdefault(str(cell.get("kind") or "value"), []).append(int(hit))
        if not hit:
            found = [v for h, v in pairs if want_header is not None and h == want_header]
            misses.append({"expected": want, "header": header, "kind": cell.get("kind"), "found_under_header": found[:3]})
    return {
        "exact_match_rate": round(matched / len(typed), 6) if typed else None,
        "matched": matched,
        "total": len(typed),
        "by_kind": {k: round(sum(v) / len(v), 6) for k, v in sorted(by_kind.items())},
        "misses": misses,
    }


def heading_scores(expected: Sequence[str], actual: Iterable[str]) -> Dict[str, Any]:
    e = {norm(h) for h in expected if norm(h)}
    a = {norm(h) for h in actual if norm(h)}
    scores = prf(sorted(e), sorted(a))
    scores["missing"] = sorted(e - a)
    scores["unexpected"] = sorted(a - e)[:10]
    return scores


def leakage(strings: Sequence[str], body_chunks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Share of body chunks whose text or search text contains any of the strings."""
    needles = [norm(s) for s in strings if norm(s)]
    if not needles:
        return {"rate": None, "leaked_chunks": 0, "body_chunks": len(body_chunks), "strings": 0}
    leaked = 0
    for chunk in body_chunks:
        blob = norm(chunk.get("text")) + "\n" + norm(chunk.get("searchable_text"))
        leaked += any(n in blob for n in needles)
    return {
        "rate": round(leaked / len(body_chunks), 6) if body_chunks else 0.0,
        "leaked_chunks": leaked,
        "body_chunks": len(body_chunks),
        "strings": len(needles),
    }
