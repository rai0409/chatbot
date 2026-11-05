"""Replay questions through a baseline and a candidate and compare what each would have answered."""
from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List, Mapping, Optional, Sequence

from eval.bench.stats import percentile
from eval.tenant_eval.runner import AnswerFn
from eval.tenant_eval.stats import clopper_pearson_upper
from rag_core.profile_engine import normalize_text

_SPACE = re.compile(r"\s+")


def _same(a: str, b: str) -> bool:
    return _SPACE.sub("", normalize_text(a)) == _SPACE.sub("", normalize_text(b))


def _run(fn: AnswerFn, question: str) -> Dict[str, Any]:
    import time

    started = time.perf_counter()
    try:
        answer = fn(question)
    except Exception as exc:  # noqa: BLE001 - one broken question must not stop the replay
        return {"error": type(exc).__name__, "answered": False, "text": "", "citation": False, "distance": None,
                "latency_ms": (time.perf_counter() - started) * 1000}
    text = str(answer.get("answer_text") or "")
    guarded = bool(answer.get("guard_reason"))
    return {"error": None, "answered": bool(text.strip()) and not guarded, "text": text,
            "citation": bool(answer.get("has_citation")), "distance": answer.get("top_distance"),
            "latency_ms": (time.perf_counter() - started) * 1000}


def compare_runs(questions: Sequence[str], baseline_fn: AnswerFn, candidate_fn: AnswerFn) -> Dict[str, Any]:
    """Run every question through both functions. Rows carry an index, never the question or the answer text."""
    rows: List[Dict[str, Any]] = []
    for index, question in enumerate(questions):
        base, cand = _run(baseline_fn, question), _run(candidate_fn, question)
        both = base["answered"] and cand["answered"]
        delta = (
            cand["distance"] - base["distance"]
            if base["distance"] is not None and cand["distance"] is not None else None
        )
        rows.append({
            "id": f"q{index:05d}",
            "baseline_error": base["error"], "candidate_error": cand["error"],
            "newly_abstained": base["answered"] and not cand["answered"] and cand["error"] is None,
            "newly_answered": cand["answered"] and not base["answered"],
            "changed_answer": both and not _same(base["text"], cand["text"]),
            "citation_lost": base["answered"] and base["citation"] and cand["answered"] and not cand["citation"],
            "citation_gained": both and cand["citation"] and not base["citation"],
            "distance_delta": delta,
            "baseline_latency_ms": base["latency_ms"], "candidate_latency_ms": cand["latency_ms"],
        })
    n = len(rows)

    def count(key: str) -> int:
        return sum(1 for row in rows if row[key])

    deltas = sorted(abs(row["distance_delta"]) for row in rows if row["distance_delta"] is not None)
    base_latency = [row["baseline_latency_ms"] for row in rows]
    cand_latency = [row["candidate_latency_ms"] for row in rows]
    disagree = sum(1 for row in rows if row["changed_answer"] or row["newly_abstained"] or row["newly_answered"])
    newly_abstained = count("newly_abstained")
    summary = {
        "n": n,
        "candidate_errors": sum(1 for row in rows if row["candidate_error"]),
        "baseline_errors": sum(1 for row in rows if row["baseline_error"]),
        "changed_answer": count("changed_answer"),
        "newly_abstained": newly_abstained,
        "newly_abstained_rate": newly_abstained / n if n else None,
        "newly_abstained_upper": clopper_pearson_upper(newly_abstained, n) if n else None,
        "newly_answered": count("newly_answered"),
        "citation_lost": count("citation_lost"),
        "citation_gained": count("citation_gained"),
        "disagreement_rate": disagree / n if n else None,
        "distance_delta_abs_median": statistics.median(deltas) if deltas else None,
        "distance_delta_abs_p95": percentile(deltas, 0.95) if deltas else None,
        "latency_ratio_median": (
            statistics.median(cand_latency) / statistics.median(base_latency)
            if n and statistics.median(base_latency) > 0 else None
        ),
    }
    return {"rows": rows, "summary": summary}


def verdict(
    summary: Mapping[str, Any], *, max_new_abstain_rate: float = 0.02, max_disagreement_rate: float = 0.3,
    max_citation_lost_rate: float = 0.02, min_questions: int = 100,
) -> Dict[str, Any]:
    """"ok" when nothing needs a person's attention, "review" with the reasons, "insufficient" below min_questions."""
    n = summary["n"]
    if n < min_questions:
        return {"decision": "insufficient", "reasons": [f"{n} questions (at least {min_questions} needed)"]}
    reasons: List[str] = []
    if summary["candidate_errors"] > summary["baseline_errors"]:
        reasons.append(f"the candidate raised errors on {summary['candidate_errors']} questions")
    if summary["newly_abstained_rate"] > max_new_abstain_rate:
        reasons.append(f"{summary['newly_abstained']} answers became abstentions "
                       f"({summary['newly_abstained_rate'] * 100:.1f}% > {max_new_abstain_rate * 100:.1f}%)")
    if summary["citation_lost"] / n > max_citation_lost_rate:
        reasons.append(f"{summary['citation_lost']} answers lost their citation")
    if summary["disagreement_rate"] > max_disagreement_rate:
        reasons.append(f"{summary['disagreement_rate'] * 100:.0f}% of the answers differ "
                       f"(> {max_disagreement_rate * 100:.0f}%)")
    return {"decision": "review" if reasons else "ok", "reasons": reasons}


def evidence_text(summary: Mapping[str, Any], decision: Mapping[str, Any]) -> str:
    """One line for the evidence of the "shadow" step of a proposal."""
    def pct(value: Optional[float]) -> str:
        return "-" if value is None else f"{value * 100:.1f}%"

    return (
        f"影子比較 {summary['n']} 件: 判定 {decision['decision']}、回答の相違 {pct(summary['disagreement_rate'])}、"
        f"新たに棄権 {summary['newly_abstained']} 件、出典喪失 {summary['citation_lost']} 件、"
        f"候補のエラー {summary['candidate_errors']} 件"
        + ("、理由: " + "; ".join(decision["reasons"]) if decision["reasons"] else "")
    )
