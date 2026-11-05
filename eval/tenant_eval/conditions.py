"""What an evaluation ran under, and the guard that a comparison differs only by the declared change."""
from __future__ import annotations

import contextlib
from typing import Any, Dict, Iterator, Mapping, Optional

import config
from rag_core import index_generation, knob_registry, profile_loader, profile_store


class ConditionsError(ValueError):
    pass


_MISSING = object()


def collect(profile_id: str, *, tenant_id: str = "default") -> Dict[str, Any]:
    """The conditions of an evaluation run now: profile version hash, index, every registered knob."""
    profile = profile_loader.load_rag_profile(profile_id)
    pointer = index_generation.active_pointer() or {}
    return {
        "profile_id": profile_id,
        "profile_version": str(profile.get("profile_version") or ""),
        "profile_pinned": bool(profile.get("profile_pinned")),
        "tenant_id": tenant_id,
        "collection": config.resolve_chroma_collection_name(),
        "generation_id": str(pointer.get("generation_id") or ""),
        "knobs": knob_registry.snapshot(),  # env knobs are read as qa.py reads them
    }


def _flatten(conditions: Mapping[str, Any]) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for key, value in conditions.items():
        if key == "knobs" and isinstance(value, Mapping):
            flat.update({f"knobs.{name}": knob_value for name, knob_value in value.items()})
        else:
            flat[key] = value
    return flat


def compare_conditions(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """{condition: {"baseline": x, "candidate": y}} for every condition that differs."""
    base, cand = _flatten(baseline), _flatten(candidate)
    return {
        key: {"baseline": base.get(key), "candidate": cand.get(key)}
        for key in sorted(set(base) | set(cand))
        if base.get(key) != cand.get(key)
    }


def assert_declared_changes(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any], declared: Mapping[str, Any]
) -> Dict[str, Dict[str, Any]]:
    """Raise unless the two conditions differ exactly where `declared` says, and the candidate has the declared values.

    declared: {"profile_version": "<version>", "knobs": {"NAME": value}}. Returns the differences."""
    differences = compare_conditions(baseline, candidate)
    wanted = _flatten(declared)
    problems = []
    undeclared = sorted(set(differences) - set(wanted))
    if undeclared:
        problems.append("differs without being declared: " + ", ".join(undeclared))
    cand = _flatten(candidate)
    not_applied = sorted(key for key, value in wanted.items() if cand.get(key) != value)
    if not_applied:
        problems.append("declared but not in effect in the candidate run: " + ", ".join(not_applied))
    same = sorted(key for key in wanted if key not in differences and key not in not_applied)
    if same:
        problems.append("declared but identical in the baseline (no change to judge): " + ", ".join(same))
    if problems:
        raise ConditionsError("; ".join(problems))
    return differences


@contextlib.contextmanager
def candidate_environment(
    profile_id: str, *, version: Optional[str] = None, knobs: Optional[Mapping[str, Any]] = None
) -> Iterator[None]:
    """Serve a stored profile version and set validated knob values in this process, then restore them."""
    changes = knob_registry.check_change(knobs) if knobs else {}
    saved = {name: getattr(config, name, _MISSING) for name in changes}
    with contextlib.ExitStack() as stack:
        if version:
            stack.enter_context(profile_store.override_version(profile_id, version))
        try:
            for name, value in changes.items():
                setattr(config, name, value)
            yield
        finally:
            for name, value in saved.items():
                if value is _MISSING:  # an env knob that had no config attribute
                    if hasattr(config, name):
                        delattr(config, name)
                else:
                    setattr(config, name, value)
