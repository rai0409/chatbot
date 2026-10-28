"""Load exported RAG project profiles for optional runtime use."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import config
from rag_core import profile_store


PROFILE_ROOT = Path(config.BASE_DIR) / "config" / "rag_profiles"

# Profile ids are directory names under PROFILE_ROOT. Anything else (path
# separators, "..", absolute paths, empty) is rejected before touching disk.
_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

JSON_FILES = {
    "profile": ("profile.json", None),
    "question_type_rules": ("question_type_rules.json", "rules"),
    "domain_terms": ("domain_terms.json", "terms"),
    "synonyms": ("synonyms.json", "synonyms"),
    "retrieval_boost_rules": ("retrieval_boost_rules.json", "rules"),
    "validation_rules": ("validation_rules.json", "rules"),
    "answer_templates": ("answer_templates.json", "templates"),
}


def is_valid_rag_profile_id(project_id: Any) -> bool:
    return isinstance(project_id, str) and bool(_PROFILE_ID_RE.fullmatch(project_id))


def _label(path: Path) -> str:
    # Warnings may reach API responses and traces: never expose server paths.
    try:
        return path.resolve().relative_to(PROFILE_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def _profile_dir(project_id: str) -> Path:
    if not is_valid_rag_profile_id(project_id):
        raise ValueError("invalid rag profile id")
    root = PROFILE_ROOT.resolve()
    candidate = (PROFILE_ROOT / project_id / "exported").resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("rag profile directory is outside the profile root")
    return candidate


def _resolve(project_id: str) -> tuple[Path, str]:
    """(directory, pinned version) the profile is read from; the version is "" when it comes from config/."""
    base = _profile_dir(project_id)
    pinned = profile_store.pinned_dir(project_id)
    if pinned is None:
        return base, ""
    return pinned, pinned.name


def active_profile_dir(project_id: str) -> Path:
    """The directory the profile is read from: the pinned version if one is active, else config/rag_profiles/<id>/exported."""
    try:
        return _resolve(project_id)[0]
    except profile_store.ProfileStoreError:
        return _profile_dir(project_id)


def pin_state(project_id: str) -> str:
    """The pinned version id, "" when the profile is read from config/, "!unusable" when the store cannot be read."""
    try:
        return _resolve(project_id)[1]
    except profile_store.ProfileStoreError:
        return "!unusable"


def _empty_profile(project_id: str, profile_dir: Path, warnings: list[str]) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "profile_dir": _label(profile_dir),
        "profile": {},
        "question_type_rules": [],
        "domain_terms": [],
        "synonyms": [],
        "retrieval_boost_rules": [],
        "validation_rules": [],
        "answer_templates": [],
        "golden_qa": [],
        "profile_version": "",
        "profile_pinned": False,
        "warnings": warnings,
    }


def _read_json(path: Path, warnings: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        warnings.append(f"{_label(path)}: file not found")
    except json.JSONDecodeError as exc:
        warnings.append(f"{_label(path)}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
    except OSError as exc:
        warnings.append(f"{_label(path)}: read failed: {type(exc).__name__}")
    return None


def _read_jsonl(path: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        warnings.append(f"{_label(path)}: file not found")
        return rows
    except OSError as exc:
        warnings.append(f"{_label(path)}: read failed: {type(exc).__name__}")
        return rows

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(f"{_label(path)}: line {line_number}: invalid JSON: {exc.msg}")
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            warnings.append(f"{_label(path)}: line {line_number}: expected JSON object")
    return rows


def _load_from_dir(project_id: str, profile_dir: Path) -> dict[str, Any]:
    warnings: list[str] = []
    loaded = _empty_profile(project_id, profile_dir, warnings)

    if not profile_dir.exists():
        warnings.append(f"{_label(profile_dir)}: profile directory not found")
        return loaded

    for output_key, (filename, root_key) in JSON_FILES.items():
        data = _read_json(profile_dir / filename, warnings)
        if data is None:
            continue
        if root_key is None:
            if isinstance(data, dict):
                loaded[output_key] = data
            else:
                warnings.append(f"{_label(profile_dir / filename)}: expected JSON object")
            continue
        if not isinstance(data, dict):
            warnings.append(f"{_label(profile_dir / filename)}: expected JSON object")
            continue
        value = data.get(root_key)
        if isinstance(value, list):
            loaded[output_key] = value
        else:
            warnings.append(f"{_label(profile_dir / filename)}: expected list at root key '{root_key}'")

    loaded["golden_qa"] = _read_jsonl(profile_dir / "golden_qa.jsonl", warnings)
    loaded["project_id"] = str(loaded["profile"].get("project_id") or project_id)
    return loaded


def _digest(directory: Path) -> str:
    try:
        return profile_store.content_digest(directory)[:16]
    except profile_store.ProfileStoreError:
        return ""  # a file that cannot be read as JSON already shows up in the warnings


def _load_dir_versioned(project_id: str, directory: Path, pinned: str) -> dict[str, Any]:
    profile = _load_from_dir(project_id, directory)
    profile["profile_version"] = _digest(directory)
    profile["profile_pinned"] = bool(pinned)
    return profile


# The cache is keyed by what is read (directory and pinned version), so activating, rolling back or clearing a
# version takes effect without a cache reload, in every worker process.
@lru_cache(maxsize=16)
def _load_cached(
    project_id: str, directory: str, pinned: str, default_directory: str, default_pinned: str
) -> dict[str, Any]:
    requested_dir = Path(directory)
    if requested_dir.exists():
        return _load_dir_versioned(project_id, requested_dir, pinned)

    default_dir = Path(default_directory)
    if project_id != "default" and default_dir.exists():
        profile = _load_dir_versioned("default", default_dir, default_pinned)
        profile["warnings"].append(
            f"{_label(requested_dir)}: profile directory not found; loaded default profile instead"
        )
        return profile

    warnings = [f"{_label(requested_dir)}: profile directory not found"]
    if project_id != "default":
        warnings.append(f"{_label(default_dir)}: default profile directory not found")
    return _empty_profile(project_id, requested_dir, warnings)


def load_rag_profile(project_id: str = "default") -> dict[str, Any]:
    """Load an exported RAG profile without raising on missing or malformed files.

    The pinned version (see rag_core.profile_store) is read when there is one, otherwise
    config/rag_profiles/<id>/exported. The result carries "profile_version" (digest of the files read) and
    "profile_pinned". An unusable version store gives an empty profile with a warning, so validation is skipped.

    Raises ValueError for an invalid profile id (path traversal, separators,
    absolute paths); callers validate request input before getting here.
    """
    requested_project_id = project_id or "default"
    base_dir = _profile_dir(requested_project_id)
    try:
        directory, pinned = _resolve(requested_project_id)
        default_directory, default_pinned = _resolve("default")
    except profile_store.ProfileStoreError as exc:
        return _empty_profile(requested_project_id, base_dir, [f"profile version store: {exc}"])
    return _load_cached(requested_project_id, str(directory), pinned, str(default_directory), default_pinned)


load_rag_profile.cache_clear = _load_cached.cache_clear  # type: ignore[attr-defined]
load_rag_profile.cache_info = _load_cached.cache_info  # type: ignore[attr-defined]


def reload_rag_profile_cache() -> None:
    """Clear cached profile reads."""
    _load_cached.cache_clear()
