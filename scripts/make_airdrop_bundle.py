#!/usr/bin/env python3
"""Packs every change of this work tree against HEAD (changed and new files) into ONE self-contained python file.

  python3 scripts/make_airdrop_bundle.py                  # writes airdrop_out/airdrop_demo_apply.py
  python3 scripts/make_airdrop_bundle.py --out some/dir/apply.py

The output is scripts/airdrop_apply.py with the files inside. Send that one file to the other machine (AirDrop, for example) and run it
there; by default it only shows what it would do. The output stays inside the repository (airdrop_out/); it is not tracked and not packed.
Not packed: build output, caches, virtual environments, the 即削除 folder, and files over 5 MB. A deleted or renamed file stops the
run: the bundle never deletes, so such a change must be made by a person.
Standard library only (and git).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import re
import subprocess  # nosec B404 - runs git with fixed arguments, no shell
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts" / "airdrop_apply.py"
SKIP_PARTS = {".venv", "node_modules", "dist", ".npm_cache", "__pycache__", ".git", "即削除", "airdrop_out", ".airdrop_backup"}
SKIP_PREFIXES = (".pytest_tmp", ".s7_", ".routes_", ".airdrop_backup_")
MAX_BYTES = 5 * 1024 * 1024
MARK_START, MARK_END = "# --- bundle (filled in by scripts/make_airdrop_bundle.py) ---", "# --- end of bundle ---"
_EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ALLOWED_DOMAINS = (b"example.co.jp", b"example.com", b"example.org", b"example.net")


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout  # nosec B603 B607


def _skipped(rel: str) -> bool:
    parts = rel.split("/")
    return bool(SKIP_PARTS.intersection(parts)) or any(parts[0].startswith(p) for p in SKIP_PREFIXES)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect(root: Path) -> List[Dict[str, Any]]:
    """The files to pack: {path, status (M or A), base_sha256 (M only), new_sha256, raw_sha256, data}."""
    files: List[Dict[str, Any]] = []
    stopped: List[str] = []
    for line in _git(root, "diff", "--name-status", "HEAD").decode("utf-8").splitlines():
        status, _, path = line.partition("\t")
        if status not in ("M", "A"):
            stopped.append(line)
    if stopped:
        raise SystemExit("a deleted or renamed file cannot be carried (the bundle never deletes):\n  " + "\n  ".join(stopped))
    changed = [p for p in _git(root, "diff", "--name-only", "--diff-filter=AM", "HEAD", "-z").decode("utf-8").split("\0") if p]
    untracked = [p for p in _git(root, "ls-files", "-o", "--exclude-standard", "-z").decode("utf-8").split("\0") if p]
    for rel in sorted(set(changed) | set(untracked)):
        if _skipped(rel) or not (root / rel).is_file():
            continue
        data = (root / rel).read_bytes()
        if len(data) > MAX_BYTES:
            print(f"skipped (over 5 MB): {rel}", file=sys.stderr)
            continue
        entry: Dict[str, Any] = {"path": rel, "status": "A" if rel in untracked else "M", "raw_sha256": _sha(data),
                                 "new_sha256": _sha(data.replace(b"\r\n", b"\n")), "data": data}
        if entry["status"] == "M":
            base = _git(root, "show", f"HEAD:{rel}")
            entry["base_sha256"] = _sha(base.replace(b"\r\n", b"\n"))
        files.append(entry)
    return files


def email_warnings(files: List[Dict[str, Any]]) -> List[str]:
    """Addresses other than the reserved example domains: the bundle must not carry real ones (pattern only, no names)."""
    found: List[str] = []
    for entry in files:
        if entry["path"].endswith((".png", ".ico", ".woff", ".woff2", ".zip")):
            continue
        for match in _EMAIL.findall(entry["data"]):
            if not match.lower().endswith(_ALLOWED_DOMAINS) and not match.lower().startswith(b"git@"):
                found.append(f"{entry['path']}: an address outside the reserved example domains")
                break
    return found


def build(root: Path, out: Path, *, now: Optional[str] = None) -> Dict[str, Any]:
    files = collect(root)
    if not files:
        raise SystemExit("nothing to pack: the work tree equals HEAD")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for entry in files:
            info = zipfile.ZipInfo(entry["path"], date_time=(2026, 1, 1, 0, 0, 0))  # a fixed date: the same files give the same bundle
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, entry["data"])
    blob = buffer.getvalue()
    meta = {
        "base_commit": _git(root, "rev-parse", "HEAD").decode("ascii").strip(),
        "created": now or time.strftime("%Y-%m-%d %H:%M"),
        "payload_sha256": _sha(blob),
        "files": [{k: v for k, v in e.items() if k != "data"} for e in files],
    }
    encoded = base64.b64encode(blob).decode("ascii")
    lines = [encoded[i:i + 100] for i in range(0, len(encoded), 100)]
    block = (f"{MARK_START}\nBUNDLE_META: Dict[str, Any] = {meta!r}\nBUNDLE_B64 = (\n"
             + "\n".join(f'    "{line}"' for line in lines) + f"\n)\n{MARK_END}")
    template = TEMPLATE.read_text(encoding="utf-8")
    start, end = template.index(MARK_START), template.index(MARK_END) + len(MARK_END)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(template[:start] + block + template[end:], encoding="utf-8")
    meta["warnings"] = email_warnings(files)
    meta["bytes"] = out.stat().st_size
    return meta


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "airdrop_out" / "airdrop_demo_apply.py"))
    args = parser.parse_args(argv)
    meta = build(ROOT, Path(args.out))
    print(f"wrote {args.out}: {len(meta['files'])} files ({sum(1 for f in meta['files'] if f['status'] == 'M')} changed, "
          f"{sum(1 for f in meta['files'] if f['status'] == 'A')} new), {meta['bytes'] // 1024} KB, base {meta['base_commit'][:12]}")
    for warning in meta["warnings"]:
        print(f"WARNING {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
