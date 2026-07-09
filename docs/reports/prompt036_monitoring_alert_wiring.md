# Prompt036: Monitoring & Alert Wiring

Implementation report. Adds a minimal, local-only monitoring/alert layer on top
of the existing metrics foundations so an operator can detect unhealthy pilot
behavior before a customer-facing PoC/limited beta — with no Grafana, cloud,
Docker, deployment, pager, or new dependencies.

## Files changed

- `webapi/alerting.py` (new) — pure-Python threshold evaluator over the
  `/metrics` JSON snapshot. `evaluate_alerts(payload, thresholds=None)` returns
  an overall verdict plus per-signal `OK/WARN/CRITICAL`. `DEFAULT_THRESHOLDS`
  mirrors `docs/operations.md` "Alert thresholds". No network, no secrets.
- `scripts/alert_check.py` (new) — CLI that reads a `/metrics` JSON snapshot
  (file arg or stdin), prints per-signal status, and exits `0` OK / `1` WARN /
  `2` CRITICAL (cron/CI-friendly). `--json` emits the full result.
- `webapi/main.py` — added one safe observability counter
  `chat_feedback_total` (labeled by the stable `feedback_type` enum) in
  `/chat/feedback`. No behavior change to feedback handling, response, or auth.
- `docs/operations.md` — added a "Local alert checker" subsection under the
  existing "Alert thresholds".
- `tests/test_monitoring_alerts.py` (new) — focused tests.
- `docs/reports/prompt036_monitoring_alert_wiring.md` (this report).

## Monitoring signals added or confirmed

All consume existing per-process counters from `metrics_registry` via the
`/metrics` JSON payload (confirmed present, except the new feedback counter):

1. **chat request count** — `sum(chat_answer_mode_total)` (denominator). [confirmed]
2. **chat error rate** — `chat_provider_error_total` ÷ chat requests. [confirmed]
3. **abstain/no-answer rate** — `chat_used_fallback_total` ÷ chat requests, plus
   **guard-trip rate** `sum(chat_guard_reason_total)` ÷ chat requests. [confirmed]
4. **feedback by value** — new `chat_feedback_total{feedback_type}` enables the
   human-review-rate signal. [added, safe enum label]
5. **retrieval no-answer / low-confidence** — represented via the fallback and
   guard-trip signals above (no-answer = fallback; low-confidence/too_general =
   guard trip). [confirmed via existing counters]
6. **latency** — optional `latency_p95_ms` signal, evaluated only if a snapshot
   includes that field. metrics_registry holds no latency histogram (latency
   lives in trace/audit `latency_ms`), so it is optional, not required. [optional]
7. **health/readiness compatibility** — `/health` and `/metrics` are unchanged;
   smoke/preflight still pass (the new counter is additive and only appears
   after feedback is posted). [confirmed]

Also surfaced: **429 count** (`api_rate_limited_total`) and **auth-rejection
count** (`api_auth_rejection_total`) as absolute-count signals.

## Alert conditions and thresholds (DEFAULT_THRESHOLDS in webapi/alerting.py)

- `error_rate`: WARN ≥ 0.02, CRITICAL ≥ 0.10 (matches doc provider-error rate).
- `fallback_rate`: WARN ≥ 0.30, CRITICAL ≥ 0.60 (abstain/no-answer).
- `guard_trip_rate`: WARN ≥ 0.40, CRITICAL ≥ 0.80.
- `zero_success`: CRITICAL when a non-empty chat window
  (`chat_requests + errors > 0`) has zero successful answers
  (grounded/approved_exact_match).
- `rate_limited` (429 count): WARN ≥ 1, CRITICAL ≥ 25.
- `auth_rejection` count: WARN ≥ 5, CRITICAL ≥ 50.
- `human_review_rate` (only if `chat_feedback_total` present): WARN ≥ 0.20,
  CRITICAL ≥ 0.50.
- `latency_p95_ms` (only if provided): WARN ≥ 4000, CRITICAL ≥ 10000.
- `min_requests_for_rate` = 20: rate signals report OK below this volume to
  avoid alerting on statistically meaningless windows.

Overall verdict = worst signal severity. Thresholds are starting points;
override per deployment by editing `DEFAULT_THRESHOLDS` or passing `thresholds=`.

## How to run the local checker

```bash
curl -s http://127.0.0.1:8000/metrics > snap.json
python scripts/alert_check.py snap.json          # exit 0 OK / 1 WARN / 2 CRITICAL
curl -s http://127.0.0.1:8000/metrics | python scripts/alert_check.py - --json
```

Alert definitions/thresholds: `webapi/alerting.py` (`DEFAULT_THRESHOLDS`);
operator doc: `docs/operations.md` → "Alert thresholds" → "Local alert checker".

## Tests run and results

- `tests/test_monitoring_alerts.py`: **14 passed** — healthy→OK; high error
  rate→CRITICAL; high fallback/guard→WARN/CRITICAL; zero-success non-empty
  window→CRITICAL; empty window not flagged; feedback human-review spike;
  429/auth-rejection counts; **no secrets/keys/prompts/.env in output**; CLI
  exit codes; `/metrics` shape unchanged + evaluator consumes live payload;
  feedback counter increments with a stable label and leaks no token.
- Regression: `test_metrics_observability.py`, `test_observability_export.py`,
  `test_enduser_chat_ui.py` (Prompt034), `test_chroma_where_builder.py` +
  `test_tenant_isolation.py` (Prompt035), `test_production_readiness_report.py`:
  **59 passed**.
- Full suite: **749 passed, 0 failed** (collect-only 749; +14 new).
- `scripts/product_readiness_smoke.sh`: exit 0 (117 passed).
- `scripts/limited_beta_preflight.sh`: exit 0 (PREFLIGHT OK).

## Confirmation of non-changes

Not changed or accessed:

- `.env` not read; no secrets printed/inferred; no `.env` model names; no real
  customer data; tests use synthetic snapshots only.
- No vectorstore mutation; no Docker; no deploy; no remote push.
- Tenant **authorization** and tenant **isolation** semantics unchanged.
- API key semantics unchanged; rate-limiter semantics unchanged (only a
  pre-existing aggregate counter is read by the checker — no new enforcement).
- `production_safe` behavior, retrieval/distance thresholds, and cross-encoder
  settings unchanged.
- **Prompt034** chat UI behavior unchanged (verified by `test_enduser_chat_ui.py`).
- **Prompt035** Chroma `$and` where behavior unchanged (verified by
  `test_chroma_where_builder.py` + `test_tenant_isolation.py`).
- `/health` and `/metrics` endpoints unchanged (the feedback counter is an
  additive `metrics_registry` increment; the `/metrics` payload shape is the
  same). No new dependencies.
