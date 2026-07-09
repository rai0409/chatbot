# Prompt042: Conversation History & Thread Persistence

Implementation report. Adds a small, default-off, per-(tenant, identity)-isolated
local conversation-history boundary. Does not reuse the vector store; stores no
secrets/keys; never broadens tenant access.

## Files changed

- `webapi/conversation_store.py` (new) — JSONL store under
  `RUNS_DIR/conversations/<tenant>/<identity>/<thread_id>.jsonl`. Isolation is
  structural (tenant + identity fingerprint are path segments); all reads are
  scoped to the caller. API: `append_turn`, `get_thread`, `list_threads`,
  `delete_thread`, `purge` (retention by max-count + max-age); path segments are
  sanitized (traversal-safe); text is length-capped. `conversation_history_enabled()`
  gates the runtime surface (env `CONVERSATION_HISTORY_ENABLED`, default false).
- `webapi/main.py` — four **default-off** endpoints (`GET /chat/threads`,
  `GET /chat/threads/{id}`, `POST /chat/threads`, `DELETE /chat/threads/{id}`),
  each returning 404 when disabled, behind `require_api_auth_rate_limited` +
  `enforce_tenant_authorization`, scoped to the caller's
  `key_fingerprint` (or `anonymous`). No change to existing endpoints.
- `tests/test_conversation_history.py` (new).
- `docs/reports/prompt042_conversation_history_and_thread_persistence.md`.

(The Prompt041 sidebar shell already shows a session-scoped placeholder; live
history wiring can consume these default-off endpoints without changing the
default UI, which keeps the placeholder when the feature is off / empty.)

## Safety / no-secret-exposure result

- Default-off: with `CONVERSATION_HISTORY_ENABLED` unset, the endpoints 404 and
  the chat path is unchanged. Tenant authorization + isolation are enforced
  before any read/write; cross-tenant and cross-identity reads are denied
  (verified). Only the caller's own question/answer text + safe metadata is
  stored — no API keys, SSO secrets, trust tokens, or other tenants' data.
  Identity is the sha256 fingerprint, never a raw key. Path segments sanitized
  (traversal test included).

## Verification results

- `tests/test_conversation_history.py` + `test_tenant_isolation.py` +
  `test_api_key_tenant_authorization.py`: **39 passed**.
- Full suite: **778 passed, 0 failed** (+12). `limited_beta_preflight.sh` exit 0
  (PREFLIGHT OK). Full suite WAS run.

## Git diff summary

New `webapi/conversation_store.py`, new `tests/test_conversation_history.py`,
`webapi/main.py` (+1 import, +4 default-off endpoints + 2 helpers + 1 model),
new report. No change to retrieval/guard/auth/tenant/rate-limit/production_safe
or Prompt034/035/036/037 behavior. No new dependencies.

## Final judgment: PASS

## Next recommendation

Prompt043 — admin console, role-aware UI, and branding (backend-enforced).
