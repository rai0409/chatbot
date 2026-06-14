from __future__ import annotations

# Group -> tenant + role RBAC mapping (Prompt047) over an SSO identity (OIDC
# Prompt046 / enterprise bridge Prompt037). Maps an IdP groups claim to allowed
# tenants and a role, with cross-tenant rejection enforced downstream by the
# existing enforce_tenant_authorization (identity never broadens tenant access).
#
# Audit-safe: callers store only sha256 fingerprints + the role enum, never raw
# group names or identity. No secrets are read or logged here.

import os
from collections.abc import Mapping
from typing import FrozenSet, List, Optional, Sequence, Tuple

from fastapi import HTTPException

from webapi.api_auth import ApiAuthContext, _normalize_tenant, _parse_tenant_map, _record_auth_rejection

ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_USER = "user"
ROLE_VIEWER = "viewer"

_VALID_ROLES = {ROLE_ADMIN, ROLE_OPERATOR, ROLE_USER, ROLE_VIEWER}
# Higher index = more privilege.
_ROLE_RANK = {ROLE_VIEWER: 0, ROLE_USER: 1, ROLE_OPERATOR: 2, ROLE_ADMIN: 3}
_DEFAULT_ROLE = ROLE_USER


def parse_group_role_map(raw: str) -> dict:
    # Format: groupA=admin,groupB=operator,groupC=viewer. Unknown roles are
    # ignored (never grant an undefined privilege).
    mapping: dict = {}
    for entry in str(raw or "").split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        group, _, role = entry.partition("=")
        group = group.strip()
        role = role.strip().lower()
        if group and role in _VALID_ROLES:
            mapping[group] = role
    return mapping


def _as_group_list(groups) -> List[str]:
    if groups is None:
        return []
    if isinstance(groups, str):
        return [g.strip() for g in groups.replace(",", " ").split() if g.strip()]
    if isinstance(groups, (list, tuple, set, frozenset)):
        return [str(g).strip() for g in groups if str(g).strip()]
    return [str(groups).strip()]


def resolve_role_and_tenants(
    groups,
    *,
    group_tenant_map_raw: Optional[str] = None,
    group_role_map_raw: Optional[str] = None,
) -> Tuple[bool, FrozenSet[str], str]:
    # Returns (authz_enabled, allowed_tenants, role). When no group->tenant map
    # is configured, authz is disabled (parity with the no-map API-key case) and
    # the role defaults to user. A configured map enforces: only the union of
    # the caller's mapped groups' tenants is allowed (fail closed if none match).
    group_list = _as_group_list(groups)
    tenant_map_raw = group_tenant_map_raw if group_tenant_map_raw is not None else os.getenv("OIDC_GROUP_TENANT_MAP", "")
    role_map_raw = group_role_map_raw if group_role_map_raw is not None else os.getenv("OIDC_GROUP_ROLE_MAP", "")

    role = _DEFAULT_ROLE
    if role_map_raw:
        role_map = parse_group_role_map(role_map_raw)
        best = -1
        chosen = None
        for g in group_list:
            r = role_map.get(g)
            if r and _ROLE_RANK[r] > best:
                best, chosen = _ROLE_RANK[r], r
        if chosen:
            role = chosen
        else:
            # No mapped role -> least privilege.
            role = ROLE_VIEWER

    if not str(tenant_map_raw or "").strip():
        return False, frozenset(), role

    tenant_map = _parse_tenant_map(tenant_map_raw)
    allowed: FrozenSet[str] = frozenset()
    for g in group_list:
        for key, tenants in tenant_map.items():
            if _normalize_tenant(g) == _normalize_tenant(key):
                allowed = allowed | tenants
    return True, allowed, role


def role_at_least(role: Optional[str], required: str) -> bool:
    have = _ROLE_RANK.get(str(role or ""), -1)
    need = _ROLE_RANK.get(str(required or ""), 99)
    return have >= need


def enforce_role(auth: Optional[ApiAuthContext], required: str) -> None:
    # Backend role gate. A non-ApiAuthContext (direct call / Depends sentinel)
    # is a no-op, mirroring enforce_tenant_authorization.
    if not isinstance(auth, ApiAuthContext):
        return
    if not role_at_least(auth.role, required):
        _record_auth_rejection("role_forbidden")
        raise HTTPException(status_code=403, detail="insufficient role for this action")
