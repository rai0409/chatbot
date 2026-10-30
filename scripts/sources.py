#!/usr/bin/env python3
from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Registered document sources: register, scan, and look at what changed (docs/operations/sources.md).

  python scripts/sources.py add --id ID --tenant T --path FOLDER [--collection NAME] [--schedule MINUTES] [--by TEXT]
  python scripts/sources.py list [--tenant T]
  python scripts/sources.py show --id ID
  python scripts/sources.py enable --id ID | disable --id ID | remove --id ID
  python scripts/sources.py scan --id ID          # scan and record the changes; nothing is written to a collection
  python scripts/sources.py due                    # the sources whose schedule has run out
  python scripts/sources.py changes --id ID [--limit N]
  python scripts/sources.py verify                 # check the hash chain of the change ledger
Exit code 0, or 3 for a refused request.
"""

import argparse
import json
from typing import List, Optional

from rag_core import change_ledger, source_apply, source_registry, source_sync


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("--id", required=True)
    add.add_argument("--tenant", required=True)
    add.add_argument("--kind", default="folder", choices=list(source_registry.KINDS))
    add.add_argument("--path", default=None, help="the folder (kind folder)")
    add.add_argument("--drive-id", default=None, help="the drive id (kind sharepoint)")
    add.add_argument("--folder", default=None, help="the folder in the drive, e.g. /Shared Documents/Policies (kind sharepoint)")
    add.add_argument("--collection", default=None)
    add.add_argument("--schedule", type=int, default=0)
    add.add_argument("--by", default="")
    lst = sub.add_parser("list")
    lst.add_argument("--tenant", default=None)
    for name in ("show", "enable", "disable", "remove", "scan"):
        sub.add_parser(name).add_argument("--id", required=True)
    sub.add_parser("due")
    sub.add_parser("plan").add_argument("--id", required=True)
    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--id", required=True)
    apply_cmd.add_argument("--approval", default=None, help="the plan SHA-256 a person approved")
    apply_cmd.add_argument("--by", default="")
    approve = sub.add_parser("approve")
    approve.add_argument("--id", required=True)
    approve.add_argument("--plan", required=True, help="the plan SHA-256 shown by `plan` or `apply`")
    approve.add_argument("--by", required=True)
    run_due = sub.add_parser("run-due")
    run_due.add_argument("--apply", action="store_true", help="also apply the plans that are not held")
    changes = sub.add_parser("changes")
    changes.add_argument("--id", required=True)
    changes.add_argument("--limit", type=int, default=20)
    sub.add_parser("verify")
    args = parser.parse_args(argv)

    try:
        if args.command == "add":
            options = {"drive_id": args.drive_id, "folder": args.folder} if args.kind == "sharepoint" else None
            result = source_registry.create(args.id, tenant_id=args.tenant, kind=args.kind, path=args.path, options=options,
                                            collection=args.collection, schedule_minutes=args.schedule, created_by=args.by)
        elif args.command == "list":
            result = {"sources": source_registry.list_sources(args.tenant)}
        elif args.command == "show":
            result = source_registry.get(args.id)
        elif args.command == "enable":
            result = source_registry.set_enabled(args.id, True)
        elif args.command == "disable":
            result = source_registry.set_enabled(args.id, False)
        elif args.command == "remove":
            source_registry.remove(args.id)
            result = {"removed": args.id}
        elif args.command == "scan":
            result = source_sync.scan_source(args.id)
        elif args.command == "due":
            result = {"due": source_sync.due_sources()}
        elif args.command == "plan":
            result = source_apply.plan_source(args.id)
            result["guard"] = source_apply.guard(result)
        elif args.command == "apply":
            result = source_apply.apply_source(args.id, approval=args.approval, actor=args.by)
        elif args.command == "approve":
            result = source_apply.approve(args.id, args.plan, actor=args.by)
        elif args.command == "run-due":
            result = {"results": source_apply.run_due(apply=args.apply)}
        elif args.command == "changes":
            source_registry.get(args.id)
            result = {"entries": [{k: e.get(k) for k in ("seq", "at", "kind", "run_id", "summary")}
                                  for e in change_ledger.read_entries(args.id, args.limit)]}
        else:
            result = {"entries": change_ledger.verify_chain(), "chain": "ok"}
    except source_registry.SourceError as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
