"""Run golden cases through an answer function and judge each answer.

Besides the answer checks of rag_core.golden_qa, a row carries (when the case / answer provide them):
  retrieval      eval.bench retrieval metrics (hit@k, recall@k, mrr@k, ndcg@k) of the answer's retrieved chunks
                 against the case's gold_chunk_ids (match by chunk id) or else gold_doc_ids (by doc id)
  retrieved      the retrieved chunk/doc ids (ids only, at most RETRIEVED_LIMIT), for later re-scoring
  should_abstain the case's abstention label (should_abstain / expected_abstain), for the abstention confusion matrix
  stage_ms       per-stage latencies the answer function reports (e.g. retrieval, rerank, generation)
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from rag_core.golden_qa import evaluate_case, group_of

AnswerFn = Callable[[str], Mapping[str, Any]]
ANSWER_TEXT_LIMIT = 2000
RETRIEVED_LIMIT = 100
RETRIEVAL_KS = (1, 3, 5, 10, 20)


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
            "cited": [
                {"chunk_id": str(c.chunk_id or ""), "doc_id": str(c.source_doc or "")} for c in (result.citations or [])
            ],
            "retrieved": [
                {
                    "chunk_id": str((chunk.metadata or {}).get("id") or ""),
                    "doc_id": str((chunk.metadata or {}).get("doc_id") or (chunk.metadata or {}).get("source_doc") or ""),
                }
                for chunk in (result.retrieved or [])[:RETRIEVED_LIMIT]
            ],
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


def should_abstain(case: Mapping[str, Any]) -> Optional[bool]:
    """The abstention label of a case; None when the case does not say."""
    for key in ("should_abstain", "expected_abstain"):
        if key in case and case[key] is not None:
            return bool(case[key])
    if case.get("answerable") is not None:
        return not bool(case["answerable"])
    return None


def _retrieved(answer: Mapping[str, Any]) -> List[Dict[str, str]]:
    items = answer.get("retrieved") or []
    out: List[Dict[str, str]] = []
    for item in list(items)[:RETRIEVED_LIMIT]:
        if isinstance(item, Mapping):
            out.append({"chunk_id": str(item.get("chunk_id") or ""), "doc_id": str(item.get("doc_id") or "")})
        else:
            out.append({"chunk_id": str(item), "doc_id": ""})
    return out


def retrieval_metrics(case: Mapping[str, Any], retrieved: Sequence[Mapping[str, str]]) -> Optional[Dict[str, float]]:
    """eval.bench metrics of the retrieved ids against the case's gold ids; None when the case has no gold ids."""
    from eval.bench.dataset import EvalQuery, Relevant
    from eval.bench.metrics import RankedChunk, query_metrics

    chunk_ids = [str(c) for c in case.get("gold_chunk_ids") or [] if str(c).strip()]
    doc_ids = [str(d) for d in case.get("gold_doc_ids") or [] if str(d).strip()]
    if chunk_ids:
        mode, relevant = "chunk", tuple(Relevant(c, None, None, 1) for c in chunk_ids)
    elif doc_ids:
        mode, relevant = "doc", tuple(Relevant(None, d, None, 1) for d in doc_ids)
    else:
        return None
    query = EvalQuery(str(case.get("case_id")), str(case.get("question") or ""), "dev", "default", True, relevant, (), "")
    ranked = [RankedChunk(item.get("chunk_id", ""), item.get("doc_id", ""), "") for item in retrieved]
    metrics = query_metrics(ranked, query, mode, RETRIEVAL_KS)
    metrics.pop("first_relevant_rank", None)
    return metrics


def citation_precision(case: Mapping[str, Any], cited: Sequence[Mapping[str, str]]) -> Optional[float]:
    """Share of the answer's citations that point at a gold chunk (or gold doc when the case has only doc ids).

    None when the case has no gold ids or the answer cites nothing (an uncited answer is caught by no_citation)."""
    chunk_ids = {str(c) for c in case.get("gold_chunk_ids") or []}
    doc_ids = {str(d) for d in case.get("gold_doc_ids") or []}
    if not cited or not (chunk_ids or doc_ids):
        return None
    good = sum(1 for c in cited if (chunk_ids and c.get("chunk_id") in chunk_ids) or (not chunk_ids and c.get("doc_id") in doc_ids))
    return good / len(cited)


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
                    "retrieved": _retrieved(answer),
                    "cited": _retrieved({"retrieved": answer.get("cited")}),
                    "stage_ms": dict(answer.get("stage_ms") or {}),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one broken case must not stop the run
            row = _error_row(case, exc, (time.perf_counter() - started) * 1000)
        row["retrieval"] = retrieval_metrics(case, row.get("retrieved") or [])
        row["citation_precision"] = citation_precision(case, row.get("cited") or [])
        row.update(
            {
                "should_abstain": should_abstain(case),
                "slice": case.get("expected_question_type") or "(none)",
                "group": group_of(case),
                "priority": case.get("priority", 0),
            }
        )
        rows.append(row)
    return rows
