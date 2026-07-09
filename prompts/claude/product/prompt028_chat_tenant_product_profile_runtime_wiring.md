# Prompt028: Chat Tenant Product Profile Runtime Wiring

You are working in:

/home/rai/chatbot

## Goal

Wire per-tenant product profile and policy resolution into `/chat` and `/chat/stream` at runtime, behind a default-off safety flag.

Prompt027 analysis confirmed that `/chat` already wires tenant isolation and API-key tenant authorization at runtime. The remaining partial area is per-tenant product profile / policy resolution, which currently appears preview-oriented or not fully connected to the main `/chat` runtime path.

This prompt must add the runtime wiring safely, without changing default behavior.

## Execution mode

Proceed autonomously.

Commit and tag automatically only if this prompt reaches PASS and the git diff is limited to this prompt scope.

Stop only for destructive operations, user-data deletion, secrets/.env access, remote push/deploy, production vectorstore/default collection mutation, required network/model downloads, ambiguous missing targets, unsafe request-path behavior, or unresolved verification failure after one bounded fix attempt.

Do not read .env.
Do not print or infer secrets.
Do not download models.
Do not run Prompt020.
Do not change cross-encoder settings.
Do not change distance thresholds.
Do not change tenant authorization semantics.
Do not change tenant isolation semantics.
Do not change rate-limiter semantics.
Do not change the too_general guard.
Do not mutate production/default vectorstore.
Do not use real customer data.
Do not push remotely.
Do not deploy externally.
No new dependencies.

## Preconditions to verify

Verify and record:

- Current branch and HEAD.
- Tag `analysis-current-state-after-prompt026-limited-beta-pack` exists.
- Tag `prompt026-limited-beta-launch-pack` exists.
- `/chat` and `/chat/stream` already enforce API auth and tenant authorization.
- Existing tenant isolation tests pass.
- Existing product profile resolution functions exist, especially:
  - `resolve_tenant_product_profile`
  - `use_tenant_profile` or equivalent runtime/profile helper
  - production-safe profile definitions
- Existing product preview tenant profile tests exist.
- Working tree has no unexpected tracked diff.

## Required design

### 1. Default-off flag

Add a default-off configuration flag:

- `CHAT_USE_TENANT_PROFILE`
- default: false

When false, `/chat` and `/chat/stream` behavior must remain unchanged.

Do not infer this from `.env`; add config support using the existing config helper style only.

Document the flag in:

- `.env.example`
- `docs/production_readiness_checklist.md`
- relevant operations or beta docs if appropriate

### 2. Runtime profile resolution

When `CHAT_USE_TENANT_PROFILE=true`, `/chat` and `/chat/stream` must resolve the tenant product profile using the request tenant context already established by API auth / tenant authorization.

Required behavior:

- Use the already-authenticated tenant context.
- Resolve per-tenant product profile/policy using the existing product profile resolver.
- Unknown tenant must default to `production_safe` or the safest available profile.
- Profile resolution must not bypass tenant authorization.
- Profile resolution must not weaken tenant isolation.
- Profile resolution must not change query routing, guard thresholds, cross-encoder settings, or distance thresholds unless those values are already part of the resolved profile contract and tests prove default-off behavior is unchanged.
- If resolved profile is invalid, fail closed to the safest profile rather than enabling a more permissive behavior.

### 3. Main chat path integration

Integrate the resolved profile into:

- `/chat`
- `/chat/stream`

The integration must be minimal and testable.

Prefer adding a small helper such as:

- `resolve_chat_runtime_profile`
- `get_chat_product_profile`
- or similar

Use existing abstractions where possible.

Do not duplicate product preview logic if there is already a safe resolver.

### 4. Observability and audit safety

If profile selection is logged, audited, or included in metrics, include only safe profile identifiers and tenant IDs already allowed by existing audit conventions.

Do not include:

- raw API keys
- query text in metrics
- private document content
- secrets
- `.env` values

### 5. Tests

Add or update targeted tests proving:

- Default-off behavior remains unchanged for `/chat`.
- Default-off behavior remains unchanged for `/chat/stream`.
- When flag is enabled, `/chat` resolves and uses tenant-specific profile.
- When flag is enabled, `/chat/stream` resolves and uses tenant-specific profile.
- Unknown tenant defaults to production_safe or the safest available profile.
- Invalid/missing profile config fails closed to the safest available profile.
- Tenant authorization still blocks unauthorized tenants before profile-specific runtime behavior.
- Tenant isolation remains intact.
- Product preview behavior is not regressed.
- Rate limiting behavior is not changed.
- No raw API keys appear in errors, audit, metrics, or test-captured outputs.

Prefer targeted tests in existing relevant files or a new file such as:

- `tests/test_chat_tenant_product_profile_runtime.py`

### 6. Documentation

Update docs minimally:

- Explain `CHAT_USE_TENANT_PROFILE`.
- Explain default-off behavior.
- Explain recommended limited-beta setting.
- Explain safest-profile fallback.
- Explain that enabling this does not replace tenant authorization or tenant isolation.

### 7. Analysis artifact

Add a short implementation report:

- `docs/reports/prompt028_chat_tenant_product_profile_runtime_wiring.md`

It must include:

- What was wired
- What remains unchanged
- Default-off safety behavior
- Test evidence
- Remaining production risks

## Explicit non-goals

- Durable multi-tenant persistence.
- New storage backend.
- Cross-encoder promotion.
- Prompt020 execution.
- Distance threshold changes.
- too_general guard changes.
- Rate limiter semantic changes.
- Tenant authorization semantic changes.
- Real customer data.
- External deployment.
- Remote push.
- OAuth/JWT.
- Secret manager integration.
- New dependencies.

## Verification

Run these targeted checks first:

    python -m pytest tests/test_api_key_tenant_authorization.py tests/test_tenant_isolation.py -q
    python -m pytest tests/test_product_preview_tenant_profiles.py -q
    python -m pytest tests/test_chat_stream.py -q
    python -m pytest tests/test_rate_limit.py tests/test_metrics_observability.py tests/test_observability_export.py -q

If a new dedicated test file is created, run it explicitly:

    python -m pytest tests/test_chat_tenant_product_profile_runtime.py -q

Then run broader safety checks:

    python -m pytest --collect-only -q
    scripts/product_readiness_smoke.sh
    scripts/limited_beta_preflight.sh

Then run synthetic evals:

    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt028_smoke_check.json

    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt028_qa_pair_check.json

Optional only if Docker is available and safe:

    scripts/limited_beta_preflight.sh --with-docker-smoke

Do not run commands that read `.env`.
Do not mutate production/default vectorstore.

## Commit/tag policy

PASS:

- commit message: `prompt028 chat tenant product profile runtime wiring`
- tag: `prompt028-chat-tenant-product-profile-runtime-wiring`

PARTIAL or FAIL:

- no commit
- no tag
- report blocker and next command

## Required final output

1. Preconditions
2. Implementation summary
3. Default-off safety result
4. Tenant-profile runtime wiring result
5. Verification results
6. Docs/report paths
7. Git diff summary
8. Commit/tag result
9. Final judgment: PASS / PARTIAL / FAIL
10. Next recommendation
