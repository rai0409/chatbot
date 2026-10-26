from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Verify the hash chain of audit logs (6-4). Exit code 1 on a broken chain.

  python scripts/verify_audit_chain.py [runs/audit/chat_events.jsonl ...]
Without arguments every *_events.jsonl / events.jsonl under RUNS_DIR/audit is
checked. Records written before the chain existed have no hash: rotate the
old file away (rename it) before enabling chained logging.
"""

import argparse
import json
from pathlib import Path
from typing import List, Optional, Sequence

import config
from rag_core.audit_log import verify_chain


def _default_paths() -> List[Path]:
    audit_dir = Path(config.RUNS_DIR) / "audit"
    return sorted(p for p in audit_dir.glob("*events.jsonl"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    paths = [Path(p) for p in args.paths] or _default_paths()
    failed = False
    for path in paths:
        result = verify_chain(path)
        print(json.dumps({"path": str(path), **result}, ensure_ascii=False))
        failed = failed or not result["ok"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
