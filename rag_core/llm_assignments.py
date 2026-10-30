"""Which model tier answers whom (S6): assigned in the console, read by the model profiles.

The profiles file (rag_core/llm_profiles.py) defines tiers, for example "standard" (a small local model) and "premium" (a larger one). Here an
administrator assigns a tier to everyone, to a tenant, to a role, or to one caller, after looking at who uses how much (rag_core/llm_usage.py).
The most specific assignment wins: person, role, tenant, everyone. An assignment is a runtime setting: it needs a reason, keeps who made
it, and is written to the audit log; removing it (tier None) returns to the routes of the profiles file.

A "caller" is the fingerprint of the API key, or of the person with single sign-on. One key used by many people is one caller.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from rag_core import llm_profiles, runtime_settings

KINDS = ("global", "tenant", "role", "user")
ROLES = ("admin", "operator", "user", "viewer")
_ID_RE = re.compile(r"^[a-z0-9]{6,50}$")


def set_assignment(kind: str, ident: Optional[str], tier: Optional[str], *, by: str, reason: str) -> Dict[str, Any]:
    """Assign `tier` (None: remove the assignment) to everyone, a tenant, a role or a caller."""
    if kind not in KINDS:
        raise runtime_settings.SettingsError(f"the kind is one of: {', '.join(KINDS)}")
    if tier is not None and tier not in llm_profiles.tiers():
        raise runtime_settings.SettingsError("the tier is not defined in the model profiles file")
    if kind == "global":
        return runtime_settings.set_value(llm_profiles.TIER_KEY, tier, by=by, reason=reason)
    if not ident:
        raise runtime_settings.SettingsError("an id is required (a tenant, a role or a caller)")
    if kind == "tenant":
        return runtime_settings.set_value(llm_profiles.TIER_KEY, tier, by=by, reason=reason, tenant_id=ident)
    if kind == "role":
        if ident not in ROLES:
            raise runtime_settings.SettingsError(f"the role is one of: {', '.join(ROLES)}")
        return runtime_settings.set_value(llm_profiles.ROLE_TIER_PREFIX + ident, tier, by=by, reason=reason)
    if not _ID_RE.fullmatch(ident):
        raise runtime_settings.SettingsError("the caller id is the lower-case fingerprint shown in the usage screen")
    return runtime_settings.set_value(llm_profiles.USER_TIER_PREFIX + ident, tier, by=by, reason=reason)


def list_assignments() -> Dict[str, Any]:
    """Every assignment now in force, and the tiers the profiles file defines."""
    data = runtime_settings.load()
    rows: List[Dict[str, Any]] = []

    def row(kind: str, ident: Optional[str], entry: Dict[str, Any]) -> None:
        rows.append({"kind": kind, "id": ident, "tier": entry.get("value"), "by": entry.get("by"), "reason": entry.get("reason"),
                     "at": entry.get("at"), "defined": entry.get("value") in llm_profiles.tiers()})

    for key, entry in sorted(data["global"].items()):
        if key == llm_profiles.TIER_KEY:
            row("global", None, entry)
        elif key.startswith(llm_profiles.ROLE_TIER_PREFIX):
            row("role", key[len(llm_profiles.ROLE_TIER_PREFIX):], entry)
        elif key.startswith(llm_profiles.USER_TIER_PREFIX):
            row("user", key[len(llm_profiles.USER_TIER_PREFIX):], entry)
    for tenant, entries in sorted(data["tenants"].items()):
        if llm_profiles.TIER_KEY in entries:
            row("tenant", tenant, entries[llm_profiles.TIER_KEY])
    parsed = llm_profiles.all_profiles()
    return {"tiers": llm_profiles.tiers(), "profiles": sorted(parsed), "assignments": rows, "roles": list(ROLES),
            "profiles_file_configured": llm_profiles.configured()}
