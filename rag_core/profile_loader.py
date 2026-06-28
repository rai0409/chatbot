"""Load exported RAG project profiles for optional runtime use."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


PROFILE_ROOT = Path("config/rag_profiles")

JSON_FILES = {
    "profile": ("profile.json", None),
    "question_type_rules": ("question_type_rules.json", "rules"),
    "domain_terms": ("domain_terms.json", "terms"),
    "synonyms": ("synonyms.json", "synonyms"),
    "retrieval_boost_rules": ("retrieval_boost_rules.json", "rules"),
    "validation_rules": ("validation_rules.json", "rules"),
    "answer_templates": ("answer_templates.json", "templates"),
}


def _empty_profile(project_id: str, profile_dir: Path, warnings: list[str]) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "profile_dir": str(profile_dir),
        "profile": {},
        "question_type_rules": [],
        "domain_terms": [],
        "synonyms": [],
        "retrieval_boost_rules": [],
        "validation_rules": [],
        "answer_templates": [],
        "golden_qa": [],
        "warnings": warnings,
    }


def _read_json(path: Path, warnings: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        warnings.append(f"{path}: file not found")
    except json.JSONDecodeError as exc:
        warnings.append(f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
    except OSError as exc:
        warnings.append(f"{path}: read failed: {exc}")
    return None


def _read_jsonl(path: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        warnings.append(f"{path}: file not found")
        return rows
    except OSError as exc:
        warnings.append(f"{path}: read failed: {exc}")
        return rows

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(f"{path}: line {line_number}: invalid JSON: {exc.msg}")
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            warnings.append(f"{path}: line {line_number}: expected JSON object")
    return rows


def _load_from_dir(project_id: str, profile_dir: Path) -> dict[str, Any]:
    warnings: list[str] = []
    loaded = _empty_profile(project_id, profile_dir, warnings)

    if not profile_dir.exists():
        warnings.append(f"{profile_dir}: profile directory not found")
        return loaded

    for output_key, (filename, root_key) in JSON_FILES.items():
        data = _read_json(profile_dir / filename, warnings)
        if data is None:
            continue
        if root_key is None:
            if isinstance(data, dict):
                loaded[output_key] = data
            else:
                warnings.append(f"{profile_dir / filename}: expected JSON object")
            continue
        if not isinstance(data, dict):
            warnings.append(f"{profile_dir / filename}: expected JSON object")
            continue
        value = data.get(root_key)
        if isinstance(value, list):
            loaded[output_key] = value
        else:
            warnings.append(f"{profile_dir / filename}: expected list at root key '{root_key}'")

    loaded["golden_qa"] = _read_jsonl(profile_dir / "golden_qa.jsonl", warnings)
    loaded["project_id"] = str(loaded["profile"].get("project_id") or project_id)
    return loaded


@lru_cache(maxsize=16)
def load_rag_profile(project_id: str = "default") -> dict[str, Any]:
    """Load an exported RAG profile without raising on missing or malformed files."""
    requested_project_id = project_id or "default"
    requested_dir = PROFILE_ROOT / requested_project_id / "exported"

    if requested_dir.exists():
        return _load_from_dir(requested_project_id, requested_dir)

    default_dir = PROFILE_ROOT / "default" / "exported"
    if requested_project_id != "default" and default_dir.exists():
        profile = _load_from_dir("default", default_dir)
        profile["warnings"].append(
            f"{requested_dir}: profile directory not found; loaded default profile instead"
        )
        return profile

    warnings = [f"{requested_dir}: profile directory not found"]
    if requested_project_id != "default":
        warnings.append(f"{default_dir}: default profile directory not found")
    return _empty_profile(requested_project_id, requested_dir, warnings)


def reload_rag_profile_cache() -> None:
    """Clear cached profile reads."""
    load_rag_profile.cache_clear()
