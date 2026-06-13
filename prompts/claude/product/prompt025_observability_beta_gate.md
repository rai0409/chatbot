# Prompt025: Observability & Beta Gate (B6)

You are working in:

/home/rai/chatbot

## Goal

The observability batch (B6 from docs/reports/current_state_chatbot_direction_autonomous_plan.md): machine-readable metrics export, documented alert thresholds, readiness report regeneration, and an explicit beta go/no-go assessment — the final gate before a limited external beta.

## Execution mode

Proceed autonomously. Commit and tag automatically only on PASS with a prompt-scoped diff.

Stop only for: destructive operations, user-data deletion, secrets/.env access, remote push/deploy, production vectorstore/default collection mutation, required network/model downloads, ambiguous missing targets, or unresolved verification failure after one bounded fix attempt.

Do not read .env. Do not change cross-encoder settings, distance thresholds, tenant authorization semantics, the too_general guard, or rate-limiter semantics. No new dependencies (stdlib only).

## Preconditions to verify before implementing

- Prompt024 complete: tag prompt024-security-ops exists; webapi/rate_limit.py and docs/security_operations.md present; tests/test_rate_limit.py passes.
- Metrics layer present: webapi/metrics_registry.py and GET /metrics; tests/test_metrics_observability.py passes.
- Readiness report generator present: eval/production_readiness_report.py; tests/test_production_readiness_report.py passes.

## Scope

1. Metrics export hardening (stdlib only):

- Extend GET /metrics with a Prometheus-compatible text exposition option (e.g. ?format=prometheus or Accept-based) generated from the existing counters — no new collector dependencies, JSON output unchanged by default.
- Add counters that operations needs and the registry does not yet record: rate-limited request count (429s) and auth rejection count (401/403), labeled by stable enum values only — never raw keys, fingerprints in metrics only if already established as safe, never query text.
- Document the per-process caveat in the exposition output docs.

2. Alert thresholds (docs/operations.md or docs/security_operations.md section):

- documented starting thresholds for: provider error rate, fallback rate, guard-trip rate, 429 rate, auth-rejection rate, and /health failures — with rationale and the metric/counter each maps to
- explicit guidance that thresholds are starting points to tune per deployment

3. Readiness report regeneration:

- regenerate artifacts/readiness/production_readiness_report.{json,md} with eval/production_readiness_report.py
- if the generator's static checks are missing any of the shipped security-ops items (auth, tenant map, rate limiting, runbook docs), extend its checks minimally to observe them

4. Beta go/no-go assessment (docs/reports/):

- a short written assessment: which readiness blockers remain open, which closed in B1–B6, and a recommendation (go / no-go / go-with-conditions) for a limited external beta with named conditions

5. Targeted tests only:

- prometheus exposition format correctness (content type, counter lines, no raw keys/query text)
- new counters increment on 429 and 401/403 paths
- readiness report checks observe the security-ops items

## Explicit non-goals

Grafana/dashboard provisioning, alertmanager configs, distributed tracing, log shipping, new dependencies, UI changes, actual beta exposure or deployment.

## Verification

Targeted tests first, then:

python -m pytest tests/test_metrics_observability.py tests/test_rate_limit.py tests/test_api_auth.py tests/test_production_readiness_report.py -q

python -m pytest --collect-only -q

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt025_smoke_check.json

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt025_qa_pair_check.json

scripts/product_readiness_smoke.sh if safe.

## Commit/tag policy

PASS: commit "prompt025 observability beta gate", tag prompt025-observability-beta-gate. PARTIAL/FAIL: no commit, no tag, report blocker and next command.

## Required final output

1. Preconditions
2. Implementation summary (exposition format, new counters, thresholds, report deltas)
3. Verification results
4. Beta go/no-go recommendation with conditions
5. Git diff summary
6. Commit/tag result
7. Final judgment: PASS / PARTIAL / FAIL
