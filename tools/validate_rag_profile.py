#!/usr/bin/env python3
"""Validate exported RAG profile JSON/JSONL files before runtime wiring."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_PROFILE_DIR = Path("config/rag_profiles/volcano_demo/exported")
EXPECTED_PROJECT_ID = "volcano_demo"

REQUIRED_FILES = (
    "profile.json",
    "question_type_rules.json",
    "domain_terms.json",
    "synonyms.json",
    "retrieval_boost_rules.json",
    "validation_rules.json",
    "answer_templates.json",
    "golden_qa.jsonl",
)

FORBIDDEN_TEXT_VALUES = ("NaN", "None", "undefined")


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def label(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if is_non_empty_string(value):
            return f"{key}={value}"
    return "unknown item"


def require_mapping(value: Any, filename: str, result: ValidationResult) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    result.error(f"{filename}: root must be object")
    return {}


def require_list_root(data: dict[str, Any], filename: str, root_key: str, result: ValidationResult) -> list[Any]:
    if root_key not in data:
        result.error(f"{filename}: missing root key '{root_key}'")
        return []
    value = data[root_key]
    if not isinstance(value, list):
        result.error(f"{filename}: root key '{root_key}' must be list")
        return []
    return value


def require_fields(
    item: dict[str, Any],
    filename: str,
    item_label: str,
    required_fields: tuple[str, ...],
    result: ValidationResult,
) -> None:
    for field_name in required_fields:
        if field_name not in item:
            result.error(f"{filename}: {item_label}: missing required field '{field_name}'")


def require_non_empty(
    item: dict[str, Any],
    filename: str,
    item_label: str,
    field_name: str,
    result: ValidationResult,
) -> None:
    if not is_non_empty_string(item.get(field_name)):
        result.error(f"{filename}: {item_label}: '{field_name}' must be non-empty string")


def require_type(
    item: dict[str, Any],
    filename: str,
    item_label: str,
    field_name: str,
    expected_type: type,
    result: ValidationResult,
) -> None:
    value = item.get(field_name)
    if expected_type is int:
        valid = isinstance(value, int) and not isinstance(value, bool)
    else:
        valid = isinstance(value, expected_type)
    if not valid:
        result.error(
            f"{filename}: {item_label}: '{field_name}' must be {expected_type.__name__}, "
            f"got {type(value).__name__}"
        )


def require_list_fields(
    item: dict[str, Any],
    filename: str,
    item_label: str,
    fields: tuple[str, ...],
    result: ValidationResult,
) -> None:
    for field_name in fields:
        require_type(item, filename, item_label, field_name, list, result)


def collect_unique_ids(
    items: list[Any],
    filename: str,
    id_field: str,
    result: ValidationResult,
) -> set[str]:
    ids: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            result.error(f"{filename}: item #{index}: must be object")
            continue
        value = item.get(id_field)
        if not is_non_empty_string(value):
            continue
        if value in ids:
            result.error(f"{filename}: duplicate {id_field} '{value}'")
        ids.add(value)
    return ids


def require_expected_ids(
    ids: set[str],
    filename: str,
    id_field: str,
    expected_ids: set[str],
    result: ValidationResult,
) -> None:
    missing = sorted(expected_ids - ids)
    if missing:
        result.error(f"{filename}: missing required {id_field}: {', '.join(missing)}")


def check_references(
    items: list[Any],
    filename: str,
    source_field: str,
    target_ids: set[str],
    result: ValidationResult,
    id_fields: tuple[str, ...],
) -> None:
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        item_label = label(item, *id_fields) if id_fields else f"item #{index}"
        value = item.get(source_field)
        if is_non_empty_string(value) and value not in target_ids:
            result.error(
                f"{filename}: {item_label}: '{source_field}' references unknown question type '{value}'"
            )


def check_category_references(
    items: list[Any],
    filename: str,
    field_name: str,
    categories: set[str],
    result: ValidationResult,
) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        item_label = label(item, "rule_id")
        value = item.get(field_name)
        if not isinstance(value, list):
            continue
        for category in value:
            if category not in categories:
                result.error(
                    f"{filename}: {item_label}: '{field_name}' references unknown category '{category}'"
                )


def has_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        return any(has_null(child) for child in value.values())
    if isinstance(value, list):
        return any(has_null(child) for child in value)
    return False


def check_forbidden_text(path: Path, result: ValidationResult) -> None:
    text = path.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_TEXT_VALUES:
        if forbidden in text:
            result.error(f"{path.name}: contains forbidden text value '{forbidden}'")


def load_json_file(profile_dir: Path, filename: str, result: ValidationResult) -> Any:
    path = profile_dir / filename
    check_forbidden_text(path, result)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result.error(f"{filename}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
        return {}
    if has_null(data):
        result.error(f"{filename}: contains JSON null")
    return data


def load_jsonl_file(profile_dir: Path, filename: str, result: ValidationResult) -> list[Any]:
    path = profile_dir / filename
    check_forbidden_text(path, result)
    rows: list[Any] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            result.error(f"{filename}: line {line_number}: invalid JSON: {exc.msg}")
            continue
        if has_null(row):
            result.error(f"{filename}: line {line_number}: contains JSON null")
        rows.append(row)
    return rows


def validate_required_files(profile_dir: Path, result: ValidationResult) -> bool:
    ok = True
    for filename in REQUIRED_FILES:
        path = profile_dir / filename
        if not path.exists():
            result.error(f"{filename}: required file is missing from {profile_dir}")
            ok = False
        elif not path.is_file():
            result.error(f"{filename}: path exists but is not a file")
            ok = False
    return ok


def validate_profile(profile: dict[str, Any], result: ValidationResult) -> None:
    filename = "profile.json"
    for key in ("project_id", "language", "fallback_policy"):
        if key not in profile:
            result.error(f"{filename}: missing required key '{key}'")

    if not is_non_empty_string(profile.get("project_id")):
        result.error(f"{filename}: project_id must be non-empty")
    elif profile["project_id"] != EXPECTED_PROJECT_ID:
        result.error(
            f"{filename}: project_id must match directory name '{EXPECTED_PROJECT_ID}', "
            f"got '{profile['project_id']}'"
        )

    if profile.get("language") != "ja":
        result.error(f"{filename}: language must be 'ja', got {profile.get('language')!r}")

    fallback_policy = profile.get("fallback_policy")
    if fallback_policy not in {"extractive", "none", "retry"}:
        result.error(
            f"{filename}: fallback_policy must be extractive / none / retry, got {fallback_policy!r}"
        )

    if "max_answer_bullets" in profile:
        value = profile["max_answer_bullets"]
        if isinstance(value, bool):
            result.error(f"{filename}: max_answer_bullets must be int-compatible, got bool")
        else:
            try:
                int(value)
            except (TypeError, ValueError):
                result.error(f"{filename}: max_answer_bullets must be int-compatible, got {value!r}")

    for key in ("enable_generic_validation", "enable_project_validation", "enable_profile_boost"):
        if key in profile and not isinstance(profile[key], bool):
            result.error(f"{filename}: {key} must be bool")


def validate_question_types(data: dict[str, Any], result: ValidationResult) -> tuple[list[Any], set[str]]:
    filename = "question_type_rules.json"
    rules = require_list_root(data, filename, "rules", result)
    type_ids = collect_unique_ids(rules, filename, "type_id", result)
    require_expected_ids(
        type_ids,
        filename,
        "type_id",
        {"count_fact", "definition", "list_items", "measure", "summary", "other"},
        result,
    )

    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            continue
        item_label = label(rule, "type_id") if is_non_empty_string(rule.get("type_id")) else f"rule #{index}"
        require_fields(
            rule,
            filename,
            item_label,
            ("type_id", "description", "question_contains_any", "question_contains_all", "priority"),
            result,
        )
        require_non_empty(rule, filename, item_label, "type_id", result)
        require_type(rule, filename, item_label, "priority", int, result)
        require_list_fields(rule, filename, item_label, ("question_contains_any", "question_contains_all"), result)
    return rules, type_ids


def validate_domain_terms(data: dict[str, Any], result: ValidationResult) -> tuple[list[Any], set[str]]:
    filename = "domain_terms.json"
    terms = require_list_root(data, filename, "terms", result)
    term_ids = collect_unique_ids(terms, filename, "term_id", result)
    require_expected_ids(term_ids, filename, "term_id", {"carry_helmet", "active_count"}, result)
    categories: set[str] = set()

    for index, term in enumerate(terms, start=1):
        if not isinstance(term, dict):
            continue
        item_label = label(term, "term_id") if is_non_empty_string(term.get("term_id")) else f"term #{index}"
        require_fields(
            term,
            filename,
            item_label,
            ("term_id", "category", "term", "aliases", "weight", "negative_weight", "description"),
            result,
        )
        require_non_empty(term, filename, item_label, "term_id", result)
        require_non_empty(term, filename, item_label, "category", result)
        require_non_empty(term, filename, item_label, "term", result)
        require_type(term, filename, item_label, "aliases", list, result)
        require_type(term, filename, item_label, "weight", int, result)
        require_type(term, filename, item_label, "negative_weight", int, result)
        if is_non_empty_string(term.get("category")):
            categories.add(term["category"])
    return terms, categories


def validate_synonyms(data: dict[str, Any], result: ValidationResult) -> list[Any]:
    filename = "synonyms.json"
    synonyms = require_list_root(data, filename, "synonyms", result)
    for index, synonym in enumerate(synonyms, start=1):
        if not isinstance(synonym, dict):
            result.error(f"{filename}: synonym #{index}: must be object")
            continue
        item_label = label(synonym, "canonical") if is_non_empty_string(synonym.get("canonical")) else f"synonym #{index}"
        require_fields(synonym, filename, item_label, ("canonical", "synonyms", "description"), result)
        require_non_empty(synonym, filename, item_label, "canonical", result)
        require_type(synonym, filename, item_label, "synonyms", list, result)
    return synonyms


def validate_retrieval_rules(
    data: dict[str, Any],
    question_type_ids: set[str],
    categories: set[str],
    result: ValidationResult,
) -> list[Any]:
    filename = "retrieval_boost_rules.json"
    rules = require_list_root(data, filename, "rules", result)
    collect_unique_ids(rules, filename, "rule_id", result)
    required_fields = (
        "rule_id",
        "applies_to_question_type",
        "when_question_contains_any",
        "positive_categories",
        "positive_terms",
        "positive_weight",
        "negative_categories",
        "negative_terms",
        "negative_weight",
    )
    list_fields = (
        "when_question_contains_any",
        "positive_categories",
        "positive_terms",
        "negative_categories",
        "negative_terms",
    )

    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            result.error(f"{filename}: rule #{index}: must be object")
            continue
        item_label = label(rule, "rule_id") if is_non_empty_string(rule.get("rule_id")) else f"rule #{index}"
        require_fields(rule, filename, item_label, required_fields, result)
        require_non_empty(rule, filename, item_label, "rule_id", result)
        require_list_fields(rule, filename, item_label, list_fields, result)
        require_type(rule, filename, item_label, "positive_weight", int, result)
        require_type(rule, filename, item_label, "negative_weight", int, result)
    check_references(rules, filename, "applies_to_question_type", question_type_ids, result, ("rule_id",))
    check_category_references(rules, filename, "positive_categories", categories, result)
    check_category_references(rules, filename, "negative_categories", categories, result)
    return rules


def validate_validation_rules(
    data: dict[str, Any],
    question_type_ids: set[str],
    result: ValidationResult,
) -> list[Any]:
    filename = "validation_rules.json"
    rules = require_list_root(data, filename, "rules", result)
    rule_ids = collect_unique_ids(rules, filename, "rule_id", result)
    require_expected_ids(rule_ids, filename, "rule_id", {"carry_items_validation"}, result)
    required_fields = (
        "rule_id",
        "applies_to_question_type",
        "when_question_contains_any",
        "answer_must_contain_any",
        "answer_must_contain_all",
        "answer_should_not_contain_any",
        "fallback_if_failed",
    )
    list_fields = (
        "when_question_contains_any",
        "answer_must_contain_any",
        "answer_must_contain_all",
        "answer_should_not_contain_any",
    )

    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            result.error(f"{filename}: rule #{index}: must be object")
            continue
        item_label = label(rule, "rule_id") if is_non_empty_string(rule.get("rule_id")) else f"rule #{index}"
        require_fields(rule, filename, item_label, required_fields, result)
        require_non_empty(rule, filename, item_label, "rule_id", result)
        require_list_fields(rule, filename, item_label, list_fields, result)
        require_type(rule, filename, item_label, "fallback_if_failed", bool, result)
    check_references(rules, filename, "applies_to_question_type", question_type_ids, result, ("rule_id",))
    return rules


def validate_answer_templates(
    data: dict[str, Any],
    question_type_ids: set[str],
    result: ValidationResult,
) -> list[Any]:
    filename = "answer_templates.json"
    templates = require_list_root(data, filename, "templates", result)
    collect_unique_ids(templates, filename, "template_id", result)
    required_fields = ("template_id", "applies_to_question_type", "format", "instruction", "max_bullets")

    for index, template in enumerate(templates, start=1):
        if not isinstance(template, dict):
            result.error(f"{filename}: template #{index}: must be object")
            continue
        item_label = (
            label(template, "template_id")
            if is_non_empty_string(template.get("template_id"))
            else f"template #{index}"
        )
        require_fields(template, filename, item_label, required_fields, result)
        require_non_empty(template, filename, item_label, "template_id", result)
        require_non_empty(template, filename, item_label, "instruction", result)
        require_type(template, filename, item_label, "max_bullets", int, result)
    check_references(templates, filename, "applies_to_question_type", question_type_ids, result, ("template_id",))
    return templates


def validate_golden_qa(
    rows: list[Any],
    question_type_ids: set[str],
    result: ValidationResult,
) -> list[Any]:
    filename = "golden_qa.jsonl"
    case_ids = collect_unique_ids(rows, filename, "case_id", result)
    require_expected_ids(case_ids, filename, "case_id", {"q006_items_to_carry"}, result)
    required_fields = (
        "case_id",
        "question",
        "expected_all",
        "expected_any",
        "forbidden_any",
        "expected_citation",
        "expected_question_type",
        "priority",
    )

    for index, case in enumerate(rows, start=1):
        if not isinstance(case, dict):
            result.error(f"{filename}: case #{index}: must be object")
            continue
        item_label = label(case, "case_id") if is_non_empty_string(case.get("case_id")) else f"case #{index}"
        require_fields(case, filename, item_label, required_fields, result)
        require_non_empty(case, filename, item_label, "case_id", result)
        require_non_empty(case, filename, item_label, "question", result)
        require_list_fields(case, filename, item_label, ("expected_all", "expected_any", "forbidden_any"), result)
        require_type(case, filename, item_label, "expected_citation", bool, result)
        require_type(case, filename, item_label, "priority", int, result)
    check_references(rows, filename, "expected_question_type", question_type_ids, result, ("case_id",))
    return rows


def validate_profile_dir(profile_dir: Path) -> tuple[ValidationResult, dict[str, int]]:
    result = ValidationResult()
    counts = {
        "question types count": 0,
        "domain terms count": 0,
        "synonyms count": 0,
        "retrieval boost rules count": 0,
        "validation rules count": 0,
        "answer templates count": 0,
        "golden qa cases count": 0,
    }

    if not validate_required_files(profile_dir, result):
        return result, counts

    profile = require_mapping(load_json_file(profile_dir, "profile.json", result), "profile.json", result)
    question_type_data = require_mapping(
        load_json_file(profile_dir, "question_type_rules.json", result),
        "question_type_rules.json",
        result,
    )
    domain_terms_data = require_mapping(
        load_json_file(profile_dir, "domain_terms.json", result),
        "domain_terms.json",
        result,
    )
    synonyms_data = require_mapping(load_json_file(profile_dir, "synonyms.json", result), "synonyms.json", result)
    retrieval_data = require_mapping(
        load_json_file(profile_dir, "retrieval_boost_rules.json", result),
        "retrieval_boost_rules.json",
        result,
    )
    validation_data = require_mapping(
        load_json_file(profile_dir, "validation_rules.json", result),
        "validation_rules.json",
        result,
    )
    templates_data = require_mapping(
        load_json_file(profile_dir, "answer_templates.json", result),
        "answer_templates.json",
        result,
    )
    golden_qa_rows = load_jsonl_file(profile_dir, "golden_qa.jsonl", result)

    validate_profile(profile, result)
    question_types, question_type_ids = validate_question_types(question_type_data, result)
    domain_terms, categories = validate_domain_terms(domain_terms_data, result)
    synonyms = validate_synonyms(synonyms_data, result)
    retrieval_rules = validate_retrieval_rules(retrieval_data, question_type_ids, categories, result)
    validation_rules = validate_validation_rules(validation_data, question_type_ids, result)
    templates = validate_answer_templates(templates_data, question_type_ids, result)
    golden_qa = validate_golden_qa(golden_qa_rows, question_type_ids, result)

    counts.update(
        {
            "question types count": len(question_types),
            "domain terms count": len(domain_terms),
            "synonyms count": len(synonyms),
            "retrieval boost rules count": len(retrieval_rules),
            "validation rules count": len(validation_rules),
            "answer templates count": len(templates),
            "golden qa cases count": len(golden_qa),
        }
    )
    return result, counts


def print_counts(counts: dict[str, int]) -> None:
    print("Count summary:")
    for name, count in counts.items():
        print(f"- {name}: {count}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate exported RAG profile JSON/JSONL files.")
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
        help=f"Directory containing exported profile files. Default: {DEFAULT_PROFILE_DIR}",
    )
    parser.add_argument("--verbose", action="store_true", help="Print additional validation details.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result, counts = validate_profile_dir(args.profile_dir)

    if result.errors:
        print("profile validation failed")
        print("Errors:")
        for error in result.errors:
            print(f"- {error}")
        if result.warnings:
            print("Warnings:")
            for warning in result.warnings:
                print(f"- {warning}")
        return 1

    print("profile validation passed")
    print_counts(counts)
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    elif args.verbose:
        print("Warnings: none")
    if args.verbose:
        print(f"Profile directory: {args.profile_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
