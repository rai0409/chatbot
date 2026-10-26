#!/usr/bin/env python3
"""Preprocess a canonical chunk JSONL before ingestion and write a report.

The input file is never modified; --output must be a different path. The
report counts every change (lines joined, header/footer lines removed,
quantities by kind, invalid dates) so the effect can be reviewed before the
output is ingested with scripts/ingest_canonical_jsonl.py.
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
from pathlib import Path
from typing import Optional, Sequence

from rag_core.ingest_preprocess import PreprocessOptions, preprocess_rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--no-clean", action="store_true")
    parser.add_argument("--no-join-lines", action="store_true")
    parser.add_argument("--no-repeated-lines", action="store_true")
    parser.add_argument("--no-quantities", action="store_true")
    parser.add_argument("--min-pages", type=int, default=3)
    parser.add_argument("--min-ratio", type=float, default=0.6)
    parser.add_argument("--max-line-chars", type=int, default=40)
    args = parser.parse_args(argv)

    if args.input.resolve() == args.output.resolve():
        parser.error("--output must differ from --input (the input is never overwritten)")
    rows = [
        json.loads(line)
        for line in args.input.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    options = PreprocessOptions(
        clean_text=not args.no_clean,
        join_broken_lines=not args.no_join_lines,
        remove_repeated_lines=not args.no_repeated_lines,
        repeated_line_max_chars=args.max_line_chars,
        repeated_line_min_pages=args.min_pages,
        repeated_line_min_ratio=args.min_ratio,
        attach_quantities=not args.no_quantities,
    )
    out_rows, report = preprocess_rows(rows, options)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in out_rows), encoding="utf-8"
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
