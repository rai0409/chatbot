# Prompt047: Group→Tenant RBAC Mapping & Audit

Implementation report. Adds group→tenant mapping and role-based access control
over the OIDC (Prompt046) / enterprise-bridge (Prompt037) identity, with
cross-tenant rejection, audit-safe fingerprints, role enforcement, and tests.

## Files changed

- `webapi/rbac.py` (new) — `resolve_role_and_tenants(groups, ...)` maps an IdP
  groups claim to (authz_enabled, allowed_tenants, role) via `OIDC_GROUP_TENANT_MAP`
  / `OIDC_GROUP_ROLE_MAP`; roles are `admin/operator/user/viewer` with a strict
  precedence (highest mapped group wins; unmapped → least privilege `viewer`).
  `parse_group_role_map` (drops unknown roles), `role_at_least`, and
  `enforce_role` (backend 403 gate; no-op on non-context).
- `webapi/api_auth.py` — `ApiAuthContext` gains an optional `role` field
  (default `None`; all existing constructions unchanged → backward compatible).
- `webapi/oidc_auth.py` — when a group map is configured, the OIDC callback
  derives allowed tenants + role from the groups claim (else the single tenant
  claim + default role); the role is stored in the signed session and surfaced
  in `resolve_oidc_session` → `ApiAuthContext.role`. Adds `api_role_total` enum
  metric.
- `webapi/main.py` — `/ui/context` now reports the authenticated OIDC session
  role (backend-authoritative) when present, else the admin-token role.
- `tests/test_group_tenant_rbac.py` (new).
- `docs/reports/prompt047_group_tenant_rbac_mapping_and_audit.md`.

## Security / audit-safe behavior

- **Cross-tenant rejection**: group-mapped `allowed_tenants` are enforced by the
  existing `enforce_tenant_authorization`; a request tenant outside the mapped
  set is 403 and identity never broadens access (tested end-to-end via OIDC).
- **Fail closed**: an unmapped group yields no allowed tenants → 403; unknown
  roles are dropped; no map → authz disabled with default role (parity with the
  no-map API-key case).
- **Audit-safe**: only sha256 identity fingerprints and the role/accepted enum
  labels are recorded; raw group names and raw identity (e.g. `alice@example.com`)
  never appear in metrics/audit (tested). No secrets read or logged.
- Role enforcement is **backend** (`enforce_role`); the UI role is cosmetic.

## Preserved behavior

API key auth, Prompt037 bridge, Prompt046 OIDC, tenant authorization/isolation,
rate limiting, production_safe, retrieval thresholds, cross-encoder settings —
all unchanged. Synthetic mock-IdP tokens only; no real IdP credentials.

## Verification results

- `tests/test_group_tenant_rbac.py` + `test_oidc_login_session.py` +
  `test_tenant_isolation.py` + `test_api_key_tenant_authorization.py` +
  `test_admin_console_roles_branding.py`: **53 passed**.
- Full suite: **811 passed, 0 failed** (+8). `product_readiness_smoke.sh` exit 0;
  `limited_beta_preflight.sh` exit 0. Full suite WAS run.

## Final judgment: PASS

## Next recommendation

Prompt048 — Entra/Okta/reverse-proxy/TLS deployment guides (docs).
