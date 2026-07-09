# Prompt058: Real-IdP SSO End-to-End Validation Workflow

Docs/workflow deliverable. Provides the steps to validate the already-implemented
in-app OIDC path (Prompt046) + group→tenant RBAC (Prompt047) against a customer's
real Entra ID / Okta tenant, with a strict mock-tested-vs-real evidence
separation. **No real credentials used or stored.** No product runtime change.

## Implementation summary

- `docs/operations/sso_real_idp_validation_checklist.md` (new) — evidence-separation
  matrix (what is mock-tested in repo vs what requires the customer tenant),
  customer-IT prerequisites, per-IdP setup (Entra ID, Okta) referencing the
  existing `OIDC_*` / RBAC config and the Prompt048 guide, an end-to-end
  sign-off checklist, a failure-handling table, and a sign-off block (alias only,
  no secrets).
- This report.

## Mock-tested evidence (re-run)

- `tests/test_oidc_login_session.py` + `tests/test_group_tenant_rbac.py`:
  **18 passed** — OIDC Auth-Code+PKCE redirect, JWKS signature + iss/aud/exp/nonce
  verification, state/nonce CSRF, session cookie mint/verify + fail-closed,
  group→tenant + role mapping, and cross-tenant rejection — all with **synthetic
  RSA/JWKS/tokens** (mock IdP).

## What is NOT validated here (requires the customer's IdP tenant)

- End-to-end login against a **real** Entra ID / Okta tenant (app registration,
  real JWKS/rotation, real group claims, redirect URIs).
- TLS + cookie `Secure` behavior behind a **real** reverse proxy.

These are documented as customer-staging sign-off steps and are **explicitly not
run / not asserted** in this repo. Per the prompt, the deliverable is the
validation workflow + checklist (template/synthetic completion), which is
complete.

## Verification results

- Mock OIDC/RBAC suites: **18 passed**. `--collect-only`: **850 collected**.
  Full suite **not run** for this docs-only prompt (no product source change;
  the OIDC behavior is unchanged and already covered).

## Final judgment: PASS

(PASS because the prompt's deliverable is the validation workflow/checklist with
mock evidence + documented real-tenant steps; real-IdP e2e is correctly marked
not-run, not fabricated.)

## Next recommendation

Prompt059 — customer monitoring wiring + ops acceptance (synthetic signals).
