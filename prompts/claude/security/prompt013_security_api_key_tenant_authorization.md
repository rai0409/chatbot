# Prompt013 Security: API Key To Tenant Authorization Mapping

You are working in:

/home/rai/chatbot

## Goal

Implement the next commercial blocker: API key to tenant_id authorization mapping.

Prompt011 added retrieval-layer tenant isolation, but tenant_id is still a client-supplied request field. Any caller with a valid API key can currently claim any tenant_id. This is not safe for multi-tenant commercial use.

Implement server-side enforcement so each API key can access only its allowed tenant_id values.

## Execution mode

Proceed autonomously.

Do not ask for human confirmation for ordinary local edits, targeted tests, smoke checks, or local verification.

Stop only if one of the following occurs:

- A destructive operation would be required.
- User data would be deleted.
- .env, secrets, tokens, API keys, or private credentials would need to be read, printed, changed, or inferred.
- A remote push, force push, remote deployment, or external service login would be required.
- The target files cannot be found and the correct location is ambiguous.
- Verification fails in a way that cannot be safely classified after one bounded fix attempt.

If verification fails because of your changes, perform one bounded fix attempt, rerun targeted verification, and report final status.

## Preconditions to verify before implementing

Confirm:

- Repo path is /home/rai/chatbot
- Current branch
- git status summary
- Prompt012 is complete:
  - tag prompt012-phase4b-deployment-packaging exists
  - Dockerfile / docker-compose.yml / .env.example / CI workflow exist
- Prompt011 tenant isolation is complete:
  - tenant filtering exists in rag_core/retrieval.py
  - ChatRequest has tenant_id
  - answer_cache key includes tenant_id
- API auth exists:
  - webapi/api_auth.py exists
  - require_api_auth is wired into /chat and /chat/stream

Do not read or print .env.

## Scope

Implement only API-key-to-tenant authorization.

### 1. Configuration

Add support for an optional env/config value:

API_AUTH_TENANT_MAP

Format:

key1=tenant_a,key2=tenant_b,key3=tenant_a|tenant_b

Rules:

- API_AUTH_TENANT_MAP is optional.
- If API_AUTH_ENABLED=false, current behavior remains unchanged.
- If API_AUTH_ENABLED=true and API_AUTH_TENANT_MAP is unset or empty, preserve current behavior for backward compatibility: any valid API key may access requested tenant_id.
- If API_AUTH_ENABLED=true and API_AUTH_TENANT_MAP is set, enforce it strictly.
- A key mapped to tenant_a may access only tenant_a.
- A key mapped to tenant_a|tenant_b may access tenant_a or tenant_b.
- A key not present in API_AUTH_TENANT_MAP should fail closed with 403 when tenant map enforcement is active.
- Empty tenant values are invalid and should be ignored or treated as misconfigured safely.
- Do not log or echo real API keys.

Use only standard library. No new dependencies.

### 2. API auth behavior

Extend webapi/api_auth.py in a minimal way.

The auth layer should expose enough request context for endpoints to know the authenticated API key identity safely, without leaking the key.

Preferred approach:

- Return an auth context object from the API auth dependency, containing:
  - authenticated: bool
  - tenant_authorization_enabled: bool
  - allowed_tenants: set[str] or equivalent
  - a redacted key id/fingerprint only if useful for audit
- Do not put raw API keys into request state, response payloads, logs, traces, or metrics.
- Keep existing API_AUTH_ENABLED / API_AUTH_KEYS behavior compatible.

### 3. Tenant enforcement

Apply tenant authorization to endpoints that accept or use tenant_id:

- POST /chat
- POST /chat/stream
- /search or /search/debug only if they accept tenant_id or thread tenant_id into retrieval
- any product preview endpoint only if it accepts tenant_id

Rules:

- Normalize tenant_id the same way Prompt011 does: missing, empty, or whitespace-only means "default".
- If tenant authorization is disabled, behavior remains unchanged.
- If tenant authorization is enabled and requested tenant_id is not allowed, return 403.
- Do not silently rewrite unauthorized tenant_id to default.
- Do not treat tenant_id as authentication by itself.
- Do not change response field sets for successful responses.

### 4. .env.example and docs

Update .env.example with placeholder documentation only:

API_AUTH_TENANT_MAP=REPLACE_API_KEY_1=default

Do not read the real .env.

Update docs/production_readiness_checklist.md or README with a short note:

- tenant_id is now enforced when API_AUTH_TENANT_MAP is configured
- without API_AUTH_TENANT_MAP, legacy behavior is preserved
- API keys must be rotated if leaked
- do not commit real keys

### 5. Tests

Add targeted tests only.

Cover:

- API_AUTH_ENABLED=false: tenant map ignored, existing behavior unchanged
- API_AUTH_ENABLED=true with valid API key and no tenant map: current behavior preserved
- API_AUTH_ENABLED=true with tenant map:
  - key for tenant_a can access tenant_a
  - key for tenant_a cannot access tenant_b
  - key for tenant_a|tenant_b can access both
  - unmapped valid key fails closed when map enforcement is active
  - missing/blank tenant_id normalizes to default and is checked against default permission
- /chat enforces tenant authorization before pipeline execution
- /chat/stream enforces tenant authorization before streaming starts
- unauthorized tenant returns 403 and does not call retrieval/LLM pipeline
- raw API keys do not appear in error details, logs, metrics, or response payloads

Use fake clients / monkeypatching. No network. No OpenAI API key.

## Explicit non-goals

Do not implement:

- tenant management endpoints
- database-backed key storage
- per-tenant quotas
- billing
- user accounts
- OAuth
- JWT
- new dependencies
- rate limiting
- changes to retrieval tenant filtering except where necessary to pass tenant_id through safely
- changes to answer quality, rerank, citations, cache semantics, streaming protocol, Docker, or CI beyond tests/docs/env template

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

Run the deterministic eval smoke:

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt013_security_tenant_auth_smoke_check.json

Run product readiness smoke if safe:

scripts/product_readiness_smoke.sh

Do not run full test suite unless targeted verification clearly requires it.

## Required final output

Report in this exact order:

1. Preconditions
   - repo path
   - branch
   - initial git status summary
   - Prompt012 complete evidence
   - Prompt011 tenant isolation evidence
   - API auth evidence

2. Implementation summary
   - files changed
   - exact tenant authorization behavior
   - backward compatibility behavior
   - explicit non-goals preserved

3. Verification results
   - targeted tests
   - collect-only
   - eval smoke
   - product readiness smoke
   - skipped checks and why

4. Git diff summary
   - git diff --stat
   - confirm no large unrelated diffs

5. Final judgment
   - PASS / PARTIAL / FAIL
   - whether it is safe to continue to accuracy roadmap

6. Next prompt file
   - If PASS, write exactly one next recommended prompt to:
     prompts/claude/prompt014_phase5a_cross_encoder_rerank.md
   - That prompt should adapt the existing prompt013_phase5a_cross_encoder_rerank.md plan if present, but do not execute it.
   - Do not delete the existing prompt013_phase5a_cross_encoder_rerank.md.
