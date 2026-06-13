# Production Readiness Checklist

This checklist is for commercial RAG / chatbot exposure readiness. It is a smoke and operations checklist, not a production approval by itself.

## Admin Auth

- [ ] `GET /admin/review` is protected when `ADMIN_AUTH_ENABLED=true`.
- [ ] `GET /admin/review/items` is protected when `ADMIN_AUTH_ENABLED=true`.
- [ ] `POST /admin/review/action` is protected when `ADMIN_AUTH_ENABLED=true`.
- [ ] `ADMIN_AUTH_TOKEN` is configured and non-empty when admin auth is enabled.
- [ ] Admin routes are not publicly exposed unless auth is enabled and verified.

## API Auth

Env vars (all optional; defaults preserve open local behavior):

- `API_AUTH_ENABLED` (default: false) — when true, `/chat`, `/search`, `/search/debug`, `/chat/product-preview`, and `/chat/feedback` require an API key via `X-Api-Key` or `Authorization: Bearer`.
- `API_AUTH_KEYS` (default: empty) — comma-separated accepted keys. If auth is enabled and no keys are configured, all protected requests are rejected with 503.
- `API_AUTH_TENANT_MAP` (default: empty) — optional API-key-to-tenant authorization map, format `key1=tenant_a,key2=tenant_a|tenant_b`. When set (with API auth enabled), the requested `tenant_id` (missing/blank normalizes to `default`) is enforced server-side on `/chat`, `/chat/stream`, `/chat/product-preview`, and `/chat/feedback`: a key may access only its listed tenants, and a valid key missing from the map is rejected with 403 (fail closed). When unset, legacy behavior is preserved: any valid API key may access any requested `tenant_id`. Never commit real keys; rotate any key that leaks.
- `SEARCH_DEBUG_ENABLED` (default: true) — when false, `/search/debug` returns 404.
- `CORS_ALLOW_ORIGINS` (default: empty) — comma-separated origin allowlist. Empty means no CORS middleware is added.

Checklist:

- [ ] `API_AUTH_ENABLED=true` and non-empty `API_AUTH_KEYS` on any public deployment.
- [ ] `API_AUTH_TENANT_MAP` is configured on any deployment that serves more than the default tenant.
- [ ] `/search/debug` is disabled (`SEARCH_DEBUG_ENABLED=false`) or protected: with API auth enabled it additionally requires the admin token (`ADMIN_AUTH_TOKEN`).
- [ ] `/health` remains unauthenticated by design; confirm it leaks no sensitive values.
- [ ] CORS origins are an explicit allowlist; no wildcard with credentials.

## Rate Limiting

Env vars (defaults preserve existing behavior — the limiter is OFF unless enabled):

- `RATE_LIMIT_ENABLED` (default: false) — when true, the API-key-protected POST endpoints (`/chat`, `/chat/stream`, `/chat/product-preview`, `/chat/feedback`, `/search`) enforce an in-process request budget AFTER authentication. Requests over budget receive 429 with a `Retry-After` header and `{"detail": "rate limit exceeded"}`. `/health` and `/metrics` are never rate limited.
- `RATE_LIMIT_REQUESTS_PER_MINUTE` (default: 60) — fixed-window budget per API key fingerprint (sha256-derived; raw keys never enter limiter state). Requests without a key fingerprint (auth disabled) share one anonymous bucket. With auth enabled, rejected (401/403) requests never consume budget.

Semantics caveat: the limiter is **per-process** (same caveat as the metrics counters). With N uvicorn workers the effective global limit is up to N × `RATE_LIMIT_REQUESTS_PER_MINUTE`. Distributed limiting (e.g. Redis) is an explicit non-goal; size the per-minute budget with the worker count in mind, and keep proxy-level limits as defense in depth.

Checklist:

- [ ] `RATE_LIMIT_ENABLED=true` on any public deployment, with a budget sized for worker count.
- [ ] 429 responses carry `Retry-After` and never echo API keys.
- [ ] See `docs/security_operations.md` for key rotation and secrets handling.

## Observability

- `GET /metrics` returns uptime, request totals, and a `counters` object
  (answer modes, guard reasons, fallback/cache-hit counts, provider error
  types). Counters are **per-process**: with multiple uvicorn workers each
  worker reports only its own numbers; aggregate externally if needed.
- Trace data (`/search/debug`, audit events) includes `stage_latency_ms`
  with `retrieval_ms` and, when generation runs, `generation_ms`.

## Runtime Safety

- [ ] `/chat` default behavior is unchanged.
- [ ] `/chat/product-preview` default behavior is unchanged when `product_profile` is absent.
- [ ] Approved similar non-exact matches remain candidate-only.
- [ ] Similar candidates are not inserted into final `answer_text`.
- [ ] Production rerank is not enabled by default.
- [ ] LLM answer and LLM rerank are off unless explicitly enabled by a safe profile and policy.

