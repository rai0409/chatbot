# Prompt041: Commercial Chat Workspace UI

Implementation report. Upgrades the minimal end-user page into a commercial-grade,
ChatGPT/Claude-style chat workspace served at `GET /chat-ui`, reusing the
existing backend APIs only. Frontend-only change; no backend/auth/runtime
semantics changed; no new dependencies.

## Files changed

- `webapi/static/chat.html` — rewritten as a 3-column workspace: left **sidebar**
  (brand + "新しい会話" + conversation-history shell for Prompt042), main **chat
  column** (messages + composer), right **citations/sources panel**. Vanilla
  HTML/CSS/JS; responsive (panels collapse < 1000px). SSE-over-`fetch` parsing
  of the real contract (`meta`/`delta`/`final`/`approved`/`error`), abstain/
  no-answer banner, and good/bad/human-review feedback (per-question UUID used
  as `trace_id` + `feedback_token`) are preserved from the proven client.
- `tests/test_commercial_chat_workspace_ui.py` (new) — workspace tests.
- `docs/reports/prompt041_commercial_chat_workspace_ui.md` (this report).

The `GET /chat-ui` route (`webapi/main.py`) is unchanged — it still serves the
static shell; no backend endpoints were added or modified.

## What remained unchanged

- `/chat/stream`, `/chat/feedback`, auth, tenant authorization, tenant
  isolation, rate limiting, `production_safe`, retrieval/guard/cross-encoder.
- Prompt034 contract markers preserved (`id="q"`, `id="send"`, `/chat/stream`,
  `/chat/feedback`, `X-Api-Key`, no hardcoded key) so the existing UI tests pass.

## Safety / no-secret-exposure result

No API key hardcoded; runtime key entry held in memory and sent as `X-Api-Key`;
keyless default works when auth is off; network errors render generic key-free
text (401/403/429 mapped to calm messages); defensive HTML escaping on rendered
answer/citation text. Verified by tests (no `key-a`/`key-b`, no secret patterns,
header name only).

## Verification results

- `tests/test_commercial_chat_workspace_ui.py` + `tests/test_enduser_chat_ui.py`:
  **13 passed**.
- Full suite: **766 passed, 0 failed** (collect-only 766; +5 new).
- `scripts/product_readiness_smoke.sh`: exit 0 (117). `scripts/limited_beta_preflight.sh`:
  exit 0 (PREFLIGHT OK). Full suite WAS run.

## Remaining gaps (next stages)

- Conversation history persistence + sidebar wiring → Prompt042.
- Admin console / role-aware UI / branding config → Prompt043.
- Document ingestion UI + job status → Prompt044.
