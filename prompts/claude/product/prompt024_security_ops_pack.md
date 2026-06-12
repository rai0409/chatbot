# Prompt024: Security Ops Pack (B5)

You are working in:

/home/rai/chatbot

## Goal

The security operations batch (B5 from docs/reports/current_state_chatbot_direction_autonomous_plan.md): application-level rate limiting, key rotation runbook, and secrets handling documentation — the remaining items before exposing a deployment to external callers.

## Execution mode

Proceed autonomously. Commit and tag automatically only on PASS with a prompt-scoped diff.

Stop only for: destructive operations, user-data deletion, secrets/.env access, remote push/deploy, production vectorstore/default collection mutation, required network/model downloads, ambiguous missing targets, or unresolved verification failure after one bounded fix attempt.

Do not read .env. Do not change cross-encoder settings, distance thresholds, tenant authorization semantics, or the too_general guard. No new dependencies (stdlib only).

## Preconditions to verify before implementing

- Prompt023 complete: tag prompt023-deploy-ops exists; scripts/deploy_smoke.sh, scripts/backup.sh, scripts/restore.sh, docs/operations.md present; tests/test_deploy_ops.py passes.
- API auth layer present: webapi/api_auth.py with ApiAuthContext / enforce_tenant_authorization; tests/test_api_key_tenant_authorization.py passes.

## Scope

1. Application-level rate limiting (stdlib only, default OFF):

- New module webapi/rate_limit.py: in-process token bucket or fixed-window limiter keyed by API key fingerprint (reuse the existing sha256 fingerprint — never raw keys) with anonymous requests bucketed together.
- Env knobs: RATE_LIMIT_ENABLED (default false — behavior unchanged by default), RATE_LIMIT_REQUESTS_PER_MINUTE (default 60), documented in .env.example and docs/production_readiness_checklist.md.
- Enforcement on the API-key-protected POST endpoints (/chat, /chat/stream, /chat/product-preview, /chat/feedback, /search) AFTER authentication, returning 429 with a Retry-After header and a stable JSON detail. /health and /metrics stay unlimited.
- Per-process semantics must be documented (same caveat as the metrics counters).

2. Key rotation runbook (docs/security_operations.md):

- zero-downtime rotation procedure using the comma-separated API_AUTH_KEYS (add new key → roll clients → remove old key) including the API_AUTH_TENANT_MAP update step
- leaked-key response: immediate removal, tenant map audit, audit-log review window
- smoke commands to verify a rotated deployment (reuse deploy_smoke patterns; never echo real keys)

3. Secrets handling documentation (same file):

- .env handling rules (never commit, file permissions, never bake into images — reference the existing .dockerignore behavior), placeholder discipline, and how the deploy smoke proves the image carries no secrets
- guidance for external secret stores as an optional hardening step (documentation only, no integration)

4. Targeted tests only:

- limiter unit tests (window math, per-key isolation, anonymous bucket, disabled-by-default no-op) with a controllable clock — no sleeps
- endpoint behavior: 429 + Retry-After after exceeding the limit with auth enabled; default-off leaves all existing tests green; raw keys never appear in 429 bodies or headers
- route coverage test: limited endpoints enforce after auth; /health & /metrics unlimited

## Explicit non-goals

WAF, OAuth/JWT, billing/quotas per tenant, distributed rate limiting (Redis), monitoring exporters (separate batch), UI, new dependencies.

## Verification

Targeted tests first, then:

python -m pytest tests/test_api_key_tenant_authorization.py tests/test_tenant_isolation.py tests/test_chat_stream.py -q

python -m pytest --collect-only -q

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt024_smoke_check.json

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt024_qa_pair_check.json

scripts/product_readiness_smoke.sh if safe. Optionally re-run scripts/deploy_smoke.sh to confirm container behavior is unchanged with the limiter off.

## Commit/tag policy

PASS: commit "prompt024 security ops pack", tag prompt024-security-ops. PARTIAL/FAIL: no commit, no tag, report blocker and next command.

## Required final output

1. Preconditions
2. Implementation summary (limiter semantics, knobs, docs)
3. Verification results
4. Git diff summary
5. Commit/tag result
6. Final judgment: PASS / PARTIAL / FAIL
7. Next prompt file: if PASS, write exactly one next prompt to prompts/claude/product/prompt025_observability_beta_gate.md (metrics export + alert thresholds + readiness report regeneration + beta go/no-go — batch B6). Do not execute it.
