#!/usr/bin/env python3
from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Judge a proposed change against the golden cases of a profile.

  python scripts/tenant_eval.py evaluate --profile ID --proposal NAME
        [--candidate-version V] [--knob NAME=VALUE ...] [--tenant T]
        [--folds K --held-out N [--seed S]] [--exclude-change CHANGE_ID] [--budget 20] [--report FILE]
  python scripts/tenant_eval.py ledger

evaluate runs the golden cases through the pipeline twice: as it is (baseline) and with the candidate (a stored profile
version, see scripts/profile_versions.py, and/or registered knob values) in this process. It refuses when the
evaluation budget of this set of cases is spent or when the two runs differ in anything but the declared change.
Exit code: 0 pass, 1 fail, 2 insufficient, 3 refused or bad input.
"""

import argparse
import json
from typing import Any, Dict, List, Optional

from eval.tenant_eval import conditions, evaluate, ledger, perturb, report
from eval.tenant_eval.gate import Policy
from eval.tenant_eval.runner import pipeline_answer_fn
from rag_core import golden_qa, knob_registry, profile_loader, profile_store

EXIT = {"pass": 0, "fail": 1, "insufficient": 2}  # nosec B105 - decision labels, not credentials


def _parse_knobs(items: List[str]) -> Dict[str, Any]:
    knobs: Dict[str, Any] = {}
    for item in items:
        name, sep, raw = item.partition("=")
        if not sep:
            raise ValueError(f"--knob expects NAME=VALUE, got '{item}'")
        spec = knob_registry.knob(name)
        text = raw.strip()
        if spec.kind == "bool":
            if text.lower() not in {"true", "false"}:
                raise ValueError(f"{name}: expected true or false")
            knobs[name] = text.lower() == "true"
        else:
            try:
                knobs[name] = int(text) if spec.kind == "int" else float(text)
            except ValueError:
                raise ValueError(f"{name}: expected a number") from None
    return knob_registry.check_change(knobs) if knobs else {}


def _select_cases(args) -> List[Dict[str, Any]]:
    cases, problems = golden_qa.load_cases(profile_loader.load_rag_profile(args.profile))
    if problems:
        raise ValueError("the golden cases have problems: " + "; ".join(problems))
    if args.exclude_change:
        cases, _left_out = golden_qa.exclude_motivating(cases, args.exclude_change)
    if args.folds:
        if args.held_out is None:
            raise ValueError("--folds needs --held-out")
        _rest, cases = golden_qa.split_by_fold(cases, args.folds, args.held_out, seed=args.seed)
    if not cases:
        raise ValueError("there are no cases to evaluate after the selection")
    return cases


def _evaluate(args) -> int:
    knobs = _parse_knobs(args.knob)
    if not args.candidate_version and not knobs:
        raise ValueError("a candidate needs --candidate-version and/or --knob")
    cases = _select_cases(args)
    if args.perturb:
        kinds = [k.strip() for k in args.perturb_kinds.split(",") if k.strip()] or list(perturb.KINDS)
        unknown = sorted(set(kinds) - set(perturb.KINDS))
        if unknown:
            raise ValueError(f"unknown perturbation kind(s): {', '.join(unknown)} (one of: {', '.join(perturb.KINDS)})")
        cases = cases + perturb.perturb_cases(cases, kinds=kinds, seed=args.seed)
    baseline_conditions = conditions.collect(args.profile, tenant_id=args.tenant)
    context = conditions.candidate_environment(args.profile, version=args.candidate_version, knobs=knobs)
    with context:
        candidate_conditions = conditions.collect(args.profile, tenant_id=args.tenant)
    declared: Dict[str, Any] = {"knobs": knobs} if knobs else {}
    if args.candidate_version:
        declared["profile_version"] = args.candidate_version
    conditions.assert_declared_changes(baseline_conditions, candidate_conditions, declared)

    answer = pipeline_answer_fn(profile_id=args.profile, tenant_id=args.tenant)
    out = evaluate.evaluate_candidate(
        cases, answer, answer,
        proposal_id=args.proposal,
        policy=Policy(),
        conditions={"baseline": baseline_conditions, "candidate": candidate_conditions, "declared": declared},
        budget=args.budget,
        candidate_context=conditions.candidate_environment(args.profile, version=args.candidate_version, knobs=knobs),
    )
    gate = out["gate"]
    markdown = report.to_markdown(
        gate, title=f"評価レポート: {args.proposal}",
        baseline_rows=out["baseline_rows"], candidate_rows=out["candidate_rows"],
        baseline_conditions=baseline_conditions, candidate_conditions=candidate_conditions,
    )
    if args.perturb:
        markdown += perturb.robustness_markdown(out["baseline_rows"], out["candidate_rows"])
    if args.report:
        target = _Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
    print(json.dumps({"decision": gate["decision"], "ledger_seq": out["ledger_entry"]["seq"],
                      "run_id": out["ledger_entry"]["run_id"], "n_cases": len(cases), "report": args.report or None},
                     ensure_ascii=False, indent=2))
    return EXIT[gate["decision"]]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--profile", required=True)
    ev.add_argument("--proposal", required=True)
    ev.add_argument("--tenant", default="default")
    ev.add_argument("--candidate-version", default=None)
    ev.add_argument("--knob", action="append", default=[])
    ev.add_argument("--folds", type=int, default=None)
    ev.add_argument("--held-out", type=int, default=None)
    ev.add_argument("--seed", default="")
    ev.add_argument("--exclude-change", default=None)
    ev.add_argument("--budget", type=int, default=ledger.DEFAULT_BUDGET)
    ev.add_argument("--report", default=None)
    ev.add_argument("--perturb", action="store_true", help="add perturbed variants of the selected cases")
    ev.add_argument("--perturb-kinds", default="", help="comma-separated kinds (default: all)")
    sub.add_parser("ledger")
    args = parser.parse_args(argv)

    try:
        if args.command == "ledger":
            count = ledger.verify_chain()
            rows = [{key: row.get(key) for key in ("seq", "at", "proposal_id", "decision", "n_cases", "eval_set_sha256")}
                    for row in ledger.read_ledger()]
            print(json.dumps({"entries": count, "chain": "ok", "rows": rows}, ensure_ascii=False, indent=2))
            return 0
        return _evaluate(args)
    except (ledger.LedgerError, conditions.ConditionsError, knob_registry.KnobError, profile_store.ProfileStoreError,
            ValueError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
