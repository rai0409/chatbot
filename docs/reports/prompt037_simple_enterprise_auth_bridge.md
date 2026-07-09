# Prompt037: Simple Enterprise Auth Bridge

Implementation report. Adds a default-off "trusted reverse-proxy header"
enterprise authentication bridge that can sit behind a customer-controlled
SSO/IdP gateway, without replacing or weakening the existing API-key tenant
authorization path. This is the safe integration **boundary** only — no LDAP /
SAML / OIDC / AD / Okta / Kerberos is implemented in-app.

## Files changed

- `webapi/enterprise_auth.py` (new) — the bridge: `enterprise_auth_enabled()`
  and `resolve_enterprise_auth(headers) -> Optional[ApiAuthContext]`. Returns
  `None` when disabled or when no enterprise signal is present (caller falls
  back to the unchanged API key path); raises fail-closed `HTTPException` on a
  malformed/invalid enterprise attempt; returns an `ApiAuthContext` on success.
- `webapi/api_auth.py` — `require_api_auth_headers` now calls the bridge first
  via a **lazy import** (avoids a circular import). When the bridge returns
  `None`, the existing API-key logic runs byte-for-byte unchanged.
- `.env.example` — documented `ENTERPRISE_AUTH_ENABLED` /
  `ENTERPRISE_AUTH_TRUST_TOKEN` / `ENTERPRISE_AUTH_TENANT_MAP` (placeholders).
- `docs/operations.md` — "Enterprise auth bridge (optional, default off)"
  section explaining how a customer reverse proxy / SSO gateway sets the
  trusted headers and strips client-supplied spoofs.
- `tests/test_enterprise_auth_bridge.py` (new) — focused tests.
- `docs/reports/prompt037_simple_enterprise_auth_bridge.md` (this report).

## Exact enterprise auth bridge behavior

Trusted headers (case-insensitive): `X-Enterprise-Auth-Trust` (trust signal),
`X-Enterprise-Tenant` (identity → mapped to allowed tenants), and optional
`X-Enterprise-User` / `X-Enterprise-Email` / `X-Enterprise-Groups`.

Config (process env, same pattern as `webapi/api_auth.py`; `.env` is never
opened): `ENTERPRISE_AUTH_ENABLED`, `ENTERPRISE_AUTH_TRUST_TOKEN`,
`ENTERPRISE_AUTH_TENANT_MAP` (reuses the `key=tenant_a|tenant_b` map parser).

On success the bridge returns
`ApiAuthContext(authenticated=True, tenant_authorization_enabled=True,
allowed_tenants=<mapped>, key_fingerprint=sha256(identity)[:12])`. The request
`tenant_id` is then checked against `allowed_tenants` by the existing
`enforce_tenant_authorization` — identity never broadens tenant access. The
fingerprint also keeps the existing per-identity rate-limit bucketing intact.

## Default-off behavior

`ENTERPRISE_AUTH_ENABLED` unset/false → `resolve_enterprise_auth` returns
`None` immediately; `X-Enterprise-*` headers are never read; the API-key path
(and the open-when-`API_AUTH_ENABLED=false` behavior) is exactly unchanged.
Spoofed enterprise headers are therefore ignored when disabled. Verified by
`test_default_off_ignores_spoofed_headers` and
`test_default_off_api_key_path_unchanged_ignores_enterprise_headers`.

## Enabled-mode behavior

- No enterprise headers on the request → returns `None` → API-key path still
  works (`test_api_key_path_works_when_enterprise_enabled`).
- Enterprise attempt (trust and/or identity headers present): trust token must
  be configured and must match (constant-time `hmac.compare_digest`); the
  identity must map to ≥1 tenant. On success → authenticated context scoped to
  the mapped tenants.
- Group/multi-tenant identities map to multiple allowed tenants
  (`test_enabled_group_identity_maps_to_multiple_tenants`).

## Security fail-closed behavior

- Trust token configured but **missing** on request → **401**.
- Trust token present but **invalid** → **403**.
- Enterprise mode on but **no trust token configured** → **503** (refuses to
  trust any forwarded header).
- Identity **unmapped** / no tenant mapping configured → **403**.
- **Cross-tenant**: identity mapped to `tenant_a`, request `tenant_id=tenant_b`
  → **403** (pipeline never invoked; verified end-to-end on `/chat`).
- All rejections record stable enum labels via the existing
  `api_auth_rejection_total` counter (`enterprise_trust_missing` /
  `enterprise_trust_invalid` / `enterprise_tenant_unmapped`); a success records
  `api_enterprise_auth_total{accepted}`. No trust token, API key, raw identity,
  prompt, or document text is logged or returned — only fingerprints/enum labels.

## Tests run and results

- `tests/test_enterprise_auth_bridge.py`: **12 passed** (default-off spoof
  ignore; API-key path unchanged; missing/invalid/unconfigured trust; valid
  accept; group mapping; cross-tenant + unmapped reject; end-to-end `/chat`
  with no-secret-exposure assertions on body+metrics; disabled resolver None).
- Targeted regression (`test_api_auth`, `test_api_key_tenant_authorization`,
  `test_tenant_isolation`, `test_rate_limit`, `test_enduser_chat_ui` [Prompt034],
  `test_chroma_where_builder` [Prompt035], `test_monitoring_alerts` [Prompt036],
  `test_chat_stream`): **108 passed**.
- Full suite: **761 passed, 0 failed** (collect-only 761; +12 new).
- `scripts/product_readiness_smoke.sh`: exit 0 (117 passed).
- `scripts/limited_beta_preflight.sh`: exit 0 (PREFLIGHT OK).

## Confirmation of non-changes

Not changed or accessed:

- `.env` not read (env via `os.getenv`, same as `api_auth.py`); no secrets
  printed/inferred; no `.env` model names; no real customer data (synthetic test
  values only).
- No vectorstore mutation; no Docker; no deploy; no remote push.
- Retrieval/distance thresholds and cross-encoder settings unchanged.
- Global tenant **isolation** semantics unchanged; tenant **authorization** is
  reused (not broadened) — enterprise identity only supplies `allowed_tenants`,
  still enforced by `enforce_tenant_authorization`.
- Rate-limiter semantics unchanged (still bucketed by `key_fingerprint`).
- `production_safe` behavior unchanged.
- **Prompt034** chat UI behavior unchanged (verified by `test_enduser_chat_ui.py`).
- **Prompt035** Chroma `$and` where behavior unchanged (verified by
  `test_chroma_where_builder.py`).
- **Prompt036** monitoring/alert behavior unchanged except an additive, safe
  aggregate counter (`api_enterprise_auth_total`) and new enum labels on the
  existing `api_auth_rejection_total` (verified by `test_monitoring_alerts.py`).
- No new dependencies.
