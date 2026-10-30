#!/usr/bin/env python3
from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Create, compare and switch versions of a RAG profile.

A version is an immutable copy of config/rag_profiles/<id>/exported named by a digest of its content. Serving reads
the active version; without one it reads config/ as before. See docs/operations/profile_versions.md.

  python scripts/profile_versions.py create   --profile ID [--source DIR] [--by TEXT] [--note TEXT]
  python scripts/profile_versions.py list     --profile ID
  python scripts/profile_versions.py show     --profile ID --version V
  python scripts/profile_versions.py diff     --profile ID A B        (A / B: a version id or "source")
  python scripts/profile_versions.py verify   --profile ID [--version V]   (default: the active version)
  python scripts/profile_versions.py activate --profile ID --version V [--by TEXT]
  python scripts/profile_versions.py rollback --profile ID [--by TEXT]
  python scripts/profile_versions.py clear    --profile ID
  python scripts/profile_versions.py status   --profile ID
Exit code 1 when a check fails or the request is refused.
"""

import argparse
import json
from pathlib import Path
from typing import List, Optional

from rag_core import profile_loader, profile_store


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, *, version: bool = False, by: bool = False):
        cmd = sub.add_parser(name)
        cmd.add_argument("--profile", required=True)
        if version:
            cmd.add_argument("--version", default=None)
        if by:
            cmd.add_argument("--by", default="")
        return cmd

    create = command("create", by=True)
    create.add_argument("--source", default=None)
    create.add_argument("--note", default="")
    command("list")
    command("show", version=True)
    diff = command("diff")
    diff.add_argument("left")
    diff.add_argument("right")
    command("verify", version=True)
    command("activate", version=True, by=True)
    command("rollback", by=True)
    command("clear")
    command("status")
    args = parser.parse_args(argv)

    try:
        pid = args.profile
        if args.command == "create":
            result = profile_store.create_version(
                pid, source=Path(args.source) if args.source else None, created_by=args.by, note=args.note
            )
        elif args.command == "list":
            result = {"profile_id": pid, "active": profile_store.pinned_version(pid), "versions": profile_store.list_versions(pid)}
        elif args.command == "show":
            result = profile_store.read_version(pid, _need_version(args))
        elif args.command == "diff":
            result = profile_store.diff_versions(pid, args.left, args.right)
        elif args.command == "verify":
            version = args.version or profile_store.pinned_version(pid)
            if not version:
                raise profile_store.ProfileStoreError("no version given and none is active")
            result = {"verified": True, **profile_store.verify_version(pid, version)}
        elif args.command == "activate":
            result = profile_store.activate(pid, _need_version(args), activated_by=args.by)
        elif args.command == "rollback":
            result = profile_store.rollback(pid, activated_by=args.by)
        elif args.command == "clear":
            result = profile_store.clear(pid)
        else:
            result = profile_store.status(pid)
    except profile_store.ProfileStoreError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    profile_loader.reload_rag_profile_cache()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _need_version(args) -> str:
    if not args.version:
        raise profile_store.ProfileStoreError("--version is required")
    return args.version


if __name__ == "__main__":
    raise SystemExit(main())
