# Prompt030: Durable Multi-Tenant Persistence Verification

You are working in:

/home/rai/chatbot

## Goal

Prove, with automated tests and synthetic data only, that tenant isolation and
stored data survive a persistence reload and a backup/restore cycle on a
NON-PRODUCTION collection. This is a verification and hardening prompt, not a
storage-backend swap. The current store is a single-node local Chroma
PersistentClient; the open blocker from Prompt027/029 is that no test proves
tenant isolation holds after the vector store is reloaded or restored from
backup.

Do not implement a new database backend. Do not change retrieval ranking,
guard, cross-encoder, or distance behavior. Only add verification coverage,
and at most a thin, safe helper/script if strictly necessary.

## Execution mode

Proceed autonomously.

Commit and tag automatically only if this prompt reaches PASS and the git diff
is limited to this prompt scope.

Stop only for destructive operations, user-data deletion, secrets/.env access,
remote push/deploy, production/default vectorstore mutation, required
network/model downloads, ambiguous missing targets, unsafe persistence
behavior, or unresolved verification failure after one bounded fix attempt.

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
Do not mutate the production/default vectorstore or default collection.
Do not use real customer data.
Do not push remotely.
Do not deploy externally.
No new dependencies.

## Preconditions to verify

Verify and record:

- Current branch and HEAD.
- Working tree has no unexpected tracked diff.
- Tag prompt028-chat-tenant-product-profile-runtime-wiring exists.
- Tag analysis-commercial-rag-chatbot-readiness-after-prompt028 exists.
- rag_core/store.py exposes get_vectorstore, reset_vectorstore_clients, and
  the embedding-fingerprint stamp/verify functions.
- scripts/backup.sh and scripts/restore.sh exist and the deploy-ops tests pass.
- The production/default collection name is read from config
  (VECTORSTORE_COLLECTION_NAME / CHROMA_COLLECTION); record it so the new tests
  can refuse to touch it.

## Required design

### 1. Non-production, synthetic-only fixtures

All new tests must:

- use a pytest tmp_path (or equivalent temp directory) as VECTORSTORE_DIR,
  never the repo vectorstore;
- use an explicit non-production collection name (for example
  pilot_persist_check_v1) and assert it differs from the configured
  production/default collection name;
- write only synthetic chunks with two distinct tenant ids (for example
  tenant_alpha and tenant_beta), each with at least two chunks;
- never read .env and never require a network call or model download (stub or
  use the existing local embedding path / a deterministic fake embedder as the
  existing fingerprint tests do).

### 2. Reload durability + tenant isolation

Add a test that:

- builds the synthetic two-tenant collection in the temp store;
- forces a client reload (reset_vectorstore_clients, then re-open via
  get_vectorstore) to simulate a process restart;
- asserts the stored records are still present after reload;
- asserts a tenant-filtered retrieval for tenant_alpha returns only
  tenant_alpha chunks and never tenant_beta chunks, and vice versa;
- asserts the embedding fingerprint is still present/valid after reload.

### 3. Backup/restore durability + tenant isolation

Add a test (or a thin safe script plus a test) that:

- takes a backup of the synthetic temp store using the existing backup
  mechanism patterns, into a temp backups dir;
- restores into a separate temp target (staging, non-destructive, hash
  verified);
- re-opens the restored store and re-asserts: records present, fingerprint
  valid, and tenant isolation still holds (alpha sees only alpha, beta only
  beta);
- never restores in-place over the repo data and never touches the
  production/default collection.

### 4. Optional thin helper/script

If a helper is needed, prefer a small safe script such as
scripts/persistence_isolation_check.sh that:

- is safe by default and never reads .env;
- runs only the new synthetic persistence tests;
- refuses to run against the production/default collection or the repo
  VECTORSTORE_DIR;
- exits non-zero on failure.

Do not duplicate retrieval logic; reuse existing store and retrieval
abstractions.

### 5. Observability/audit safety

If anything is logged, include only safe identifiers (tenant ids, collection
name, counts). Never include raw API keys, query text in metrics, private
document content, secrets, or .env values.

### 6. Tests

Prefer a new dedicated test file such as
tests/test_durable_multitenant_persistence.py proving:

- reload preserves stored records and the embedding fingerprint;
- tenant isolation holds after reload (alpha-only and beta-only);
- backup then restore preserves records and fingerprint;
- tenant isolation holds after restore;
- the tests use a non-production collection and temp VECTORSTORE_DIR and assert
  they are not the production/default collection;
- no raw API keys or secrets appear in any captured output.

### 7. Documentation

Update docs minimally:

- a short note in docs/operations.md (or docs/reports) describing the
  persistence reload and backup/restore isolation verification and how to run
  it;
- make clear this proves single-node durability and isolation, not a managed
  multi-tenant database.

### 8. Analysis artifact

Add a short implementation report:

- docs/reports/prompt030_durable_multitenant_persistence_verification.md

It must include: what was verified, what remains unchanged, synthetic-data and
non-production-collection safety, test evidence, and remaining production
risks (for example managed/HA persistence, concurrent writers, scale).

## Explicit non-goals

- New storage backend or managed database integration.
- Distributed or HA persistence.
- Cross-encoder promotion.
- Prompt020 execution.
- Distance threshold or guard changes.
- Tenant authorization or isolation semantic changes.
- Rate limiter semantic changes.
- Real customer data.
- External deployment or remote push.
- New dependencies.

## Verification

Run these targeted checks first:

    python -m pytest tests/test_durable_multitenant_persistence.py -q
    python -m pytest tests/test_tenant_isolation.py tests/test_embedding_fingerprint.py tests/test_deploy_ops.py -q

Then run broader safety checks:

    python -m pytest --collect-only -q
    python -m pytest tests/test_chat_tenant_product_profile_runtime.py tests/test_api_key_tenant_authorization.py -q
    scripts/product_readiness_smoke.sh
    scripts/limited_beta_preflight.sh

Then run synthetic evals:

    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt030_smoke_check.json
    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt030_qa_pair_check.json

Optional only if Docker is available and safe (document, do not run
automatically):

    scripts/limited_beta_preflight.sh --with-docker-smoke

Do not run commands that read .env.
Do not mutate the production/default vectorstore.

## Commit/tag policy

PASS:

- commit message: prompt030 durable multitenant persistence verification
- tag: prompt030-durable-multitenant-persistence-verification

PARTIAL or FAIL:

- no commit
- no tag
- report blocker and next command

## Required final output

1. Preconditions
2. Implementation summary
3. Non-production / synthetic-data safety result
4. Reload isolation result
5. Backup/restore isolation result
6. Verification results
7. Docs/report paths
8. Git diff summary
9. Commit/tag result
10. Final judgment: PASS / PARTIAL / FAIL
11. Next recommendation
