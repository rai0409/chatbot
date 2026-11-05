"""Delete documents or promote a staging collection (stage 15).

  python scripts/manage_documents.py delete --tenant T --source-doc 経費規程.pdf [--source-doc ...]
      [--collection STAGING] [--execute]
  python scripts/manage_documents.py promote --collection STAGING --tenant T [--execute]

delete: every chunk of the documents (tenant + source_doc) leaves the vector
  store and the keyword corpus; without --collection the served collection.
promote: the staging collection's documents of the tenant are copied into the
  served collection without re-embedding, replacing the same documents there.
Both print a dry-run report unless --execute is given. The same functions back
the admin API (/admin/ingestion/documents/delete, /admin/ingestion/promote).
Back up first (scripts/backup.sh); stop or let finish ingestion jobs of the
same collection (they share the write lock).
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
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    delete = sub.add_parser("delete")
    delete.add_argument("--tenant", required=True)
    delete.add_argument("--source-doc", action="append", required=True)
    delete.add_argument("--collection", default=None, help="staging collection (default: the served one)")
    delete.add_argument("--execute", action="store_true")
    promote = sub.add_parser("promote")
    promote.add_argument("--collection", required=True)
    promote.add_argument("--tenant", required=True)
    promote.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    from rag_core.audit_log import append_audit_event
    from webapi import collection_admin

    try:
        if args.command == "delete":
            report = collection_admin.delete_documents(args.collection, tenant=args.tenant,
                                                       source_docs=args.source_doc, execute=args.execute)
        else:
            report = collection_admin.promote_staging(args.collection, tenant=args.tenant, execute=args.execute)
    except (PermissionError, LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.execute:
        append_audit_event("admin", {"action": f"cli_{args.command}", "admin_scope": "installation",
                                     "tenant_id": args.tenant, "collection": report.get("collection")})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
