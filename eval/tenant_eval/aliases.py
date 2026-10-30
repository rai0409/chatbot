"""Alias candidates for approved QA (step 3-5): which paraphrases are not covered, and candidates to add."""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence, Set

from eval.tenant_eval import perturb
from rag_core.approved_qa import (
    MAX_APPROVED_ALIAS_CHARS,
    MAX_APPROVED_ALIASES,
    ApprovedAnswer,
    ApprovedQAIndex,
    lookup_approved_answer,
)
from rag_core.question_normalization import exact_match_key

# Kinds of perturbation that people plausibly type again and again (typos and dropped characters do not repeat).
DEFAULT_ALIAS_KINDS = ("casual_ending", "keywords_only", "katakana_hiragana", "spacing")


def _answers(index: ApprovedQAIndex, tenant_id: str) -> List[ApprovedAnswer]:
    return [answer for answer in index.records if answer.tenant_id == tenant_id]


def coverage(
    index: ApprovedQAIndex, *, tenant_id: str = "default", kinds: Sequence[str] = perturb.KINDS, seed: str = ""
) -> Dict[str, Any]:
    """Perturb each approved question and look the variant up: does it reach the same approved answer?"""
    by_kind: Dict[str, Dict[str, int]] = {}
    misses: List[Dict[str, str]] = []
    answers = _answers(index, tenant_id)
    for answer in answers:
        for variant in perturb.perturb_question(answer.question, kinds=kinds, seed=seed):
            stats = by_kind.setdefault(variant["kind"], {"variants": 0, "covered": 0})
            stats["variants"] += 1
            hit = lookup_approved_answer(index, variant["text"], tenant_id=tenant_id)
            if hit is not None and hit.qa_id == answer.qa_id:
                stats["covered"] += 1
            else:
                misses.append({"qa_id": answer.qa_id, "question": answer.question, "kind": variant["kind"],
                               "variant": variant["text"]})
    total = sum(s["variants"] for s in by_kind.values())
    covered = sum(s["covered"] for s in by_kind.values())
    return {"approved": len(answers), "variants": total, "covered": covered,
            "covered_rate": covered / total if total else None, "by_kind": dict(sorted(by_kind.items())),
            "misses": misses}


def _known_keys(answer: ApprovedAnswer) -> Set[str]:
    return {exact_match_key(answer.question), *(exact_match_key(alias) for alias in answer.approved_aliases)}


def _valid_alias(alias: str) -> bool:
    return bool(alias.strip()) and len(alias) <= MAX_APPROVED_ALIAS_CHARS and not any(ord(c) < 32 or ord(c) == 127 for c in alias)


def _row(answer: ApprovedAnswer, alias: str, source: str, **extra: Any) -> Dict[str, Any]:
    digest = hashlib.sha256(f"{answer.qa_id}\0{exact_match_key(alias)}".encode("utf-8")).hexdigest()[:16]
    return {"candidate_id": digest, "qa_id": answer.qa_id, "question": answer.question, "alias": alias,
            "source": source, "status": "pending", **extra}


def candidates_from_coverage(
    index: ApprovedQAIndex, report: Dict[str, Any], *, tenant_id: str = "default",
    kinds: Sequence[str] = DEFAULT_ALIAS_KINDS, max_per_question: int = 3,
) -> List[Dict[str, Any]]:
    """The misses of `kinds` as alias candidates, within the room an approved QA has for aliases."""
    by_id = {answer.qa_id: answer for answer in _answers(index, tenant_id)}
    taken: Dict[str, Set[str]] = {}
    added: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []
    for miss in report["misses"]:
        answer = by_id.get(miss["qa_id"])
        if answer is None or miss["kind"] not in kinds or not _valid_alias(miss["variant"]):
            continue
        keys = taken.setdefault(answer.qa_id, _known_keys(answer))
        room = MAX_APPROVED_ALIASES - len(answer.approved_aliases) - added.get(answer.qa_id, 0)
        key = exact_match_key(miss["variant"])
        if room <= 0 or added.get(answer.qa_id, 0) >= max_per_question or key in keys:
            continue
        keys.add(key)
        added[answer.qa_id] = added.get(answer.qa_id, 0) + 1
        rows.append(_row(answer, miss["variant"], "perturbation", kind=miss["kind"]))
    return rows


def _bigrams(text: str) -> Set[str]:
    key = exact_match_key(text)
    return {key[i:i + 2] for i in range(len(key) - 1)} or ({key} if key else set())


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of the character bigrams of two texts (after the strong normalization of the lookup)."""
    left, right = _bigrams(a), _bigrams(b)
    return len(left & right) / len(left | right) if left and right else 0.0


def mine(
    index: ApprovedQAIndex, questions: Sequence[str], *, tenant_id: str = "default", threshold: float = 0.5,
    max_per_question: int = 3,
) -> List[Dict[str, Any]]:
    """For each question that reaches no approved answer, the nearest approved question (above `threshold`)."""
    answers = _answers(index, tenant_id)
    taken: Dict[str, Set[str]] = {}
    added: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []
    for text in questions:
        text = str(text or "").strip()
        if not text or lookup_approved_answer(index, text, tenant_id=tenant_id) is not None or not _valid_alias(text):
            continue
        best: Optional[tuple] = None
        for answer in answers:
            score = max(similarity(text, candidate) for candidate in (answer.question, *answer.approved_aliases))
            if best is None or score > best[0]:
                best = (score, answer)
        if best is None or best[0] < threshold:
            continue
        score, answer = best
        keys = taken.setdefault(answer.qa_id, _known_keys(answer))
        key = exact_match_key(text)
        room = MAX_APPROVED_ALIASES - len(answer.approved_aliases) - added.get(answer.qa_id, 0)
        if room <= 0 or added.get(answer.qa_id, 0) >= max_per_question or key in keys:
            continue
        keys.add(key)
        added[answer.qa_id] = added.get(answer.qa_id, 0) + 1
        rows.append(_row(answer, text, "mined", similarity=round(score, 3)))
    return rows
