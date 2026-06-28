"""Validate generated answers with exported RAG profile validation rules."""

from __future__ import annotations

from typing import Any


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []


def _as_bool(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else False


def _contains_any(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term and term in text]


def _base_result(question_type: str) -> dict[str, Any]:
    return {
        "passed": True,
        "matched_rule_id": "",
        "question_type": question_type or "other",
        "missing_any": [],
        "missing_all": [],
        "forbidden_hits": [],
        "fallback_if_failed": False,
        "reason": "",
    }


def _rule_applies(question: str, question_type: str, rule: dict[str, Any]) -> bool:
    if rule.get("applies_to_question_type") != question_type:
        return False
    question_terms = _as_list(rule.get("when_question_contains_any"))
    if not question_terms:
        return True
    return bool(_contains_any(question, question_terms))


def validate_answer_with_profile(
    question: str,
    answer_text: str,
    question_type: str,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate an answer against the first matching exported profile rule."""
    safe_question = question or ""
    safe_answer = answer_text or ""
    safe_question_type = question_type or "other"
    result = _base_result(safe_question_type)

    rules = profile.get("validation_rules") if isinstance(profile, dict) else []
    if not isinstance(rules, list):
        return result

    matched_rule = None
    for rule in rules:
        if isinstance(rule, dict) and _rule_applies(safe_question, safe_question_type, rule):
            matched_rule = rule
            break

    if matched_rule is None:
        return result

    result["matched_rule_id"] = str(matched_rule.get("rule_id") or "")
    result["fallback_if_failed"] = _as_bool(matched_rule.get("fallback_if_failed"))

    must_any = _as_list(matched_rule.get("answer_must_contain_any"))
    must_all = _as_list(matched_rule.get("answer_must_contain_all"))
    should_not_any = _as_list(matched_rule.get("answer_should_not_contain_any"))

    if must_any and not _contains_any(safe_answer, must_any):
        result["missing_any"] = must_any

    missing_all = [term for term in must_all if term not in safe_answer]
    if missing_all:
        result["missing_all"] = missing_all

    forbidden_hits = _contains_any(safe_answer, should_not_any)
    if forbidden_hits:
        result["forbidden_hits"] = forbidden_hits

    if result["missing_any"] or result["missing_all"] or result["forbidden_hits"]:
        result["passed"] = False
        result["reason"] = "answer failed profile validation"

    return result
