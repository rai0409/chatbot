# Prompt045: Enterprise SSO Architecture Decision

Analysis/decision report (no runtime change). Decides the exact SSO path for
蔵伝 / KuraDen and defines the precise implementation contract + dependency
gate for Prompt046.

## Current auth model (repo evidence)

- `webapi/api_auth.py` — API-key auth + fail-closed per-key→tenant authorization.
- `webapi/enterprise_auth.py` (Prompt037) — **default-off trusted reverse-proxy
  header bridge**: when a gateway authenticates the user and forwards
  `X-Enterprise-*` headers plus a matching `X-Enterprise-Auth-Trust` token, the
  identity maps to allowed tenants without broadening access.
- `webapi/admin_auth.py` — admin token gate for privileged routes.

## Dependency analysis (decisive)

Importability check in this environment: `authlib` **absent**, `jwt` (PyJWT)
**absent**, `python-jose` **absent**, `cryptography` **absent**, `itsdangerous`
**absent**. `requests`/`httpx` present (HTTP only, no JWT/JWKS verification).

Robust in-app OIDC requires verifying RS256-signed ID tokens against the IdP's
JWKS — which **requires a mature crypto/JWT library** (e.g. `authlib` +
`cryptography`, or `pyjwt[crypto]`). Implementing this without such a library
would mean homegrown crypto, which is explicitly forbidden. Adding the library
is a network install that this offline, no-external-download program does not
permit, and shipping it unpinned/unvetted would be unsafe.

## Decision

1. **Supported SSO path TODAY = reverse-proxy / gateway OIDC (or SAML) feeding
   the Prompt037 trusted-header bridge.** This is already implemented and tested:
   the customer runs an IdP-connected proxy (e.g. oauth2-proxy, Entra ID
   App Proxy, Okta) that authenticates the user, strips client-supplied
   `X-Enterprise-*` headers, and injects trusted identity headers + the shared
   trust token. No in-app crypto needed; works with Entra ID, Okta, Google,
   Keycloak, Auth0, and SAML IdPs (the proxy speaks the IdP protocol).

2. **Primary FUTURE in-app path = OIDC / OAuth2 Authorization Code + PKCE
   (default-off)**, to be implemented in Prompt046 **only when** `authlib` +
   `cryptography` (or `pyjwt[crypto]`) are provisioned in the environment and
   added to `requirements.txt` with pinned versions. No homegrown crypto.

3. **SAML / LDAP = gateway/proxy integration only** (not in-app), reusing the
   same bridge. Direct in-app SAML/LDAP is out of scope.

## What "SSO-connected" means for this product

- A user authenticates against the **customer's IdP** (Entra ID / Okta / etc.).
- An IdP-connected **proxy** (today) or a **default-off in-app OIDC flow**
  (future, Prompt046) establishes the authenticated identity + groups.
- Identity/groups map to **tenant + role** via the existing fail-closed
  authorization (full group→tenant RBAC is Prompt047); identity never broadens
  the request tenant_id.

- **Locally implementable + testable now:** the proxy-bridge path (done,
  Prompt037, tested); and — once the dependency is provisioned — the in-app OIDC
  code path against a **mock IdP / mock JWKS** with synthetic tokens.
- **Requires a real customer IdP tenant:** end-to-end validation against live
  Entra ID/Okta (app registration, real JWKS, real group claims) — documented in
  Prompt048, never asserted as locally verified.

## Implementation contract for Prompt046 (when unblocked)

- Config-gated `ENTERPRISE_OIDC_ENABLED` (default false): zero behavior change
  and no honored request surface when off; the API-key path and the Prompt037
  bridge remain working.
- Endpoints: `GET /auth/oidc/login` (redirect with state+nonce+PKCE) and
  `GET /auth/oidc/callback` (code exchange + ID-token verification via the
  library's JWKS client). Secure server-side session via signed, httponly,
  SameSite cookies; session/signing secret env-only, never logged.
- On valid token: build the identity (sha256 fingerprint for audit/rate-limit)
  and map claims/groups to the existing tenant-authorization context (Prompt047
  does the group→tenant/role detail). Fail closed on invalid/missing/expired
  token.
- Dependency: add `authlib` (+ `cryptography`) pinned to `requirements.txt`,
  justified here; do not write crypto.

## Decision: PASS

The decision is recorded; **Prompt046 is dependency-gated** and cannot be
implemented to a secure PASS in the current offline environment (no crypto/JWT
library; homegrown crypto forbidden; no network installs). The reverse-proxy
SSO path (Prompt037) is the supported mechanism in the meantime.

## Next recommendation

Prompt046 is **blocked on a missing security dependency** in this environment.
Resume Prompt046 only where `authlib`+`cryptography` (or `pyjwt[crypto]`) are
installable and can be pinned into `requirements.txt`. Until then, customers use
the reverse-proxy OIDC path documented for Prompt048.
