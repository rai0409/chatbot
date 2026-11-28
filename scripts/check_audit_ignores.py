"""CI helper: validate security/pip-audit-ignore.txt and print the pip-audit --ignore-vuln arguments.

Every ignored advisory carries a package, a reason, an expiry date and a reviewer role. An expired or malformed
entry fails (exit 1), so an ignore cannot silently outlive its review. An entry expiring within --warn-days is
reported on stderr.

  python scripts/check_audit_ignores.py                  # check only
  python scripts/check_audit_ignores.py --print-args     # check, then print "--ignore-vuln ID ..." on stdout
  IGNORES=$(python scripts/check_audit_ignores.py --print-args)   # in a `bash -e` step: fails the step on error

Input: security/pip-audit-ignore.txt (or --file); tab-separated columns
  id, package, reason, expires (YYYY-MM-DD), reviewer role; "#" starts a comment line.
Output: stdout (arguments) and stderr (findings). Writes no file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = ROOT / "security" / "pip-audit-ignore.txt"
COLUMNS = ("id", "package", "reason", "expires", "reviewer")
_ID = re.compile(r"^[A-Z][A-Z0-9]*-[A-Za-z0-9][A-Za-z0-9.:_-]*$")  # PYSEC-..., GHSA-..., CVE-...


def parse(text: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """Entries and errors (one per malformed line); the dates are checked in check()."""
    entries: List[Dict[str, str]] = []
    errors: List[str] = []
    seen = set()
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) != len(COLUMNS) or not all(parts):
            errors.append(f"line {number}: expected {len(COLUMNS)} non-empty tab-separated columns {COLUMNS}")
            continue
        entry = dict(zip(COLUMNS, parts))
        if not _ID.match(entry["id"]):
            errors.append(f"line {number}: invalid advisory id {entry['id']!r}")
            continue
        if entry["id"] in seen:
            errors.append(f"line {number}: duplicate advisory id {entry['id']}")
            continue
        seen.add(entry["id"])
        entry["line"] = str(number)
        entries.append(entry)
    return entries, errors


def check(entries: Sequence[Dict[str, str]], today: dt.date, warn_days: int = 14) -> Tuple[List[str], List[str]]:
    """(errors, warnings): an expired or undated entry is an error; one expiring soon is a warning."""
    errors: List[str] = []
    warnings: List[str] = []
    for entry in entries:
        try:
            expires = dt.date.fromisoformat(entry["expires"])
        except ValueError:
            errors.append(f"line {entry['line']}: {entry['id']}: expires {entry['expires']!r} is not YYYY-MM-DD")
            continue
        if expires < today:
            errors.append(f"line {entry['line']}: {entry['id']} ({entry['package']}) expired on {expires}: "
                          f"re-review it ({entry['reviewer']}) or remove it")
        elif (expires - today).days <= warn_days:
            warnings.append(f"line {entry['line']}: {entry['id']} ({entry['package']}) expires on {expires}")
    return errors, warnings


def ignore_args(entries: Sequence[Dict[str, str]]) -> List[str]:
    args: List[str] = []
    for entry in entries:
        args += ["--ignore-vuln", entry["id"]]
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the pip-audit ignore list.")
    parser.add_argument("--file", default=str(DEFAULT_FILE))
    parser.add_argument("--today", default=None, help="YYYY-MM-DD (tests); default: the current UTC date")
    parser.add_argument("--warn-days", type=int, default=14)
    parser.add_argument("--print-args", action="store_true", help="print the --ignore-vuln arguments on stdout")
    args = parser.parse_args(argv)
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.timezone.utc).date()
    path = Path(args.file)
    if not path.is_file():
        print(f"{path}: not found", file=sys.stderr)
        return 1
    entries, errors = parse(path.read_text(encoding="utf-8"))
    date_errors, warnings = check(entries, today, args.warn_days)
    for line in warnings:
        print(f"warning: {path.name}: {line}", file=sys.stderr)
    errors += date_errors
    if errors:
        for line in errors:
            print(f"error: {path.name}: {line}", file=sys.stderr)
        return 1
    if args.print_args:
        print(" ".join(ignore_args(entries)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