## Product Profiles

- [ ] `production_safe` disables similar auto-answer.
- [ ] `production_safe` disables LLM answer and LLM rerank.
- [ ] `production_safe` disables debug comparison.
- [ ] `production_safe` disables `feedback_preview_rerank`.
- [ ] `production_low_cost` disables expensive comparison and LLM behavior.
- [ ] `pilot_high_accuracy`, `evaluation`, and `dev_debug` do not enable similar auto-answer.
- [ ] Request overrides cannot enable unsafe features disabled by the selected profile.

## Audit And Feedback Safety

- [ ] Audit logging does not store full private payloads.
- [ ] Audit logging stores candidate IDs, not full candidate payloads or approved answers.
- [ ] Feedback logging does not immediately change production ranking.
- [ ] Feedback rerank remains preview, eval, and policy gated.
- [ ] Feedback-derived profiles require offline evaluation before promotion.

## Admin And Review Operations

- [ ] Review queue endpoint uses a safe field whitelist.
- [ ] Review actions are logged to bounded JSONL.
- [ ] Review action logs do not store full candidate payloads or approved answers.
- [ ] Admin routes must not be publicly exposed without auth.

## Known Production Blockers

- [ ] Tenant/customer runtime selection is not fully complete.
- [ ] Knowledge manifest and source versioning are not complete.
- [ ] Citation/source metadata hardening is not complete.
- [ ] DB persistence and tenant isolation are not complete.
- [ ] Rollback/profile promotion workflow is not complete.

## Required Smoke Commands

Run the local smoke script:

```bash
scripts/product_readiness_smoke.sh
```

Or run the same checks directly:

```bash
.venv/bin/python -m pytest \
  tests/test_admin_auth.py \
  tests/test_review_queue_page.py \
  tests/test_review_actions.py \
  tests/test_product_profile.py \
  tests/test_product_route_policy.py \
  tests/test_production_readiness_report.py \
  tests/test_product_preview_profiles.py \
  tests/test_product_preview_chat.py \
  tests/test_product_preview_feedback_rerank.py \
  tests/test_product_preview_feature_rerank.py -q

.venv/bin/python -m py_compile \
  webapi/main.py \
  webapi/admin_auth.py \
  eval/production_readiness_report.py \
  rag_core/product_profile.py \
  rag_core/product_route_policy.py
```

## Production Readiness Report

Generate the offline production readiness report with:

```bash
.venv/bin/python eval/production_readiness_report.py
```

Default outputs:

- `artifacts/readiness/production_readiness_report.json`
- `artifacts/readiness/production_readiness_report.md`

Interpretation:

- `blocked_for_production`: a critical static safety check failed. Do not expose the deployment.
- `needs_review`: critical static checks passed, but optional artifacts, dirty worktree state, or deployment-specific settings need review.
- `ready_for_limited_preview`: static checks support a limited preview only. This is not full production approval.

Full production readiness still requires human review, deployment-specific auth and tenant checks, generated manifest review, rollback planning, and deployed server smoke tests.

## Manual Curl Smoke Examples

These examples require a local server to already be running. The smoke script prints them but does not start a server.

Admin auth disabled:

```bash
curl -i -s http://127.0.0.1:8000/admin/review/items
```

Admin auth enabled without token should reject:

```bash
ADMIN_AUTH_ENABLED=true ADMIN_AUTH_TOKEN=local-admin-token .venv/bin/python -m uvicorn webapi.main:app --host 127.0.0.1 --port 8000
curl -i -s http://127.0.0.1:8000/admin/review/items
```

Admin auth enabled with token should allow:

```bash
curl -i -s http://127.0.0.1:8000/admin/review/items \
  -H 'Authorization: Bearer local-admin-token'

curl -i -s http://127.0.0.1:8000/admin/review/items \
  -H 'X-Admin-Token: local-admin-token'
```

Product preview with `production_safe`:

```bash
curl -i -s -X POST http://127.0.0.1:8000/chat/product-preview \
  -H 'Content-Type: application/json' \
  -d '{"query":"15問に自由回答は含まれますか？","product_profile":"production_safe","apply_feedback_preview":true,"apply_feature_rerank":true}'
```

Product preview with `pilot_high_accuracy`:

```bash
curl -i -s -X POST http://127.0.0.1:8000/chat/product-preview \
  -H 'Content-Type: application/json' \
  -d '{"query":"15問に自由回答は含まれますか？","product_profile":"pilot_high_accuracy","apply_feedback_preview":true,"apply_feature_rerank":true}'
```
