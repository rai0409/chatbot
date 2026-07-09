# Prompt052: SLO/SLA & Incident Escalation Runbook

You are working in:

/home/rai/chatbot
## Goal

Create an honest SLO/SLA and incident escalation runbook suitable for first paid
annual contract discussions, plus customer communication templates. Docs only.
Do NOT claim 24/7 SLA or HA unless repo/operational evidence supports it - state
the current single-node, business-hours-support reality.

## Scope

- SLO definitions tied to the actual metrics (availability via /health, answer/
  error/abstain rates, latency caveats) and the per-process metrics caveat.
- A realistic SLA proposal menu (e.g. business-hours support; not 24/7 unless
  staffed) with explicit assumptions and exclusions.
- Incident severity levels, escalation path, and customer communication
  templates (placeholders only; no secrets, no real tenant names).

## Tests

Docs-only. Optional presence/lint test.

## Verification

    git status --short
    python -m pytest --collect-only -q

## Report

docs/reports/prompt052_slo_sla_incident_escalation_runbook.md


## Global safety constraints (apply to this prompt)

Do not read .env. Do not print or infer secrets. Do not use .env model names.
Do not use real customer data. Do not mutate the production/default vectorstore
or default collection except through an explicitly safe, tested staged workflow.
Do not run Docker (unless this prompt explicitly decides it is safe and necessary
for local-only validation). Do not deploy externally. Do not push remotely.
Do not weaken tenant authorization, tenant isolation, API key behavior, rate
limiting, or production_safe behavior. Do not change retrieval thresholds or
cross-encoder settings unless this prompt explicitly analyzes and justifies it
with tests. Do not expose API keys, SSO secrets, trust tokens, raw prompts, raw
document text, or tenant-private data in UI, logs, metrics, alerts, reports, or
tests. No new dependencies unless explicitly justified by this prompt. Leave
unrelated orphan files untouched (including previous market prompt/report
orphans). Preserve Prompt034 UI, Prompt035 Chroma where, Prompt036 monitoring,
and Prompt037 enterprise-auth behavior unless explicitly in this prompt's scope.

## Execution mode

Proceed autonomously. Run targeted tests first; run broader tests only when
targeted tests pass and runtime is reasonable; never fabricate test results; if
the full suite is not run, say so. Commit and tag only on PASS with a
prompt-scoped diff and no unrelated orphan changes. On FAIL/PARTIAL: no commit,
no tag; write a blocker report and stop.

## Commit/tag policy

PASS -> commit message "prompt052 slo sla incident escalation runbook", tag "prompt052-slo-sla-incident-escalation-runbook".
PARTIAL/FAIL -> no commit, no tag; report blocker and the next command.

## Required final output

1. Preconditions  2. Implementation summary  3. Safety/no-secret-exposure result
4. Verification results (targeted first; state if full suite not run)
5. Docs/report path  6. Git diff summary  7. Commit/tag result
8. Final judgment PASS/PARTIAL/FAIL  9. Next recommendation
