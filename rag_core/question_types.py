"""Question type detection using exported RAG profile rules."""

from __future__ import annotations

from typing import Any


FALLBACK_RULES = (
    {
        "type_id": "count_fact",
        "question_contains_any": ["いくつ", "何個", "何件", "何名", "何年", "何円", "数"],
        "priority": 100,
    },
    {
        "type_id": "definition",
        "question_contains_any": ["とは", "定義", "意味", "指す"],
        "priority": 90,
    },
    {
        "type_id": "list_items",
        "question_contains_any": ["必要なもの", "持っていく", "持参", "携行品", "装備", "一覧"],
        "priority": 90,
    },
    {
        "type_id": "procedure",
        "question_contains_any": ["方法", "手順", "どうする", "流れ", "進め方"],
        "priority": 80,
    },
    {
        "type_id": "summary",
        "question_contains_any": ["概要", "要約", "まとめ", "全体像", "何について"],
        "priority": 70,
    },
    {
        "type_id": "measure",
        "question_contains_any": ["対策", "整備", "取り組み", "施策", "実施", "支援"],
        "priority": 80,
    },
    {
        "type_id": "lesson",
        "question_contains_any": ["教訓", "学び", "示唆", "反省", "重要"],
        "priority": 80,
    },
)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _matched_terms(question: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term and term in question]


def _rule_matches(question: str, rule: dict[str, Any]) -> tuple[bool, list[str]]:
    contains_all = _as_list(rule.get("question_contains_all"))
    contains_any = _as_list(rule.get("question_contains_any"))

    all_matches = _matched_terms(question, contains_all)
    if len(all_matches) != len(contains_all):
        return False, all_matches

    any_matches = _matched_terms(question, contains_any)
    if contains_any and not any_matches:
        return False, all_matches

    matched = []
    for term in all_matches + any_matches:
        if term not in matched:
            matched.append(term)
    return True, matched


def _profile_rules(profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not profile:
        return []
    rules = profile.get("question_type_rules")
    if not isinstance(rules, list):
        return []
    typed_rules = [rule for rule in rules if isinstance(rule, dict) and rule.get("type_id")]
    return sorted(typed_rules, key=lambda rule: _as_int(rule.get("priority")), reverse=True)


def _fallback_explanation(question: str) -> dict[str, Any]:
    for rule in sorted(FALLBACK_RULES, key=lambda item: item["priority"], reverse=True):
        matched = _matched_terms(question, _as_list(rule.get("question_contains_any")))
        if matched:
            return {
                "question_type": rule["type_id"],
                "matched_rule_id": rule["type_id"],
                "source": "fallback",
                "matched_terms": matched,
                "priority": rule["priority"],
            }
    return {
        "question_type": "other",
        "matched_rule_id": "other",
        "source": "fallback",
        "matched_terms": [],
        "priority": 0,
    }


def explain_question_type(question: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return question type classification and the rule evidence."""
    safe_question = question or ""
    for rule in _profile_rules(profile):
        matched, matched_terms = _rule_matches(safe_question, rule)
        if matched:
            type_id = str(rule.get("type_id"))
            return {
                "question_type": type_id,
                "matched_rule_id": type_id,
                "source": "profile",
                "matched_terms": matched_terms,
                "priority": _as_int(rule.get("priority")),
            }
    return _fallback_explanation(safe_question)


def detect_question_type(question: str, profile: dict[str, Any] | None = None) -> str:
    """Return the detected question type id."""
    return str(explain_question_type(question, profile).get("question_type") or "other")
