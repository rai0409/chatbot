"""Abstention calibration and rerank-skip thresholds.

Inputs come from the per-query rows of a bench run: a confidence score
(reranker score of the top answer, or the fused score), whether answering
with the top result would be wrong (unanswerable query, or top result not
relevant), and for skip analysis the fused top1-top2 margin.
- isotonic_fit: monotone score -> probability map (pool adjacent violators)
- cost_threshold: answer iff P(correct) >= 1 - c_abstain / c_wrong
- conformal_risk_threshold: smallest score threshold whose finite-sample
  bound on P(answered and wrong) is <= alpha (conformal risk control; the
  guarantee is marginal over queries, not conditional on answering)
- risk_coverage: selective risk per coverage level and its area (AURC)
- margin_skip_analysis: how often reranking could be skipped for at most a
  given loss in hit@k
"""

from __future__ import annotations

import bisect
from typing import Dict, List, Sequence, Tuple


def isotonic_fit(scores: Sequence[float], labels: Sequence[int]) -> Tuple[List[float], List[float]]:
    """Non-decreasing step function: returns (block upper scores, block probabilities)."""
    pairs = sorted(zip(scores, labels))
    blocks: List[List[float]] = []  # [sum_labels, count, max_score]
    for score, label in pairs:
        blocks.append([float(label), 1.0, float(score)])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            total, count, top = blocks.pop()
            blocks[-1][0] += total
            blocks[-1][1] += count
            blocks[-1][2] = top
    return [b[2] for b in blocks], [b[0] / b[1] for b in blocks]


def isotonic_predict(model: Tuple[List[float], List[float]], score: float) -> float:
    uppers, probs = model
    if not uppers:
        return float("nan")
    return probs[min(bisect.bisect_left(uppers, score), len(probs) - 1)]


def expected_calibration_error(probs: Sequence[float], labels: Sequence[int], bins: int = 10) -> float:
    total = len(probs)
    if total == 0:
        return float("nan")
    ece = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probs) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if idx:
            confidence = sum(probs[i] for i in idx) / len(idx)
            accuracy = sum(labels[i] for i in idx) / len(idx)
            ece += len(idx) / total * abs(confidence - accuracy)
    return ece


def cost_threshold(cost_wrong: float, cost_abstain: float) -> float:
    if cost_wrong <= 0 or cost_abstain < 0:
        raise ValueError("cost_wrong must be > 0 and cost_abstain >= 0")
    return max(0.0, 1.0 - cost_abstain / cost_wrong)


def conformal_risk_threshold(scores: Sequence[float], wrong: Sequence[int], alpha: float) -> Dict[str, float]:
    """Answer when score >= threshold; loss_i = wrong_i if answered else 0."""
    n = len(scores)
    if n == 0:
        raise ValueError("calibration set is empty")
    candidates = sorted(set(scores)) + [float("inf")]
    for threshold in candidates:
        answered = [i for i in range(n) if scores[i] >= threshold]
        risk = sum(wrong[i] for i in answered) / n
        if (n * risk + 1.0) / (n + 1) <= alpha:
            coverage = len(answered) / n
            selective = sum(wrong[i] for i in answered) / len(answered) if answered else 0.0
            return {"threshold": threshold, "coverage": coverage, "risk": risk, "selective_risk": selective}
    return {"threshold": float("inf"), "coverage": 0.0, "risk": 0.0, "selective_risk": 0.0}


def risk_coverage(scores: Sequence[float], wrong: Sequence[int]) -> Dict[str, object]:
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    curve = []
    errors = 0
    for answered, i in enumerate(order, start=1):
        errors += wrong[i]
        curve.append((answered / len(order), errors / answered))
    aurc = sum(risk for _, risk in curve) / len(curve) if curve else float("nan")
    return {"aurc": aurc, "curve": curve}


def margin_skip_analysis(
    margins: Sequence[float], hit_without_rerank: Sequence[float], hit_with_rerank: Sequence[float], max_loss: float
) -> Dict[str, float]:
    """Largest skip rate whose loss in hit@k (vs always reranking) stays <= max_loss.

    Skipping applies when margin >= threshold; loss counts queries where the
    reranked list had a hit and the un-reranked list did not.
    """
    n = len(margins)
    best = {"threshold": float("inf"), "skip_rate": 0.0, "hit_loss": 0.0}
    for threshold in sorted(set(margins), reverse=True):
        skipped = [i for i in range(n) if margins[i] >= threshold]
        loss = sum(1 for i in skipped if hit_with_rerank[i] > hit_without_rerank[i]) / n
        if loss <= max_loss and len(skipped) / n >= best["skip_rate"]:
            best = {"threshold": threshold, "skip_rate": len(skipped) / n, "hit_loss": loss}
    return best
