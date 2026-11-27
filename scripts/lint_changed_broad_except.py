"""CI helper: BLE001 / S110 (ruff) only on Python lines added or changed against a base ref.

Legacy code is not linted: findings on lines the branch did not touch are ignored, so the check can be adopted
without fixing the whole tree. A broad except that stays on purpose carries `# noqa: BLE001  <reason>`.

  python scripts/lint_changed_broad_except.py [--base origin/main]

Input: git diff against <base>...HEAD plus the working tree (committed, staged, unstaged and untracked .py files).
Output: findings on stdout; exit 1 when there is any, 0 otherwise. Writes no file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Dict, List, Set

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELECT = "BLE001,S110"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=True).stdout


def added_lines(base: str) -> Dict[str, Set[int]]:
    """path -> line numbers added/changed in the working tree relative to the merge base with `base`."""
    merge_base = _git("merge-base", base, "HEAD").strip()
    added: Dict[str, Set[int]] = {}
    current = None
    for line in _git("diff", "--diff-filter=AM", "-U0", merge_base, "--", "*.py").splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            added[current] = set()
            continue
        match = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)
        if match and current:
            start, count = int(match.group(1)), int(match.group(2) or 1)
            added[current].update(range(start, start + count))
    for path in _git("ls-files", "--others", "--exclude-standard", "--", "*.py").split():
        with open(os.path.join(ROOT, path), encoding="utf-8") as stream:
            added[path] = set(range(1, len(stream.readlines()) + 1))
    return added


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(argv)
    added = added_lines(args.base)
    files = sorted(path for path in added if os.path.exists(os.path.join(ROOT, path)))
    if not files:
        print("no changed Python files")
        return 0
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", SELECT, "--output-format", "json", *files],
        cwd=ROOT, text=True, capture_output=True,
    )
    if proc.returncode not in (0, 1):
        print(proc.stderr or proc.stdout)
        return 2
    findings = json.loads(proc.stdout or "[]")
    bad = []
    for item in findings:
        rel = os.path.relpath(item["filename"], ROOT)
        row = item["location"]["row"]
        if row in added.get(rel, ()):
            bad.append(f"{rel}:{row}: {item['code']} {item['message']}")
    for entry in bad:
        print(entry)
    print(f"{len(bad)} finding(s) on changed lines ({len(findings) - len(bad)} on untouched legacy lines ignored)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
