"""Judge one candidate against the baseline on a set of golden cases, within the evaluation budget."""
from __future__ import annotations

import contextlib
import uuid
from pathlib import Path
from typing import Any, ContextManager, Dict, Mapping, Optional, Sequence

from eval.tenant_eval import ledger
from eval.tenant_eval.gate import Policy, compare
from eval.tenant_eval.runner import AnswerFn, eval_set_digest, run_cases


def evaluate_candidate(
    cases: Sequence[Mapping[str, Any]],
    baseline_answer: AnswerFn,
    candidate_answer: AnswerFn,
    *,
    proposal_id: str,
    policy: Policy = Policy(),
    conditions: Optional[Mapping[str, Any]] = None,
    budget: int = ledger.DEFAULT_BUDGET,
    ledger_file: Optional[Path] = None,
    run_id: Optional[str] = None,
    candidate_context: Optional[ContextManager[Any]] = None,
) -> Dict[str, Any]:
    """Check the budget, run the baseline and the candidate on the same cases, gate, and record in the ledger.

    `cases` are the cases to judge on: leave out the ones written for this change (golden_qa.exclude_motivating) and
    use a fold that was not tuned on (golden_qa.split_by_fold). BudgetError is raised before anything runs.
    The candidate run happens inside `candidate_context` (conditions.candidate_environment)."""
    if not cases:
        raise ValueError("there are no cases to evaluate")
    digest = eval_set_digest(cases)
    remaining = ledger.check_budget(digest, budget=budget, path=ledger_file)
    baseline_rows = run_cases(cases, baseline_answer)
    with candidate_context if candidate_context is not None else contextlib.nullcontext():
        candidate_rows = run_cases(cases, candidate_answer)
    gate = compare(baseline_rows, candidate_rows, policy)
    entry = ledger.record(
        {
            "run_id": run_id or uuid.uuid4().hex[:12],
            "proposal_id": proposal_id,
            "eval_set_sha256": digest,
            "n_cases": len(cases),
            "budget_remaining_after": remaining - 1,
            "conditions": dict(conditions or {}),
            "decision": gate["decision"],
            "checks": gate["checks"],
            "baseline": gate["baseline"],
            "candidate": gate["candidate"],
            "policy": gate["policy"],
        },
        path=ledger_file,
    )
    return {"gate": gate, "baseline_rows": baseline_rows, "candidate_rows": candidate_rows, "ledger_entry": entry}
