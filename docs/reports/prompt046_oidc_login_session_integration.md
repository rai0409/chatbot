# Prompt046: OIDC Login / Session Integration

Implementation report. Adds a **default-off** in-app OIDC (OAuth2 Authorization
Code + PKCE) login/session path with JWKS-verified ID tokens via
authlib/cryptography (no homegrown crypto). The API-key path and the Prompt037
reverse-proxy bridge are unchanged when OIDC is off.

## Dependencies

`Authlib==1.7.2` and `cryptography==49.0.0` are importable and now pinned in
`requirements.txt` (with `oauthlib`/`requests-oauthlib`). PyJWT was **not** added
(the design uses `authlib.jose` for JWT/JWKS). `authlib.jose` emits a deprecation
notice (recommends joserfc); it remains supported and is the pinned dependency,
so it is used and the import-time notice is narrowly silenced.

## Files changed

- `webapi/oidc_auth.py` (new) — config (env, fail-closed 503 when enabled but
  unconfigured), `build_login()` (state+nonce+PKCE S256, signed txn cookie),
  `verify_id_token()` (JWKS signature + iss/aud/exp + nonce + sub), `complete_login()`
  (state check → code exchange → JWKS fetch → verify → tenant map → signed
  HS256 session), and `resolve_oidc_session()` → `ApiAuthContext`.
- `webapi/api_auth.py` — `require_api_auth_headers` consults the OIDC session
  (lazy import) after the enterprise bridge; returns `None` when disabled/no
  session, so the API-key path is byte-for-byte unchanged.
- `webapi/main.py` — default-off (404) endpoints `GET /auth/oidc/login`
  (302 + httponly txn cookie), `/auth/oidc/callback` (verify → session cookie →
  302 to /chat-ui), `/auth/oidc/logout`. Added imports (`oidc_auth`,
  `RedirectResponse`).
- `requirements.txt` — pinned OIDC dependencies.
- `tests/test_oidc_login_session.py` (new).
- `docs/reports/prompt046_oidc_login_session_integration.md`.

## Security / fail-closed behavior

- Default-off: endpoints 404; a forged session cookie is ignored. API-key and
  Prompt037 paths unchanged (tested).
- **ID-token verification** rejects bad signature (incl. wrong signing key),
  wrong `iss`/`aud`, expired token, and `nonce` mismatch (tested).
- **CSRF**: callback verifies `state` against the signed txn cookie (mismatch →
  400). **PKCE** S256 challenge on login; code verifier bound in the txn.
- **Tenant mapping**: identity claim → allowed tenants via `OIDC_TENANT_MAP`;
  cross-tenant access rejected (403) and never broadened; unmapped identity
  fails closed (403). Enabled-but-unconfigured → 503.
- **Session**: short HS256-signed httponly/SameSite cookie (Secure by default;
  `OIDC_COOKIE_SECURE=false` only for local http). Identity stored as a sha256
  fingerprint of `sub` (never raw); rate-limit bucketing preserved.
- **No secret exposure**: client secret, session secret, tokens, and raw
  identity never appear in responses, redirects, logs, or metrics (tested);
  only `api_oidc_auth_total{accepted}` and `api_auth_rejection_total` enums.

## Preserved behavior

API key auth, Prompt037 enterprise bridge, tenant authorization/isolation, rate
limiting, production_safe, retrieval thresholds, and cross-encoder settings are
unchanged. End-to-end test mocks the IdP (synthetic RSA keypair / JWKS / tokens);
no real IdP credentials.

## Verification results

- `tests/test_oidc_login_session.py` + auth regression (`test_api_auth`,
  `test_enterprise_auth_bridge`, `test_api_key_tenant_authorization`,
  `test_rate_limit`): **72 passed** (1 harmless authlib deprecation warning).
- Full suite: **803 passed, 0 failed** (+10). `product_readiness_smoke.sh` exit 0;
  `limited_beta_preflight.sh` exit 0. Full suite WAS run.

## Final judgment: PASS

## Next recommendation

Prompt047 — group→tenant RBAC mapping + audit over the OIDC/enterprise identity.
