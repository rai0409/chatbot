# Prompt034: Minimal End-User Chat UI for On-Prem Manufacturing Pilot

Implementation report. Adds a minimal, brandable end-user chat UI served by the
existing backend, reusing `/chat/stream` and `/chat/feedback`. No retrieval,
answering, auth, tenant, guard, rate-limit, or `production_safe` behavior was
changed; no new dependencies.

## What was added

- **`webapi/static/chat.html`** — a single self-contained page (vanilla
  HTML/CSS/JS, no CDN, no build). It:
  - presents a question input and a conversation view;
  - calls `POST /chat/stream` and parses the real SSE contract
    (`meta` / `delta` token stream / `final` / `approved` / `error`) via a
    `fetch()` streaming reader (EventSource cannot POST);
  - renders the authoritative answer from `final`/`approved`, plus **citations**
    (`source_doc` + `source_pages`) when present;
  - shows a calm **abstain/no-answer** banner when `guard_reason` or
    `used_fallback` is set or the answer text is empty — never fabricates;
  - offers **good / bad / human-review** feedback that calls
    `POST /chat/feedback`;
  - escapes rendered text defensively.
- **`GET /chat-ui`** route in `webapi/main.py` serving the static page, mirroring
  the existing `/product-preview` page handler. `/health` and `/metrics` are
  untouched.

## What remained unchanged

- `/chat`, `/chat/stream`, `/chat/feedback` logic, models, and their
  `require_api_auth_rate_limited` + `enforce_tenant_authorization` enforcement.
- The `too_general` guard, cross-encoder settings, distance thresholds, tenant
  authorization/isolation, rate-limiter semantics, and the `production_safe`
  profile. The UI is a thin client over existing contracts.
- No backend response shape changed. Because `/chat/stream` does not surface a
  `feedback_token`, the UI **mints a per-question UUID** and uses it as both the
  request `trace_id` and the `feedback_token` — entirely within existing request
  fields, requiring no backend change.

## Default-off / no-secret-exposure safety

- **No API key is hardcoded** in `chat.html`. When API auth is enabled the
  operator enters the pilot key at runtime; it is held in a JS variable only
  (not persisted to the page) and sent as the existing `X-Api-Key` header.
- With API auth disabled the page works with no key (unchanged default).
- Network errors render generic, key-free messages (401/403/429/other mapped to
  calm text); request headers/keys are never echoed to the DOM.
- The `/chat-ui` route serves a static shell only and **does not bypass** auth or
  tenant authorization on the data endpoints.

## Test evidence

`tests/test_enduser_chat_ui.py` (8 tests, all passing):

- `/chat-ui` returns 200 HTML wired to `/chat/stream` and `/chat/feedback`;
- served HTML contains no configured key (`key-a`/`key-b`) and no secret-like
  token; references only the `X-Api-Key` header name;
- with API auth enabled, `/chat/stream` still returns 401 without a key and 403
  for an unauthorized tenant, and the stream pipeline is never invoked;
- `/chat/feedback` stays protected under auth and accepts the UI's
  client-token shape (good/bad/human_review_requested) when open;
- serving `/chat-ui` invokes no pipeline and emits no metrics
  (`production_safe`/answering untouched);
- `/health` and `/metrics` unaffected.

Regression suites remained green (chat stream, API auth, tenant authorization,
rate limit, full collection, product readiness smoke, limited-beta preflight,
smoke/qa_pair evals).

## Remaining gaps (out of scope here)

- **SSO/AD** integration (separate prompt) — pilot uses the existing API key
  model only.
- **Admin/management console** for non-engineers (tenant/key/document
  management) — not built.
- **Mobile/field-optimized UI** — current page is responsive but not a native
  app.
- The `_build_base_where` `$and` fix (Prompt030 finding) and monitoring/alert
  wiring remain separate, sell-blocking-but-distinct items per the roadmap.
