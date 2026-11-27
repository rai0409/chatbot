"""Run the retrieval_boost_rules of an exported RAG profile.

A rule applies when its question type is the question's and, if it lists trigger words, the question contains one.
Its terms are positive_terms / negative_terms plus the term and aliases of every enabled domain term in its
positive_categories / negative_categories. A chunk whose text contains any positive term gets +|positive_weight|
points, any negative term -|negative_weight| (one hit per side and rule, however many terms match); the points of
all applying rules add up. Points are on a 0-100 scale and become a change of the chunk score (a distance, lower is
better): score -= max_delta * clamp(points / 100, -1, 1). Matching is on the keyword-index normalization
(ja_text.normalize_for_index: NFKC, lower case and, with NORMALIZATION_PROFILE=index, dash / long-vowel / wave
unification), so a term matches the chunk text the same way the keyword search does.

Document priority (apply_doc_priority): the doc_boost_rules of a profile and the optional doc_priority metadata of
a chunk move preferred documents (a regulation over a memo, the current edition over an old one) up a few places
in an already ranked list, e.g. after the cross-encoder.
"""
from __future__ import annotations

import fnmatch
from typing import Any, Dict, List, Sequence, Tuple

from rag_core.ja_text import normalize_for_index
from rag_core.retrieval import RetrievedChunk

POINT_SCALE = 100


def _norm(text: Any) -> str:
    return normalize_for_index(str(text or ""))


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


# --- document priority ------------------------------------------------------------------------------------------
# A profile may carry
#   "doc_boost_rules": [
#     {"rule_id": "regulations_first", "enabled": true, "points": 40,
#      "match": {"source_doc": "規程_*.pdf"}},             # glob on source_doc (or "doc_id": glob)
#     {"rule_id": "hr_authority", "points": 20, "match": {"authority": "人事部"}},   # equal after normalization
#     {"rule_id": "old_memos", "points": -30, "match": {"source_doc": "*旧*"}}
#   ]
# All given match keys must match. Points (0-100 scale, negative demotes) of every matching rule add up, plus the
# chunk's own doc_priority metadata (an int, default 0). The sum moves a chunk up (or down) by at most
# max_rank_shift places: shift = max_rank_shift * clamp(points / 100, -1, 1). A tie goes to the higher priority,
# then to the incoming order, and
# the incoming scores are reassigned in the new order (ascending), as the cross-encoder does, so a later sort by
# score keeps it.
DEFAULT_MAX_RANK_SHIFT = 2.0


def _glob(pattern: Any, value: Any) -> bool:
    text, pat = _norm(value), _norm(pattern)
    return bool(pat) and fnmatch.fnmatchcase(text, pat)


def doc_rule_matches(rule: Any, metadata: Dict[str, Any]) -> bool:
    if not rule_is_enabled(rule):
        return False
    match = rule.get("match")
    if not isinstance(match, dict) or not match:
        return False
    for key, expected in match.items():
        if key == "source_doc":
            if not _glob(expected, metadata.get("source_doc") or metadata.get("doc")):
                return False
        elif key == "doc_id":
            if not _glob(expected, metadata.get("doc_id") or metadata.get("source_doc")):
                return False
        elif key == "authority":
            if not _norm(expected) or _norm(expected) != _norm(metadata.get("authority")):
                return False
        else:
            return False  # an unknown key never matches (a typo must not boost everything)
    return True


def _signed_points(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):  # OverflowError: JSON "Infinity"
        return 0


def doc_priority_points(metadata: Dict[str, Any], rules: Sequence[Any]) -> Tuple[int, List[str]]:
    points = _signed_points(metadata.get("doc_priority"))
    matched: List[str] = []
    for n, rule in enumerate(rules, start=1):
        if doc_rule_matches(rule, metadata):
            points += _signed_points(rule.get("points"))
            matched.append(str(rule.get("rule_id") or f"doc_rule_{n}"))
    return points, matched


def apply_doc_priority(
    chunks: Sequence[RetrievedChunk],
    profile: Dict[str, Any] | None,
    *,
    max_rank_shift: float = DEFAULT_MAX_RANK_SHIFT,
) -> Tuple[List[RetrievedChunk], Dict[str, Any]]:
    """Reorder ranked chunks by document priority. Returns (chunks, trace); the input chunks are not changed.

    Without doc_boost_rules and without doc_priority metadata the list comes back unchanged (same objects)."""
    items = list(chunks)
    rules = [rule for rule in ((profile or {}).get("doc_boost_rules") or []) if rule_is_enabled(rule)]
    shift_limit = max(0.0, float(max_rank_shift))
    trace: Dict[str, Any] = {"rules": len(rules), "prioritized_chunks": 0, "max_rank_shift": shift_limit, "reordered": False}
    if not items or shift_limit <= 0:
        return items, trace
    scored: List[Tuple[float, int, RetrievedChunk, int, List[str]]] = []
    any_points = False
    for idx, chunk in enumerate(items):
        points, matched = doc_priority_points(dict(chunk.metadata or {}), rules)
        if points:
            any_points = True
        shift = shift_limit * max(-1.0, min(1.0, points / POINT_SCALE))
        scored.append((idx - shift, idx, chunk, points, matched))
    if not any_points:
        return items, trace
    # A tie on the shifted rank goes to the higher priority, then to the incoming order.
    ordered = sorted(scored, key=lambda item: (item[0], -item[3], item[1]))
    original_scores = [float(chunk.score) for chunk in items]
    out: List[RetrievedChunk] = []
    for position, (_eff, idx, chunk, points, matched) in enumerate(ordered):
        meta = dict(chunk.metadata or {})
        if points:
            details = dict(meta.get("score_details") or {})
            details["doc_priority"] = {"points": points, "rules": matched, "from_rank": idx + 1, "to_rank": position + 1}
            meta["score_details"] = details
            trace["prioritized_chunks"] += 1
        score = original_scores[position] if sorted(original_scores) == original_scores else float(chunk.score)
        out.append(RetrievedChunk(text=chunk.text, metadata=meta, score=score))
    trace["reordered"] = [item[1] for item in ordered] != list(range(len(items)))
    return out, trace
