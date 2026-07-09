# KuraDen Security / Compliance Evidence Pack

Conservative security evidence for commercial discussions. **No compliance
certification is claimed.** Placeholders only; no secrets, no real identities.

## 1. Audit log export (redacted aggregate)

- Audit JSONL (`runs/audit/*.jsonl`) records request/trace ids, tenant id,
  question text, answer mode, guard reason, citation counts, latency — and
  deliberately **not** API keys, full candidate payloads, or approved answer
  bodies (ids only). See `docs/operations.md` (Log retention).
- For security/compliance review, `scripts/audit_export.py` produces a **redacted
  aggregate** export: counts grouped by date / tenant / kind / answer_mode /
  guard_reason — it **drops raw question text, document text, identity, and any
  secret** (enforced + tested in `tests/test_audit_export.py`, `webapi/audit_export.py`).

## 2. Retention policy

- `runs/audit/*.jsonl`: rotate daily or at 100 MB; retain ~90 days online then
  delete or move to the customer's archival storage per the data agreement.
- Audit logs contain user question text → treat with the same confidentiality as
  source documents; include them in tenant offboarding deletion.
- Conversation history (`runs/conversations/`, default-off) is per-(tenant,
  identity) isolated with max-count/max-age retention (`webapi/conversation_store.py`).

## 3. Access review checklist

- [ ] `API_AUTH_ENABLED=true` with per-tenant keys; `API_AUTH_TENANT_MAP` covers
      every served tenant (fail-closed).
- [ ] `ADMIN_AUTH_ENABLED=true`; admin token rotated per the rotation runbook.
- [ ] SSO (reverse-proxy bridge or OIDC) maps groups→tenant/role; cross-tenant
      fails closed.
- [ ] `SEARCH_DEBUG_ENABLED=false`; `RATE_LIMIT_ENABLED=true`.
- [ ] Periodic review of `API_AUTH_KEYS` / tenant map / admin token holders.

## 4. Secret handling review

- Secrets (API keys, admin token, OIDC client/session secrets, trust token,
  Slack/SMTP creds) are **env-only**; never committed; never logged.
- `.env` is gitignored and never baked into images (proven by `deploy_smoke.sh`).
- Identity is stored only as sha256 fingerprints; metrics/alerts/audit-export use
  stable enums (no raw keys/identity/queries).
- Key rotation: `docs/security_operations.md` (zero-downtime + leaked-key response).

## 5. Data handling boundary

- On-prem / closed network; documents do not leave the customer's network.
- The vendor does not receive or retain customer documents in a PoC.
- Customer documents / PoC outputs stay under gitignored local paths
  (`runs/poc/<alias>/`).

## 6. Security review questionnaire (draft — expert review required)

- Authentication: API key + reverse-proxy SSO + in-app OIDC (Auth Code + PKCE,
  JWKS-verified); admin token for privileged routes.
- Authorization: per-key/identity → tenant, fail-closed; group→tenant RBAC.
- Transport: TLS terminated at the customer proxy; app serves plain HTTP on
  `:8000` (never exposed directly).
- Data at rest: local vectorstore + audit logs on the customer host; retention
  per policy.
- Logging: no secrets/raw keys; question text in audit logs (treat as
  confidential); redacted aggregate export available.
- Vulnerability/dep management: pinned `requirements.txt`; `release_check.py`
  freeze gate.
- **Not claimed:** ISO/SOC2/other certifications; penetration-test results;
  formal compliance attestations. These require a separate, expert-led program.
