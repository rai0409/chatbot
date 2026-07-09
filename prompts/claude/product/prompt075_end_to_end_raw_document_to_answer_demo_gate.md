# Prompt075 End To End Raw Document To Answer Demo Gate

Proceed autonomously.
Do not ask yes/no confirmation for safe local repository reads, lightweight checks, report generation, prompt generation, commits, or tags.

## Context

Prompt071 found that KuraDen has UI surfaces, converters, dry-run validation, and CLI vectorstore import, but no browser-verified raw document to answer flow. Prompt075 should be run after upload-to-staging, staging query selection, and operator UX hardening are implemented.

## Goal

Create and verify an end-to-end demo gate proving a non-technical operator can upload synthetic Excel/Word/PDF documents, import them into a non-production staging collection, query them from `/chat-ui`, and receive grounded answers with citations.

## Scope

- Synthetic/sanitized demo data only.
- Browser/operator flow verification.
- No production/default vectorstore mutation.
- No external deployment.

## Safety constraints

- no `.env`
- no secrets
- no real customer data
- no production/default vectorstore mutation
- unrelated orphan files untouched
- do not weaken tenant authorization
- do not weaken tenant isolation
- do not weaken API key behavior
- do not weaken OIDC/session behavior
- do not weaken RBAC behavior
- do not weaken rate limiting
- do not weaken `production_safe` behavior
- do not change retrieval thresholds
- do not change cross-encoder settings
- do not expose API keys, OIDC secrets, session secrets, trust tokens, raw prompts, raw document text, tenant-private data, customer-private data, or real identity data in reports, docs, tests, prompts, artifacts, logs, UI, or generated files

## Implementation requirements

- Use synthetic manufacturing documents.
- Verify browser upload and dry-run validation.
- Verify actual import only into explicit non-production staging collection.
- Verify `/chat-ui` can query that staging collection.
- Verify citations point to synthetic source documents.
- Verify `/health` and keyword index status are documented.
- Produce a demo readiness report and machine-readable artifact.

## Tests/checks

- Targeted backend tests for the full synthetic path.
- UI or integration smoke checks where reasonable.
- JSON artifact validation.
- `pytest --collect-only -q`.
- Full suite only if reasonable for the environment.

## Required deliverables

- Demo gate report under `docs/reports/`.
- JSON artifact under `artifacts/commercial_readiness/`.
- Any required synthetic demo fixtures under existing synthetic/eval paths.
- No real customer data.

## Commit/tag expectations

Commit only if tests/checks pass and changes are scoped.

Expected commit message:
prompt075 end to end raw document to answer demo gate

Expected tag:
prompt075-end-to-end-raw-document-to-answer-demo-gate

## Final output format

- PASS/FAIL/PARTIAL
- commit hash if committed
- tag if created
- end-to-end demo result
- browser upload result
- staging import result
- chat query result
- safety checks
- tests run
- files changed
- blockers
