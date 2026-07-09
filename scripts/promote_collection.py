#!/usr/bin/env python3
"""Safe collection promotion planner (Prompt056).

Evaluation/approval ONLY by default: validates a reviewed NON-production staging
set and prints an approval report with the required isolation-check + backup
steps and a rollback plan. It never mutates a vectorstore; actual ingest into the
explicit non-production served collection and restore are run by the existing
tested tools after approval. Refuses the production/default collection. No
secrets, no .env, no network, no Docker.

Usage:
  python scripts/promote_collection.py --served <nonprod_collection> --inputs a.jsonl b.jsonl [--expected-tenant t] [--prior-backup PATH] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapi.collection_promotion import approval_report_markdown, plan_promotion  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Plan a safe staging->served collection promotion (evaluation only).")
    parser.add_argument("--served", required=True, help="Explicit NON-production served collection name.")
    parser.add_argument("--inputs", nargs="+", required=True, help="Canonical chunk JSONL files (synthetic/sanitized).")
    parser.add_argument("--expected-tenant", default=None)
    parser.add_argument("--prior-backup", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    plan = plan_promotion(args.inputs, args.served, expected_tenant=args.expected_tenant, prior_backup=args.prior_backup)
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(approval_report_markdown(plan))
    # exit 0 if approvable, 1 otherwise (operator gate)
    return 0 if plan.get("approved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
