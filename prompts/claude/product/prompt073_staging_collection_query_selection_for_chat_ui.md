# Prompt073 Staging Collection Query Selection For Chat UI

Proceed autonomously.
Do not ask yes/no confirmation for safe local repository reads, lightweight checks, report generation, prompt generation, commits, or tags.

## Context

Prompt071 found that `/chat-ui` calls `/chat/stream`, and retrieval uses `store.get_vectorstore()` with configured default collection selection. There is no browser collection selector or staging query mode, so a newly ingested staging collection cannot be queried from `/chat-ui` without runtime configuration changes.

## Goal

Add a safe operator/developer path for querying an explicit non-production staging collection from `/chat-ui` without changing production/default behavior.

## Scope

- Add backend request support for optional staging collection selection where safe.
- Add browser UI control visible only for authorized/admin/operator context if appropriate.
- Preserve default `/chat` and `/chat/stream` behavior when no staging collection is provided.
- Ensure production/default collection names cannot be selected through staging mode.

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

- Design minimal request schema addition for staging collection selection.
- Ensure collection selection is explicit and non-production-only.
- Keep tenant authorization enforced before retrieval.
- Ensure default collection behavior is unchanged when the field is absent.
- Thread collection selection into retrieval without using global mutable state.
- Update `/chat-ui` with a staging collection field or admin-only control.
- Clearly label staging mode in UI responses.
- Add audit metadata with safe collection identifier only.

## Tests/checks

- Tests proving default `/chat` and `/chat/stream` behavior unchanged.
- Tests proving unauthorized tenant is blocked before staging retrieval.
- Tests proving production/default collection names are refused.
- Tests proving non-production staging collection reaches `store.get_vectorstore(collection_name=...)`.
- UI tests for field presence and no hardcoded secrets.
- Run targeted tests plus `pytest --collect-only -q`.

## Required deliverables

- Source changes.
- Tests.
- Report under `docs/reports/`.
- JSON artifact under `artifacts/commercial_readiness/`.

## Commit/tag expectations

Commit only if tests/checks pass and changes are scoped.

Expected commit message:
prompt073 staging collection query selection for chat ui

Expected tag:
prompt073-staging-collection-query-selection-for-chat-ui

## Final output format

- PASS/FAIL/PARTIAL
- commit hash if committed
- tag if created
- query selection behavior
- safety checks
- tests run
- files changed
- blockers
