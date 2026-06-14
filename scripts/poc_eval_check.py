#!/usr/bin/env python3
"""Validate a PoC question-set file against the eval schema (Prompt057).

Safe, local-only: parses a question-set JSONL (template or a sanitized customer
set), checks required categories are present and fields are well-formed, and
warns if any obvious real-PII-looking marker appears. It does NOT read customer
documents, .env, the network, or Docker, and prints no secrets. Customer
question sets and outputs must live under a gitignored path (e.g. runs/poc/).

Usage: python scripts/poc_eval_check.py <question_set.jsonl>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REQUIRED_CATEGORIES = ("answerable", "abstain", "out-of-corpus")
# crude markers that suggest unsanitized real data slipped in
_PII_HINTS = (re.compile(r"\b\d{3}-\d{4}-\d{4}\b"), re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"))


def validate(path: str) -> int:
    rows = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            print(f"poc_eval_check: line {i} is not valid JSON", file=sys.stderr)
            return 2
    if not rows:
        print("poc_eval_check: empty question set", file=sys.stderr)
        return 2

    cats = " ".join(str(r.get("category", "")) for r in rows)
    missing = [c for c in REQUIRED_CATEGORIES if c not in cats]
    if missing:
        print(f"poc_eval_check: missing required categories: {missing}", file=sys.stderr)
        return 1

    warnings = 0
    for r in rows:
        if not r.get("case_id") or "query" not in r:
            print(f"poc_eval_check: case missing case_id/query: {r.get('case_id')}", file=sys.stderr)
            return 1
        blob = json.dumps(r, ensure_ascii=False)
        if any(p.search(blob) for p in _PII_HINTS):
            print(f"poc_eval_check: WARNING possible unsanitized PII in {r.get('case_id')}", file=sys.stderr)
            warnings += 1
    print(f"poc_eval_check: OK ({len(rows)} cases, {warnings} warnings)")
    return 0


def main(argv=None) -> int:
    if not argv:
        argv = sys.argv[1:]
    if len(argv) != 1:
        print("usage: poc_eval_check.py <question_set.jsonl>", file=sys.stderr)
        return 2
    return validate(argv[0])


if __name__ == "__main__":
    raise SystemExit(main())
