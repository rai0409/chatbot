#!/usr/bin/env python3
"""Applies a bundle of file changes to a checkout in one go. Built to be sent to another machine (for example with AirDrop).

The bundle (the files of S0-S7 against one base commit) is inside this file; scripts/make_airdrop_bundle.py writes it.

  python3 airdrop_demo_apply.py --target /path/to/checkout            # the demo: shows what would change, writes nothing
  python3 airdrop_demo_apply.py --target /path/to/checkout --apply    # writes the files

What it does and does not do:
  - A file that is the same as in the bundle is left alone (running it again changes nothing).
  - A file that still is the base version is replaced; the original is first copied to <target>/.airdrop_backup_<time>/.
  - A file that is neither (someone changed it) is a CONFLICT: nothing is written at all, unless --force (the originals are backed up).
  - It never deletes a file, never runs git, never writes outside --target, and refuses a path that would leave it.
  - Every file is checked against the checksum in the bundle before it is written.
Standard library only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

# --- bundle (filled in by scripts/make_airdrop_bundle.py) ---
BUNDLE_META: Dict[str, Any] = {}
BUNDLE_B64 = ""
# --- end of bundle ---


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _norm_sha(data: bytes) -> str:
    """Checksum that ignores the line-ending difference a checkout on another system may have."""
    return _sha(data.replace(b"\r\n", b"\n"))


def _safe_relative(rel: str) -> PurePosixPath:
    path = PurePosixPath(rel)
    if not rel or path.is_absolute() or ".." in path.parts or "\\" in rel or (path.parts and ":" in path.parts[0]):
        raise ValueError(f"unsafe path in the bundle: {rel!r}")
    return path


def _open_bundle(meta: Dict[str, Any], b64: str) -> zipfile.ZipFile:
    blob = base64.b64decode(b64.encode("ascii"))
    if _sha(blob) != meta.get("payload_sha256"):
        raise ValueError("the bundle is damaged (checksum of the payload does not match): send the file again")
    return zipfile.ZipFile(io.BytesIO(blob))


def make_plan(target: Path, meta: Dict[str, Any]) -> List[Dict[str, str]]:
    """For each file of the bundle: add / update / same / conflict."""
    root = target.resolve()
    plan: List[Dict[str, str]] = []
    for entry in meta["files"]:
        rel = str(_safe_relative(entry["path"]))
        dest = (root / rel)
        if not dest.resolve().is_relative_to(root):
            raise ValueError(f"the path leaves the target: {rel!r}")
        if not dest.exists():
            action, why = ("add", "") if entry["status"] == "A" else ("conflict", "the base file is missing in the target")
        elif not dest.is_file():
            action, why = "conflict", "not a regular file"
        else:
            current = _norm_sha(dest.read_bytes())
            if current == entry["new_sha256"]:
                action, why = "same", ""
            elif entry["status"] == "M" and current == entry["base_sha256"]:
                action, why = "update", ""
            else:
                action, why = "conflict", "the file differs from both the base and the bundle"
        plan.append({"path": rel, "action": action, "why": why})
    return plan


def apply(target: Path, meta: Optional[Dict[str, Any]] = None, b64: Optional[str] = None, *, write: bool = False, force: bool = False,
          out=print, stamp: Optional[str] = None) -> int:
    """0: done (or nothing to do), 1: conflicts (nothing written), 2: the bundle or the target is unusable."""
    meta = BUNDLE_META if meta is None else meta
    b64 = BUNDLE_B64 if b64 is None else b64
    if not meta.get("files"):
        out("this file holds no bundle (run scripts/make_airdrop_bundle.py to fill it)")
        return 2
    if not target.is_dir():
        out(f"the target is not a directory: {target}")
        return 2
    try:
        archive = _open_bundle(meta, b64)
        plan = make_plan(target, meta)
    except (ValueError, zipfile.BadZipFile) as exc:
        out(f"cannot use the bundle: {exc}")
        return 2
    counts = {key: sum(1 for p in plan if p["action"] == key) for key in ("add", "update", "same", "conflict")}
    out(f"bundle: {len(plan)} files against base commit {meta.get('base_commit', '?')[:12]}  ({meta.get('created', '')})")
    out(f"  add {counts['add']} / update {counts['update']} / already applied {counts['same']} / conflict {counts['conflict']}")
    for item in plan:
        if item["action"] == "conflict":
            out(f"  CONFLICT {item['path']}: {item['why']}")
    if counts["conflict"] and not force:
        out("stopped: nothing was written. Resolve the conflicts, or use --force (the originals are backed up first).")
        return 1
    if not write:
        out("this was a demo: nothing was written. Add --apply to write the files.")
        return 0
    backup = target / f".airdrop_backup_{stamp or time.strftime('%Y%m%d-%H%M%S')}"
    written = 0
    for item in plan:
        if item["action"] == "same":
            continue
        data = archive.read(item["path"])
        expected = next(e for e in meta["files"] if e["path"] == item["path"])
        if _sha(data) != expected["raw_sha256"]:
            out(f"stopped: checksum of {item['path']} does not match the bundle; files written so far are listed above")
            return 2
        dest = target / item["path"]
        if dest.exists():
            saved = backup / item["path"]
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(dest.read_bytes())
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        written += 1
    out(f"done: {written} files written." + (f" The originals of replaced files are in {backup}." if backup.exists() else ""))
    out("next: pip install -r requirements-anthropic.txt (only if the Claude adapter is used); cd webui && npm ci && npm run build; run the tests.")
    out("note: nothing was deleted and git was not touched. Review with `git status` and `git diff`, then commit yourself.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", default=".", help="the checkout to change (default: the current directory)")
    parser.add_argument("--apply", action="store_true", help="write the files (without it: a demo that only shows the plan)")
    parser.add_argument("--force", action="store_true", help="also replace files that conflict (the originals are backed up first)")
    args = parser.parse_args(argv)
    return apply(Path(args.target), write=args.apply, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
