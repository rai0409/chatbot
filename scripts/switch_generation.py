#!/usr/bin/env python3
from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Register, verify and switch index generations.

A generation is a vector collection plus its keyword corpus
(INDEX_DIR/collections/<collection>.jsonl). Switching points the served name at a
verified generation; the previous one is kept and can be restored with rollback.

  python scripts/switch_generation.py status
  python scripts/switch_generation.py register --collection NAME [--derived-from OTHER [--reembedded]]
                                                [--keyword-index PATH] [--source-jsonl PATH] [--chunking-schema TEXT] [--force]
  python scripts/switch_generation.py verify --collection NAME
  python scripts/switch_generation.py activate --collection NAME
  python scripts/switch_generation.py rollback
  python scripts/switch_generation.py clear
Exit code 1 when a check fails or the request is refused.
"""

import argparse
import json
from typing import List, Optional

from rag_core import index_generation, index_manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    reg = sub.add_parser("register", help="write the manifest of an existing collection")
    reg.add_argument("--collection", required=True)
    reg.add_argument("--derived-from", default=None)
    reg.add_argument("--reembedded", action="store_true")
    reg.add_argument("--keyword-index", default=None)
    reg.add_argument("--source-jsonl", default=None)
    reg.add_argument("--chunking-schema", default=None)
    reg.add_argument("--force", action="store_true", help="overwrite an existing manifest")
    for name in ("verify", "activate"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--collection", required=True)
    sub.add_parser("rollback")
    sub.add_parser("clear")
    args = parser.parse_args(argv)

    try:
        if args.command == "status":
            result = index_generation.status()
        elif args.command == "register":
            if index_manifest.read_manifest(args.collection) is not None and not args.force:
                print(json.dumps({"error": f"a manifest for '{args.collection}' exists; use --force to replace it"}))
                return 1
            manifest = index_manifest.build_manifest(
                args.collection,
                keyword_index_path=args.keyword_index,
                derived_from=args.derived_from,
                reembedded=args.reembedded,
                source_jsonl_path=args.source_jsonl,
                chunking_schema=args.chunking_schema,
            )
            result = {"manifest_file": str(index_manifest.write_manifest(manifest)), "manifest": manifest}
        elif args.command == "verify":
            result = index_generation.verify_generation(args.collection)
        elif args.command == "activate":
            result = index_generation.activate(args.collection)
        elif args.command == "rollback":
            result = index_generation.rollback()
        else:
            result = index_generation.clear()
    except index_generation.GenerationError as exc:
        print(json.dumps({"error": str(exc), "report": exc.report}, ensure_ascii=False, indent=2))
        return 1
    except (ValueError, LookupError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if args.command == "verify" and not result["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
