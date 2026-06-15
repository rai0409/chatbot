# Prompt074 Operator Ingestion Promotion Chat UI UX Hardening

Proceed autonomously.
Do not ask yes/no confirmation for safe local repository reads, lightweight checks, report generation, prompt generation, commits, or tags.

## Context

After raw upload-to-staging and staging query selection exist, KuraDen still needs browser/operator hardening so a non-technical operator can understand conversion, validation, import, staging query, and safe promotion state without using CLI scripts.

## Goal

Harden the operator UX across admin ingestion, staging query, and promotion planning so the workflow is understandable, safe, and commercially demo-ready.

## Scope

- Improve `/admin/ingestion` status and summary UX.
- Add safe promotion planning visibility if backend planning exists.
- Improve `/chat-ui` staging mode clarity.
- Keep all production/default mutation prohibited.

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

- Show clear stage states: uploaded, converted, validated, imported to staging, query-ready, promotion-planned.
- Show safe counts only: files, chunks, source types, issue counts, collection names.
- Do not show raw document text.
- Provide actionable validation errors without private content.
- Make staging mode visually distinct in `/chat-ui`.
- Add UI copy that production/default collection mutation is refused.
- Keep admin gating for privileged flows.

## Tests/checks

- UI tests for stage labels and controls.
- Backend tests for safe summaries.
- Tests that raw content and secrets are not present in responses.
- Tests that promotion planning does not mutate vectorstore.
- Run targeted tests plus `pytest --collect-only -q`.

## Required deliverables

- Source/UI changes.
- Tests.
- Report under `docs/reports/`.
- JSON artifact under `artifacts/commercial_readiness/`.

## Commit/tag expectations

Commit only if tests/checks pass and changes are scoped.

Expected commit message:
prompt074 operator ingestion promotion chat ui ux hardening

Expected tag:
prompt074-operator-ingestion-promotion-chat-ui-ux-hardening

## Final output format

- PASS/FAIL/PARTIAL
- commit hash if committed
- tag if created
- UX changes
- safety checks
- tests run
- files changed
- blockers
