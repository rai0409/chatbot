# Prompt028: Chat Tenant Product Profile Runtime Wiring

Implementation report. Wires per-tenant product-profile resolution into the
main `/chat` and `/chat/stream` runtime paths behind a default-off flag, with
byte-for-byte unchanged default behavior.

## What was wired

- **New config flag** `CHAT_USE_TENANT_PROFILE` (`config.py`, default `false`,
  via the existing `_get_bool` helper).
- **New resolver helper** `resolve_chat_runtime_profile(tenant_id, *, customer_id=None)`
  in `webapi/main.py`:
  - returns `None` when the flag is off (chat path runs unchanged);
  - when on, resolves the per-tenant profile via the existing
    `resolve_tenant_product_profile` using the already-authenticated tenant
    context, loads the resolved product profile, and returns a compact
    descriptor `{profile_name, max_candidates_internal, decision}`;
  - **fails closed to `production_safe`** on any problem: unknown/disabled/
    rejected tenant, resolver exception, unknown/invalid profile name (a name
    that `load_product_profile` silently falls back on is treated as invalid),
    or load error;
  - forces `answer_policy.allow_similar_auto_answer = False` on the resolved
    policy.
- **Two small apply helpers** in `webapi/main.py`:
  - `_apply_chat_profile_top_k` clamps the effective `top_k` to the resolved
    profile's `limits.max_candidates_internal` (the only retrieval-affecting
    profile-contract value applied);
  - `_record_chat_profile_metric` increments `chat_tenant_profile_total`
    labeled by the safe profile name;
  - `_annotate_chat_profile` adds `product_profile` and
    `tenant_profile_decision` (safe identifiers) to the chat audit event.
- **Integration** in `/chat` and `/chat/stream`: resolve after the
  approved-exact-match early return and after tenant authorization, clamp
  `top_k`, record the metric, and annotate the audit event. Both endpoints
  share the same helpers.

## What remains unchanged

- Default behavior with `CHAT_USE_TENANT_PROFILE=false`: `top_k` is not
  clamped, no `product_profile`/`tenant_profile_decision` keys are added to
  audit events, and the `chat_tenant_profile_total` metric is never emitted.
- API auth, tenant authorization (`enforce_tenant_authorization` runs before
  resolution), tenant isolation (the authorized `tenant_id` is threaded to the
  pipeline exactly as before), rate limiting, the confidence/`too_general`
  guard, cross-encoder settings, distance thresholds, and query routing are
  untouched.
- `/chat/product-preview` and its tenant-profile path are unchanged (the
  preview `_product_policy_context` was not modified).
- No new dependencies; no production/default vectorstore mutation.

## Default-off safety behavior

- The flag is read via `getattr(config, "CHAT_USE_TENANT_PROFILE", False)` at
  call time; when false the helper short-circuits to `None`.
- Tests assert default-off `/chat` and `/chat/stream` pass `top_k` through
  unchanged and add no profile audit keys or metrics.

## Safest-profile fallback

`production_safe` is the fail-closed target for every non-clean resolution:
unknown tenant (default mapping policy), disabled/rejected tenant, resolver
exception, unknown profile name, or profile load/validation failure. The
resolver never escalates to a more permissive profile.

## Test evidence

New file `tests/test_chat_tenant_product_profile_runtime.py` (14 tests, all
passing):

- resolver unit tests: flag-off returns `None`; default tenant →
  `production_safe` (limit 8); unknown tenant → `production_safe`; invalid
  profile name → `production_safe` with decision `invalid_profile_fallback_safe`;
  resolver exception → `production_safe`.
- `/chat` and `/chat/stream` default-off unchanged (top_k passthrough, no
  audit keys, no metric).
- `/chat` and `/chat/stream` flag-on resolve `production_safe`, clamp `top_k`
  20→8, emit `chat_tenant_profile_total{production_safe}`, and annotate audit.
- flag-on unknown tenant uses the safe profile; flag-on invalid profile fails
  closed.
- tenant authorization blocks an unauthorized tenant (403) before any profile
  runtime work, with no profile metric and no raw key in the response.
- authorized tenant id is threaded to the pipeline (isolation intact).
- no raw API key appears in audit, metrics, or response with the flag on.

Regression suites run green (see commit verification): tenant authorization,
tenant isolation, product-preview tenant profiles, chat stream, rate limit,
metrics, observability export, full collection, product readiness smoke,
limited-beta preflight, and the synthetic smoke/qa_pair evals.

## Remaining production risks

- This wires per-tenant **profile selection and candidate-limit application**;
  it does not add durable multi-tenant persistence (single-node local Chroma
  remains) — still a general-production blocker.
- Profile effect on `/chat` is currently the candidate-limit clamp plus
  audit/metric annotation; richer per-tenant policy effects (e.g. answer
  routing) remain a future step and are intentionally out of scope here to
  avoid touching guard/cross-encoder/distance behavior.
- Cross-encoder rerank promotion remains parked (model not cached).
- Enabling the flag is a deployment decision; for limited beta it should be
  paired with a complete `API_AUTH_TENANT_MAP`.
