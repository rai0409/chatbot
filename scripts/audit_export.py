#!/usr/bin/env python3
"""Safe (redacted, aggregate) audit export CLI (Prompt062).

Reads an audit JSONL file and emits an AGGREGATE export (counts by date/tenant/
kind/answer_mode/guard_reason). Never emits raw question text, document text,
API keys, or identity. No .env, no network, no secrets.

Usage: python scripts/audit_export.py <audit.jsonl> [--json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapi.audit_export import export_from_lines  # noqa: E402


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: audit_export.py <audit.jsonl> [--json]", file=sys.stderr)
        return 2
    path = argv[0]
    rows = export_from_lines(Path(path).read_text(encoding="utf-8").splitlines())
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
