#!/usr/bin/env python3
"""Quantitative checks for Japanese normalization and quantity extraction.

Default golden cases are synthetic, embedded in Python rather than stored as
public JSONL files. A caller may supply --golden path/to/private.jsonl explicitly
for its own local evaluation. Nothing uploads that private file to GitHub.
"""

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
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rag_core.ja_numbers import decimal_to_str, parse_japanese_number
from rag_core.quantities import extract_quantities
from rag_core.text_normalize import PROFILES, normalize

_MAX_EXAMPLES = 20

# Synthetic, explicitly authored test inputs; none is extracted from user PDFs.
# The default CI regression gate must not silently disappear when source-data
# JSONL files are deliberately removed from public Git history.
_SYNTHETIC_GOLDEN: List[Dict[str, Any]] = [
    {"id": "number-fullwidth-man", "category": "number", "input": "１万", "expected": "10000"},
    {"id": "number-mixed-man-sen", "category": "number", "input": "1万5千", "expected": "15000"},
    {"id": "number-decimal-oku", "category": "number", "input": "3.5億", "expected": "350000000"},
    {"id": "number-kanji-year", "category": "number", "input": "二〇二四", "expected": "2024"},
    {"id": "number-malformed-order", "category": "number", "input": "十百", "expected": None},
    {"id": "number-decimal-trailing", "category": "number", "input": "2.50", "expected": "2.5"},
    {"id": "normalize-legal-amount", "category": "normalize", "input": "第三条　１，２００円", "profile": "index", "expected": "第3条 1200円"},
    {"id": "normalize-display-spaces", "category": "normalize", "input": "  alpha　 beta ", "profile": "display", "expected": "alpha beta"},
    {"id": "normalize-dense-width", "category": "normalize", "input": "ＡＢＣ", "profile": "dense", "expected": "ABC"},
    {"id": "normalize-katakana-hyphen", "category": "normalize", "input": "サ-バ", "profile": "index", "expected": "サーバ"},
    {"id": "normalize-match-long-vowel", "category": "normalize", "input": "サーバー", "profile": "match", "expected": "サーバ"},
    {"id": "quantity-jpy", "category": "quantity", "input": "15,000円", "expected": [["money", "15000JPY", True]]},
    {"id": "quantity-percent", "category": "quantity", "input": "10%", "expected": [["percent", "10%", True]]},
    {"id": "quantity-article", "category": "quantity", "input": "第三条", "expected": [["article", "3条", True]]},
    {"id": "quantity-people", "category": "quantity", "input": "二人", "expected": [["count", "2人", True]]},
    {"id": "date-iso", "category": "date", "input": "2024年4月5日", "expected": [["date", "2024-04-05", True]]},
]


def synthetic_golden_cases() -> List[Dict[str, Any]]:
    """Return fresh, deterministic synthetic golden cases for CI and pytest."""
    return [dict(item) for item in _SYNTHETIC_GOLDEN]


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check_case(case: Dict[str, Any]) -> Tuple[bool, Any]:
    category = case["category"]
    if category == "number":
        value = parse_japanese_number(case["input"])
        actual = None if value is None else decimal_to_str(value)
        return actual == case["expected"], actual
    if category == "normalize":
        actual = normalize(case["input"], case.get("profile", "index"))
        return actual == case["expected"], actual
    if category in ("date", "quantity"):
        actual = [[q.kind, q.canonical, q.valid] for q in extract_quantities(case["input"])]
        return actual == [list(item) for item in case["expected"]], actual
    raise ValueError(f"unknown golden category '{category}' in case {case.get('id')}")


def idempotence_failures(texts: Sequence[str]) -> List[Dict[str, str]]:
    failures = []
    for text in texts:
        for profile in PROFILES:
            once = normalize(text, profile)
            if normalize(once, profile) != once:
                failures.append({"profile": profile, "input": text[:120]})
    return failures


def golden_report(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_category: Dict[str, Counter] = defaultdict(Counter)
    failures = []
    for case in cases:
        ok, actual = check_case(case)
        by_category[case["category"]]["passed" if ok else "failed"] += 1
        if not ok:
            failures.append({"id": case.get("id"), "input": case["input"], "expected": case["expected"], "actual": actual})
    accuracy = {
        category: {
            "passed": counts["passed"],
            "total": counts["passed"] + counts["failed"],
            "accuracy": counts["passed"] / max(1, counts["passed"] + counts["failed"]),
        }
        for category, counts in sorted(by_category.items())
    }
    return {
        "cases": len(cases),
        "accuracy_by_category": accuracy,
        "failures": failures,
        "idempotence_failures": idempotence_failures([case["input"] for case in cases]),
    }


def corpus_report(path: Path, limit: Optional[int]) -> Dict[str, Any]:
    texts = []
    for row in _read_jsonl(path)[:limit]:
        text = str(row.get("searchable_text") or row.get("text") or "")
        if text:
            texts.append(text)
    changed = {profile: sum(1 for t in texts if normalize(t, profile) != t) for profile in PROFILES}
    kinds: Counter = Counter()
    invalid: List[Dict[str, str]] = []
    for text in texts:
        for q in extract_quantities(text):
            kinds[q.kind] += 1
            if not q.valid and len(invalid) < _MAX_EXAMPLES:
                invalid.append({"raw": q.raw, "canonical": q.canonical, "note": q.note})
    failures = idempotence_failures(texts)
    return {
        "texts": len(texts),
        "change_rate_by_profile": {p: changed[p] / max(1, len(texts)) for p in PROFILES},
        "idempotence_failures": len(failures),
        "idempotence_failure_examples": failures[:_MAX_EXAMPLES],
        "quantities_by_kind": dict(sorted(kinds.items())),
        "invalid_quantity_examples": invalid,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=None, help="Optional private golden JSONL; default: in-code synthetic cases")
    parser.add_argument("--corpus", type=Path, default=None, help="canonical chunk JSONL to profile")
    parser.add_argument("--corpus-limit", type=int, default=None)
    parser.add_argument("--report", type=Path, default=None, help="write the JSON report here")
    args = parser.parse_args(argv)

    cases = _read_jsonl(args.golden) if args.golden is not None else synthetic_golden_cases()
    if not cases:
        parser.error("golden cases cannot be empty")
    report: Dict[str, Any] = {
        "golden_source": str(args.golden) if args.golden is not None else "synthetic_builtin",
        "golden": golden_report(cases),
    }
    if args.corpus is not None:
        report["corpus"] = corpus_report(args.corpus, args.corpus_limit)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    golden = report["golden"]
    corpus_failures = report.get("corpus", {}).get("idempotence_failures", 0)
    return 1 if golden["failures"] or golden["idempotence_failures"] or corpus_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
