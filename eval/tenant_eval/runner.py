"""Run golden cases through an answer function and judge each answer."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from rag_core.golden_qa import evaluate_case, group_of

AnswerFn = Callable[[str], Mapping[str, Any]]
ANSWER_TEXT_LIMIT = 2000


def eval_set_digest(cases: Sequence[Mapping[str, Any]]) -> str:
    """SHA-256 of the cases (order does not matter): the identity of an evaluation set."""
    rows = sorted((dict(case) for case in cases), key=lambda case: case["case_id"])
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def pipeline_answer_fn(
    *, profile_id: str, tenant_id: str = "default", top_k: int = 20, max_context_chars: int = 8000, client: Any = None
) -> AnswerFn:
    """An answer function over the real pipeline (needs an index; tests use fakes)."""

    def answer(question: str) -> Dict[str, Any]:
        from rag_core import qa

        result, trace = qa.answer_query_with_trace(
            question,
            client=client,
            top_k=top_k,
            max_context_chars=max_context_chars,
            tenant_id=tenant_id,
            rag_profile_id=profile_id,
        )
        return {
            "answer_text": result.answer_text,
            "has_citation": bool(result.citations),
            "question_type": str(trace.get("question_type") or ""),
            "guard_reason": result.guard_reason,
            "top_distance": result.retrieved[0].score if result.retrieved else None,
        }

    return answer


def _error_row(case: Mapping[str, Any], exc: Exception, latency_ms: float) -> Dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "passed": False,
        "failures": ["error"],
        "missing_all": [],
        "missing_any": [],
        "forbidden_hits": [],
        "error": type(exc).__name__,  # the message may carry paths
        "answered": False,
        "guard_reason": None,
        "question_type": "",
        "answer_text": "",
        "top_distance": None,
        "latency_ms": latency_ms,
    }


def run_cases(cases: Sequence[Mapping[str, Any]], answer_fn: AnswerFn) -> List[Dict[str, Any]]:
    """One row per case (in the order given). A case whose answer function raises becomes an "error" row."""
    rows: List[Dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        try:
            answer = answer_fn(case["question"])
            latency_ms = (time.perf_counter() - started) * 1000
            text = str(answer.get("answer_text") or "")
            guard_reason: Optional[str] = answer.get("guard_reason") or None
            delivered = "" if guard_reason else text  # a guarded answer is not shown to the user
            row = evaluate_case(
                case,
                answer_text=delivered,
                has_citation=bool(answer.get("has_citation")),
                question_type=str(answer.get("question_type") or ""),
            )
            row.update(
                {
                    "answered": bool(delivered.strip()),
                    "guard_reason": guard_reason,
                    "question_type": str(answer.get("question_type") or ""),
                    "answer_text": text[:ANSWER_TEXT_LIMIT],
                    "top_distance": answer.get("top_distance"),
                    "latency_ms": latency_ms,
                }
            )
        except Exception as exc:  # noqa: BLE001 - one broken case must not stop the run
            row = _error_row(case, exc, (time.perf_counter() - started) * 1000)
        row.update(
            {
                "slice": case.get("expected_question_type") or "(none)",
                "group": group_of(case),
                "priority": case.get("priority", 0),
            }
        )
        rows.append(row)
    return rows
