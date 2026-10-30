#!/usr/bin/env python3
from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Candidates for approved_aliases: other ways to ask an approved question.

  python scripts/alias_candidates.py coverage --tenant T [--approved FILE] [--kinds a,b] [--seed S] [--out FILE]
  python scripts/alias_candidates.py mine --tenant T --questions FILE [--approved FILE] [--threshold 0.5] [--out FILE]

coverage perturbs every approved question and reports how many variants the lookup already answers, per kind, and writes
the misses of the kinds people really type (casual_ending, keywords_only, katakana_hiragana, spacing; --kinds
overrides) as candidates. mine reads user questions that found no approved answer (one per line, a file you supply)
and writes the nearest approved question for each one that is similar enough.
The output is a JSONL of candidates with status "pending" (default: RUNS_DIR/alias_candidates/<tenant>_<command>.jsonl).
The approved QA file is never changed and cannot be the output; a person checks that the approved answer answers the
wording and adds it to approved_aliases. Exit code 0, or 3 for bad input. The questions file may hold real user text:
keep it, and the output, inside the repository's ignored runs folder.
"""

import argparse
import json
from typing import List, Optional

import config
from eval.tenant_eval import aliases, perturb
from rag_core.approved_qa import load_approved_qa


def _kinds(raw: str, default):
    kinds = [k.strip() for k in raw.split(",") if k.strip()] or list(default)
    unknown = sorted(set(kinds) - set(perturb.KINDS))
    if unknown:
        raise ValueError(f"unknown perturbation kind(s): {', '.join(unknown)}")
    return kinds


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("coverage", "mine"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--tenant", default="default")
        cmd.add_argument("--approved", default=None)
        cmd.add_argument("--out", default=None)
    cov = sub.choices["coverage"]
    cov.add_argument("--kinds", default="")
    cov.add_argument("--seed", default="")
    mine = sub.choices["mine"]
    mine.add_argument("--questions", required=True)
    mine.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args(argv)

    try:
        approved = _Path(args.approved or config.APPROVED_QA_PATH)
        out = _Path(args.out) if args.out else _Path(config.RUNS_DIR) / "alias_candidates" / f"{args.tenant}_{args.command}.jsonl"
        if out.resolve() == approved.resolve():
            raise ValueError("the output cannot be the approved QA file")
        index = load_approved_qa(approved, tenant_id=args.tenant)
        if args.command == "coverage":
            report = aliases.coverage(index, tenant_id=args.tenant, kinds=_kinds("", perturb.KINDS), seed=args.seed)
            rows = aliases.candidates_from_coverage(
                index, report, tenant_id=args.tenant, kinds=_kinds(args.kinds, aliases.DEFAULT_ALIAS_KINDS)
            )
            summary = {k: report[k] for k in ("approved", "variants", "covered", "covered_rate", "by_kind")}
        else:
            if not 0 < args.threshold <= 1:
                raise ValueError("--threshold must be in (0, 1]")
            questions = _Path(args.questions).read_text(encoding="utf-8").splitlines()
            rows = aliases.mine(index, questions, tenant_id=args.tenant, threshold=args.threshold)
            summary = {"approved": len(index.records), "questions": len([q for q in questions if q.strip()])}
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 3
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(json.dumps({**summary, "candidates": len(rows), "out": str(out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
