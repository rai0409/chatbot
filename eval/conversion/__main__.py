"""python -m eval.conversion <command>

run       convert every document of a fixture directory that has a *.truth.json and score it
          python -m eval.conversion run --fixtures tests/fixtures/conversion \
              --out eval/conversion/out/run.json [--markdown eval/conversion/out/run.md] \
              [--thresholds eval/conversion/thresholds_v1.json] [--root PATH]
          --root: score another code tree (e.g. a `git worktree` of the base commit) with the same fixtures
          exit 1 when a threshold is violated or a document fails to convert (with --thresholds)
compare   per-document / per-metric deltas of two runs (regressions listed)
          python -m eval.conversion compare --base before.json --candidate after.json \
              [--tolerance 0.0] [--out compare.json] [--fail-on-regression]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from eval.conversion import harness


def _write(path: Optional[Path], text: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.conversion", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("--fixtures", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--markdown", type=Path)
    p.add_argument("--thresholds", type=Path)
    p.add_argument("--root", type=Path, help="code tree whose converters are scored (default: this repository)")
    p = sub.add_parser("compare")
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--tolerance", type=float, default=0.0)
    p.add_argument("--out", type=Path)
    p.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "run":
        result = harness.run(args.fixtures, code_root=args.root)
        violations = []
        if args.thresholds:
            thresholds = json.loads(args.thresholds.read_text(encoding="utf-8"))
            violations = harness.check_thresholds(result, thresholds)
            result["thresholds"] = {"file": str(args.thresholds), "violations": violations}
        _write(args.out, json.dumps(result, ensure_ascii=False, indent=1) + "\n")
        _write(args.markdown, harness.markdown(result))
        print(harness.markdown(result))
        for line in violations:
            print(f"THRESHOLD: {line}", file=sys.stderr)
        return 1 if violations else 0

    base = json.loads(args.base.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    result = harness.compare(base, candidate, tolerance=args.tolerance)
    _write(args.out, json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    if not result["same_fixtures"]:
        print("WARNING: the two runs used different fixtures (fixtures_sha256 differs)", file=sys.stderr)
    for row in result["overall"]:
        print(f"{row['metric']:<42} {row['base']!s:>10} -> {row['candidate']!s:>10}  delta={row['delta']}")
    for line in result["improvements"]:
        print(f"IMPROVED  {line}")
    for line in result["regressions"]:
        print(f"REGRESSED {line}")
    return 1 if (args.fail_on_regression and result["regressions"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
