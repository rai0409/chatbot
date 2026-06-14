# Prompt048: Entra ID / Okta / Reverse-Proxy / TLS Deployment Guides

Deployment guides (docs only; no runtime change) for connecting 蔵伝 / KuraDen
to a real customer IdP. Two supported SSO mechanisms:

- **In-app OIDC** (Prompt046, default-off) — the app speaks OIDC directly.
- **Reverse-proxy / gateway** (Prompt037 trusted-header bridge, default-off) —
  an IdP-connected proxy authenticates and forwards trusted headers.

All examples use **placeholders**; never commit real client secrets, trust
tokens, or session secrets. Secrets are env-only and never logged.

## A. In-app OIDC (Prompt046 + RBAC Prompt047)

Enable with env (placeholders):

```bash
ENTERPRISE_OIDC_ENABLED=true
OIDC_ISSUER=https://login.microsoftonline.com/<TENANT_ID>/v2.0      # Entra ID
OIDC_CLIENT_ID=<APP_CLIENT_ID>
OIDC_CLIENT_SECRET=<APP_CLIENT_SECRET>          # env-only, never commit
OIDC_REDIRECT_URI=https://chat.example.co.jp/auth/oidc/callback
OIDC_JWKS_URI=https://login.microsoftonline.com/<TENANT_ID>/discovery/v2.0/keys
OIDC_SESSION_SECRET=<RANDOM_32B_HEX>            # signs the session cookie
OIDC_COOKIE_SECURE=true                          # required behind TLS
# group -> tenant + role (Prompt047)
OIDC_GROUPS_CLAIM=groups
OIDC_GROUP_TENANT_MAP=<GROUP_OBJ_ID_A>=tenant_a,<GROUP_OBJ_ID_B>=tenant_b
OIDC_GROUP_ROLE_MAP=<GROUP_ADMINS>=admin,<GROUP_OPS>=operator
```

Login flow: user → `GET /auth/oidc/login` → IdP → `GET /auth/oidc/callback` →
signed session cookie → `/chat-ui`. Endpoints are 404 when disabled.

### A.1 Microsoft Entra ID (Azure AD)

1. Azure portal → App registrations → New registration.
2. Redirect URI (Web): `https://<host>/auth/oidc/callback`.
3. Certificates & secrets → new client secret → set `OIDC_CLIENT_ID` /
   `OIDC_CLIENT_SECRET`.
4. Token configuration → add the **groups** claim (or app roles). Entra emits
   group **object IDs** — use those as the keys in `OIDC_GROUP_TENANT_MAP`.
5. `OIDC_ISSUER=https://login.microsoftonline.com/<TENANT_ID>/v2.0`;
   `OIDC_JWKS_URI` = the v2.0 discovery keys URL above.

### A.2 Okta

1. Okta admin → Applications → Create App Integration → OIDC → Web Application.
2. Sign-in redirect URI: `https://<host>/auth/oidc/callback`.
3. Set `OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET`.
4. Add a **groups** claim to the ID token (Authorization Server → Claims:
   `groups`, filter as needed).
5. `OIDC_ISSUER=https://<org>.okta.com/oauth2/<AUTH_SERVER_ID>` (or the org
   issuer); `OIDC_JWKS_URI` = issuer + `/v1/keys`.

### A.3 Generic OIDC (Keycloak / Google / Auth0)

Use the provider's discovery document (`/.well-known/openid-configuration`) to
fill `OIDC_ISSUER`, `OIDC_JWKS_URI`, and the authorize/token endpoints
(`OIDC_AUTH_ENDPOINT`, `OIDC_TOKEN_ENDPOINT` if not derivable from the issuer).

## B. Reverse-proxy / gateway path (Prompt037 bridge)

When the customer prefers a gateway (SAML-only IdP, or central auth at the
edge), front the app with an IdP-connected proxy (oauth2-proxy, Entra App Proxy,
Okta Access Gateway). The proxy authenticates, **strips any client-supplied**
`X-Enterprise-*` headers, and injects trusted ones:

```bash
ENTERPRISE_AUTH_ENABLED=true
ENTERPRISE_AUTH_TRUST_TOKEN=<SHARED_SECRET_ONLY_THE_PROXY_KNOWS>
ENTERPRISE_AUTH_TENANT_MAP=<ENT_TENANT_A>=tenant_a,<GROUP_X>=tenant_b|tenant_c
```

nginx sketch (the proxy sets the trust token from its own secret, and clears
inbound spoofing):

```nginx
# clear anything a client tried to send
proxy_set_header X-Enterprise-User  "";
proxy_set_header X-Enterprise-Email "";
proxy_set_header X-Enterprise-Tenant "";
# inject trusted values from the authenticated session
proxy_set_header X-Enterprise-Auth-Trust "REPLACE_WITH_PROXY_SECRET";
proxy_set_header X-Enterprise-Tenant     $sso_tenant;   # from the IdP assertion
proxy_set_header X-Enterprise-User       $sso_user;
```

## C. TLS termination

Terminate TLS at the proxy; the container serves plain HTTP on `:8000` and must
never be exposed directly (see `docs/operations.md` "Reverse proxy / TLS
reference"). With OIDC, set `OIDC_COOKIE_SECURE=true` so the session cookie is
Secure + httponly + SameSite=Lax. SSE (`/chat/stream`) requires
`proxy_buffering off` / `flush_interval -1`.

## D. Audit logging

- App-side: OIDC/enterprise rejections increment `api_auth_rejection_total`
  (enum reasons); accepted logins increment `api_oidc_auth_total` /
  `api_enterprise_auth_total` and `api_role_total`. Identity is a sha256
  fingerprint only — no raw email/group/token.
- Proxy-side: enable the proxy's access/auth logs for the IdP handshake; keep
  them per the customer's data agreement (see log retention in `docs/operations.md`).

## E. Locally tested vs requires a real customer IdP tenant

| Item | Locally tested (this repo) | Requires customer IdP tenant |
| --- | --- | --- |
| OIDC Authorization Code + PKCE redirect build | Yes (`test_oidc_login_session`) | — |
| ID-token JWKS signature + iss/aud/exp/nonce verification | Yes (synthetic RSA/JWKS/tokens) | Real signing keys/rotation |
| Session cookie mint/verify + fail-closed | Yes | — |
| Group→tenant + role mapping, cross-tenant rejection | Yes (`test_group_tenant_rbac`) | Real group object IDs/claims |
| Reverse-proxy trusted-header bridge | Yes (`test_enterprise_auth_bridge`) | Real proxy + IdP |
| End-to-end login against Entra ID / Okta | **No** | **Yes** (app registration, real JWKS, real groups, redirect URIs) |
| TLS/cookie Secure behavior behind a real proxy | **No** (documented) | **Yes** |

**Do not claim** end-to-end Entra/Okta verification as tested — it requires a
customer IdP tenant. The app-side OIDC/bridge/RBAC logic is verified with
synthetic mocks only.

## Verification (docs-only)

No runtime change; no tests added. `git status` limited to this report;
`pytest --collect-only` unchanged.

## Final judgment: PASS

## Next recommendation

Prompt049 — Prometheus + Grafana observability pack.
