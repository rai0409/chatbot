# Real-IdP SSO End-to-End Validation Checklist (Entra ID / Okta)

Validates the **already-implemented** in-app OIDC path (Prompt046) + group→tenant
RBAC (Prompt047) against a **customer's real IdP tenant**. The in-repo code is
**mock-tested only**; this checklist is the steps to validate end-to-end in the
customer's staging environment. **No real credentials are used or stored here.**

## Evidence separation (read first)

| Layer | Status in this repo | Where validated |
| --- | --- | --- |
| OIDC Auth-Code + PKCE redirect build | **mock-tested** (`tests/test_oidc_login_session.py`) | repo |
| ID-token JWKS signature + iss/aud/exp/nonce | **mock-tested** (synthetic RSA/JWKS) | repo |
| state/nonce CSRF, session cookie mint/verify, fail-closed | **mock-tested** | repo |
| group→tenant + role mapping, cross-tenant rejection | **mock-tested** (`tests/test_group_tenant_rbac.py`) | repo |
| **End-to-end against Entra ID / Okta** | **NOT validated in repo** | customer staging tenant |
| TLS + cookie Secure behind a real proxy | **NOT validated in repo** | customer staging |

Do not claim end-to-end real-IdP validation until the customer-staging steps
below are signed off.

## Customer IT prerequisites

- A staging app registration in the customer IdP (Entra ID or Okta).
- A TLS-terminating reverse proxy in front of KuraDen (`OIDC_COOKIE_SECURE=true`).
- A test user and a test group for the tenant/role mapping.

## Entra ID steps

1. App registration → Redirect URI (Web): `https://<host>/auth/oidc/callback`.
2. Client secret → set `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` (env-only).
3. Token configuration → add the **groups** claim (object IDs).
4. Config: `OIDC_ISSUER=https://login.microsoftonline.com/<TENANT_ID>/v2.0`,
   `OIDC_JWKS_URI=.../discovery/v2.0/keys`,
   `OIDC_GROUP_TENANT_MAP=<GROUP_OBJ_ID>=tenant_a`,
   `OIDC_GROUP_ROLE_MAP=<ADMINS_GROUP>=admin`.

## Okta steps

1. OIDC Web app → Sign-in redirect URI `https://<host>/auth/oidc/callback`.
2. Set `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET`.
3. Add a `groups` claim to the ID token (Authorization Server → Claims).
4. Config: `OIDC_ISSUER=https://<org>.okta.com/oauth2/<AUTH_SERVER_ID>`,
   `OIDC_JWKS_URI=<issuer>/v1/keys`, group maps as above.

## End-to-end validation steps (sign off each)

- [ ] `GET /auth/oidc/login` redirects to the IdP with `state`, `nonce`,
      `code_challenge=...&code_challenge_method=S256`.
- [ ] After IdP login, `GET /auth/oidc/callback` establishes a session and
      redirects to `/chat-ui`.
- [ ] A mapped group user can access only its tenant; a non-mapped tenant
      request returns 403 (cross-tenant rejection).
- [ ] An unmapped group fails closed (403).
- [ ] Session cookie is `Secure`, `HttpOnly`, `SameSite=Lax` (behind TLS).
- [ ] No client secret / session secret / token appears in logs or responses.
- [ ] Role (admin/operator/user/viewer) surfaces correctly in `/ui/context`.

## Failure handling

| Symptom | Likely cause | Action |
| --- | --- | --- |
| 401 on callback | invalid/expired token, clock skew | check IdP clock, token lifetime |
| 400 on callback | state mismatch / expired txn cookie | retry login; check cookie path `/auth/oidc` |
| 403 unmapped | group not in `OIDC_GROUP_TENANT_MAP` | add the group object id / claim |
| no cookie set | `OIDC_COOKIE_SECURE=true` over plain HTTP | terminate TLS at the proxy |
| 503 | enabled but unconfigured | set all `OIDC_*` env vars |

## Sign-off

- Validated by: `<NAME>`  Date: `<DATE>`  IdP: `<Entra/Okta>`  Tenant alias: `<ALIAS>`
- Result: `<pass/fail>`  Notes: `<no secrets / no real identity here>`
