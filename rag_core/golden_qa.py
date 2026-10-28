"""Golden question-and-answer cases of a RAG profile (golden_qa.jsonl).

A case says what a good answer to a question contains (expected_all: every term; expected_any: at least one),
must not contain (forbidden_any), whether it cites a source and which question type it has. Matching is on
NFKC-normalized, case-folded text. Cases carry an optional "group" (cases about the same source material) and an
optional "origin_change" (the change a case was written for); see assign_folds and exclude_motivating.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from rag_core.profile_engine import normalize_text

Case = Dict[str, Any]


def _strings(value: Any, field: str, errors: List[str]) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"'{field}' must be a list of strings")
        return []
    return [item for item in value if item.strip()]


def _text(value: Any, field: str, errors: List[str], *, required: bool = False) -> str:
    if value is None or value == "":
        if required:
            errors.append(f"'{field}' is required")
        return ""
    if not isinstance(value, str) or not value.strip():
        errors.append(f"'{field}' must be a non-empty string")
        return ""
    return value.strip()


def parse_case(row: Any) -> Tuple[Optional[Case], List[str]]:
    """(case, errors). A row with errors gives no case."""
    if not isinstance(row, dict):
        return None, ["case is not an object"]
    errors: List[str] = []
    case_id = _text(row.get("case_id"), "case_id", errors, required=True)
    question = _text(row.get("question"), "question", errors, required=True)
    case: Case = {
        "case_id": case_id,
        "question": question,
        "expected_all": _strings(row.get("expected_all"), "expected_all", errors),
        "expected_any": _strings(row.get("expected_any"), "expected_any", errors),
        "forbidden_any": _strings(row.get("forbidden_any"), "forbidden_any", errors),
        "expected_question_type": _text(row.get("expected_question_type"), "expected_question_type", errors),
        "group": _text(row.get("group"), "group", errors),
        "origin_change": _text(row.get("origin_change"), "origin_change", errors),
        "reference": _text(row.get("reference"), "reference", errors),
        "tags": _strings(row.get("tags"), "tags", errors),
    }
    for field, default in (("expected_citation", False), ("enabled", True)):
        value = row.get(field, default)
        if not isinstance(value, bool):
            errors.append(f"'{field}' must be true or false")
            value = default
        case[field] = value
    priority = row.get("priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int):
        errors.append("'priority' must be an integer")
        priority = 0
    case["priority"] = priority
    if not errors and not (
        case["expected_all"]
        or case["expected_any"]
        or case["forbidden_any"]
        or case["expected_citation"]
        or case["expected_question_type"]
        or case["reference"]
    ):
        errors.append("the case checks nothing (no expected terms, citation or question type)")
    label = case_id or "(no case_id)"
    if errors:
        return None, [f"{label}: {message}" for message in errors]
    return case, []


def load_cases(profile: Mapping[str, Any], *, include_disabled: bool = False) -> Tuple[List[Case], List[str]]:
    """The cases of a loaded profile, highest priority first, plus a problem for every row that was not used."""
    cases: List[Case] = []
    problems: List[str] = []
    seen: set = set()
    for number, row in enumerate(profile.get("golden_qa") or [], start=1):
        case, errors = parse_case(row)
        if case is None:
            problems.extend(f"row {number}: {message}" for message in errors)
            continue
        if case["case_id"] in seen:
            problems.append(f"row {number}: duplicate case_id '{case['case_id']}'")
            continue
        seen.add(case["case_id"])
        if case["enabled"] or include_disabled:
            cases.append(case)
    cases.sort(key=lambda item: (-item["priority"], item["case_id"]))
    return cases, problems


def evaluate_case(case: Mapping[str, Any], *, answer_text: str, has_citation: bool, question_type: str) -> Dict[str, Any]:
    """Check one answer against one case. `failures` lists why it failed (empty when it passed)."""
    text = normalize_text(answer_text)
    present = lambda term: normalize_text(term) in text  # noqa: E731
    missing_all = [term for term in case["expected_all"] if not present(term)]
    missing_any = list(case["expected_any"]) if case["expected_any"] and not any(map(present, case["expected_any"])) else []
    forbidden_hits = [term for term in case["forbidden_any"] if present(term)]
    failures: List[str] = []
    if missing_all:
        failures.append("missing_all")
    if missing_any:
        failures.append("missing_any")
    if forbidden_hits:
        failures.append("forbidden")
    if case["expected_citation"] and not has_citation:
        failures.append("no_citation")
    if case["expected_question_type"] and question_type != case["expected_question_type"]:
        failures.append("question_type")
    return {
        "case_id": case["case_id"],
        "passed": not failures,
        "failures": failures,
        "missing_all": missing_all,
        "missing_any": missing_any,
        "forbidden_hits": forbidden_hits,
    }


# --- folds ----------------------------------------------------------------------------------------


def group_of(case: Mapping[str, Any]) -> str:
    return str(case.get("group") or case["case_id"])


def assign_folds(cases: Sequence[Mapping[str, Any]], k: int, *, seed: str = "") -> Dict[str, int]:
    """case_id -> fold in [0, k). All cases of a group share a fold; a fold depends only on (seed, group)."""
    if isinstance(k, bool) or not isinstance(k, int) or k < 2:
        raise ValueError("k must be an integer of at least 2")
    folds: Dict[str, int] = {}
    for case in cases:
        digest = hashlib.sha256(f"{seed}\0{group_of(case)}".encode("utf-8")).hexdigest()
        folds[case["case_id"]] = int(digest[:12], 16) % k
    return folds


def split_by_fold(
    cases: Sequence[Case], k: int, held_out: int, *, seed: str = ""
) -> Tuple[List[Case], List[Case]]:
    """(the cases outside the fold, the cases in it); no group is in both."""
    if isinstance(held_out, bool) or not isinstance(held_out, int) or not 0 <= held_out < k:
        raise ValueError("held_out must be a fold number in [0, k)")
    folds = assign_folds(cases, k, seed=seed)
    rest = [case for case in cases if folds[case["case_id"]] != held_out]
    held = [case for case in cases if folds[case["case_id"]] == held_out]
    return rest, held


def exclude_motivating(cases: Sequence[Case], change_id: str) -> Tuple[List[Case], List[Case]]:
    """(the cases to judge a change on, the cases that were written for it and are left out)."""
    kept = [case for case in cases if case["origin_change"] != change_id]
    left_out = [case for case in cases if case["origin_change"] == change_id]
    return kept, left_out
