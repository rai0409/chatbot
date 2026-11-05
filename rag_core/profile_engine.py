"""Run the retrieval_boost_rules of an exported RAG profile.

A rule applies when its question type is the question's and, if it lists trigger words, the question contains one.
Its terms are positive_terms / negative_terms plus the term and aliases of every enabled domain term in its
positive_categories / negative_categories. A chunk whose text contains any positive term gets +|positive_weight|
points, any negative term -|negative_weight| (one hit per side and rule, however many terms match); the points of
all applying rules add up. Points are on a 0-100 scale and become a change of the chunk score (a distance, lower is
better): score -= max_delta * clamp(points / 100, -1, 1). Matching is on NFKC-normalized, case-folded text.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Sequence, Tuple

from rag_core.retrieval import RetrievedChunk

POINT_SCALE = 100


def _norm(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


normalize_text = _norm  # the matching used by the golden-case checks too


def _as_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []


def _points(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return abs(int(value))
    except (TypeError, ValueError):
        return 0


def rule_is_enabled(rule: Any) -> bool:
    return isinstance(rule, dict) and rule.get("enabled") is not False


def rule_applies(question: str, question_type: str, rule: Any) -> bool:
    if not rule_is_enabled(rule) or rule.get("applies_to_question_type") != question_type:
        return False
    triggers = _as_list(rule.get("when_question_contains_any"))
    if not triggers:
        return True
    text = _norm(question)
    return any(_norm(term) in text for term in triggers)


def category_terms(profile: Dict[str, Any], categories: Sequence[str]) -> List[str]:
    wanted = set(categories)
    terms: List[str] = []
    for entry in profile.get("domain_terms") or []:
        if rule_is_enabled(entry) and entry.get("category") in wanted:
            terms.extend([str(entry.get("term") or ""), *_as_list(entry.get("aliases"))])
    return terms


def rule_terms(profile: Dict[str, Any], rule: Dict[str, Any], side: str) -> List[str]:
    """The normalized, de-duplicated terms of one side ("positive" or "negative") of a rule."""
    raw = _as_list(rule.get(f"{side}_terms")) + category_terms(profile, _as_list(rule.get(f"{side}_categories")))
    seen: Dict[str, None] = {}
    for term in raw:
        normalized = _norm(term)
        if normalized:
            seen.setdefault(normalized, None)
    return list(seen)


def apply_profile_boost(
    chunks: Sequence[RetrievedChunk],
    question: str,
    question_type: str,
    profile: Dict[str, Any],
    *,
    max_delta: float,
) -> Tuple[List[RetrievedChunk], Dict[str, Any]]:
    """Re-rank chunks with the applying boost rules. Returns (chunks, trace); the input chunks are not changed."""
    items = list(chunks)
    rules = [
        rule
        for rule in (profile.get("retrieval_boost_rules") or [])
        if rule_applies(question, question_type, rule)
    ]
    limit = max(0.0, float(max_delta))
    trace: Dict[str, Any] = {
        "question_type": question_type,
        "rules": [str(rule.get("rule_id") or f"rule_{n}") for n, rule in enumerate(rules, start=1)],
        "boosted_chunks": 0,
        "max_delta": limit,
    }
    if not rules or not items or limit <= 0:
        return items, trace

    prepared = [
        (
            str(rule.get("rule_id") or f"rule_{n}"),
            rule_terms(profile, rule, "positive"),
            _points(rule.get("positive_weight")),
            rule_terms(profile, rule, "negative"),
            _points(rule.get("negative_weight")),
        )
        for n, rule in enumerate(rules, start=1)
    ]
    ranked: List[Tuple[float, int, RetrievedChunk]] = []
    for idx, chunk in enumerate(items):
        text = _norm(chunk.text)
        points = 0
        matched: List[str] = []
        for rule_id, positive, positive_points, negative, negative_points in prepared:
            if positive and positive_points and any(term in text for term in positive):
                points += positive_points
                matched.append(rule_id)
            if negative and negative_points and any(term in text for term in negative):
                points -= negative_points
                matched.append(rule_id)
        if not points:
            ranked.append((chunk.score, idx, chunk))
            continue
        delta = limit * max(-1.0, min(1.0, points / POINT_SCALE))
        meta = dict(chunk.metadata or {})
        details = dict(meta.get("score_details") or {})
        details["profile_boost"] = {"rules": matched, "points": points, "delta": round(delta, 6)}
        meta["score_details"] = details
        boosted = RetrievedChunk(text=chunk.text, metadata=meta, score=chunk.score - delta)
        ranked.append((boosted.score, idx, boosted))
        trace["boosted_chunks"] += 1
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ranked], trace
