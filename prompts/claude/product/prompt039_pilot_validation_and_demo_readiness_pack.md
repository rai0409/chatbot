# Prompt039: Pilot Validation & Demo Readiness Pack (Synthetic Manufacturing Procedure Docs)

You are working in:

/home/rai/chatbot

## Goal

Produce a repeatable, evidence-backed pilot validation + demo pack for the
first commercial target — a one-department manufacturing PoC of 蔵伝 / KuraDen —
using SYNTHETIC manufacturing procedure documents only. This turns the current
"READY WITH CONDITIONS" judgment (Prompt038) into a measured position by adding
(a) a synthetic manufacturing corpus, (b) a measured answer/abstain/error eval,
and (c) a reproducible demo script for the existing `/chat-ui`.

This is an additive data + evaluation + docs prompt. It must not change product
runtime behavior, retrieval/guard/auth/rate-limit semantics, or the chat UI.

## Execution mode

Proceed autonomously. Commit and tag automatically only on PASS with a
prompt-scoped diff.

Stop only for: destructive operations, user-data deletion, secrets/.env access,
remote push/deploy, production/default vectorstore mutation, required
network/model downloads, ambiguous missing targets, unsafe behavior, or
unresolved verification failure after one bounded fix attempt.

Do not read .env.
Do not print or infer secrets.
Do not use .env model names.
Do not download models.
Do not run Prompt020.
Do not use real customer data — synthetic, clearly-fictional content only.
Do not mutate the production/default vectorstore or default collection.
Do not change cross-encoder settings.
Do not change distance thresholds.
Do not change tenant authorization semantics.
Do not change tenant isolation semantics.
Do not change API key semantics.
Do not change rate-limiter semantics.
Do not change the too_general guard.
Do not change production_safe behavior.
Do not change Prompt034 chat UI behavior, Prompt035 Chroma where behavior,
Prompt036 monitoring/alert behavior, or Prompt037 enterprise auth behavior.
Do not run Docker. Do not deploy. Do not push remotely.
No new dependencies.
Leave unrelated orphan files untouched, including previous market prompt/report orphans.

## Preconditions to verify

Verify and record:

- Current branch and HEAD; working tree has no unexpected tracked diff.
- Tag prompt037-simple-enterprise-auth-bridge exists.
- Tag prompt038-commercial-chatbot-current-state-gap-analysis exists.
- The eval runner exists and is invokable: eval/runner.py with the
  --cases/--chunks-jsonl/--output interface used by existing smoke/qa_pair evals.
- Existing eval case format under eval/cases/ (smoke_cases.jsonl,
  smoke_chunks.jsonl, qa_pair_cases.jsonl, multiformat_cases.jsonl) so the new
  cases match the established schema.
- The existing synthetic sample docs under eval/cases/sample_docs/ and the
  generator scripts/generate_sample_docs.py for the multiformat onboarding path.

## Required design

### 1. Synthetic manufacturing corpus (clearly fictional)

Add a synthetic, clearly-fictional manufacturing document set under a NEW,
clearly-labeled non-production path (for example eval/cases/manufacturing_pilot/),
covering the pilot use cases from Prompt033/038:

- a procedure / 作業手順書 (operation/changeover steps)
- a quality / 外観検査基準 (inspection pass-fail criteria)
- a safety / 安全規程 (PPE / lockout rules)
- a troubleshooting / 過去トラブル事例 (alarm -> cause -> action)
- an internal helpdesk / IT 申請 FAQ

Use clearly fictional company/product names (for example 「架空精機」「装置X」).
Prefer reusing the existing canonical chunk JSONL format and, where helpful,
scripts/generate_sample_docs.py patterns. Do NOT ingest into the production or
default collection; eval runs operate on the provided --chunks-jsonl, not the
live store.

### 2. Measured evaluation (answer / abstain / error)

Add eval cases (in the existing eval/cases schema) that exercise, at minimum:

- citation answers (grounded with source)
- an approved-Q&A exact-match answer
- at least one deliberately too-general question that MUST abstain
- at least one out-of-corpus question that MUST return no-answer
- representative procedure / quality / safety / troubleshooting questions

Run the eval via the existing runner and capture a JSON result under
runs/eval/ (runs/ is gitignored — do not commit run outputs; summarize numbers
in the report). Report first-answer rate, abstain rate, and no-answer/expected
behavior counts. Do not fabricate numbers — run the eval and quote it; if a case
fails, report it honestly and either fix the case data or document the gap.

### 3. Reproducible demo script

Add a demo script document (for example
docs/reports/pilot_demo_script_manufacturing.md) that an operator can follow
against the existing /chat-ui with the synthetic corpus, including:

- the exact synthetic documents to load and how (dry-run onboarding into an
  explicit NON-production collection only)
- 5 demo questions and expected response shapes, including one too-general
  (abstain) and one no-answer case and one approved-exact-match case
- how to show citations, the abstain message, and feedback controls
- how to explain on-prem/no-cloud and "never wrong" safety
- explicit reminder: synthetic data only; no real customer documents in a demo

### 4. Optional safe helper

If useful, add a small safe generator/check script (no new deps, no network, no
.env, no Docker) that builds or validates the synthetic corpus and is runnable
locally. Keep it minimal and tested.

### 5. Tests

Add focused tests proving:

- the synthetic manufacturing chunks JSONL is well-formed and loads with the
  existing case/chunk loader
- the eval cases parse and run through the existing runner on the synthetic
  chunks (a small, fast subset is acceptable) with the expected
  abstain/no-answer behavior on the designated cases
- no real-customer-data markers and no secret-like tokens appear in the
  synthetic corpus, eval cases, or demo script
- existing suites remain green: Prompt034 UI, Prompt035 Chroma where, Prompt036
  monitoring, Prompt037 enterprise auth, tenant isolation

### 6. Report

Add docs/reports/prompt039_pilot_validation_and_demo_readiness_pack.md with:
files added, the synthetic corpus description, measured eval numbers (quoted
from the actual run), demo script location, what is now safe to claim vs still
not, and confirmation that no runtime behavior / semantics listed above changed.

## Explicit non-goals

- Real customer data or real manufacturing documents.
- Any change to retrieval/guard/auth/rate-limit/UI/monitoring semantics.
- Cross-encoder promotion; model downloads.
- Production/default vectorstore mutation; Docker; deploy; remote push.
- New dependencies.

## Verification

Run these targeted checks first:

    python -m pytest tests/test_pilot_validation_pack.py -q
    python -m pytest tests/test_enduser_chat_ui.py tests/test_chroma_where_builder.py tests/test_monitoring_alerts.py tests/test_enterprise_auth_bridge.py tests/test_tenant_isolation.py -q

Then broader checks:

    python -m pytest --collect-only -q
    python -m pytest -q
    scripts/product_readiness_smoke.sh
    scripts/limited_beta_preflight.sh

Then run the synthetic manufacturing eval and quote the numbers in the report:

    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/manufacturing_pilot/manufacturing_cases.jsonl --chunks-jsonl eval/cases/manufacturing_pilot/manufacturing_chunks.jsonl --output runs/eval/prompt039_manufacturing_check.json

Also re-run the existing safety evals to confirm no regression:

    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt039_smoke_check.json
    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt039_qa_pair_check.json

Do not run commands that read .env. Do not mutate the production/default vectorstore.

## Commit/tag policy

PASS:

- commit message: prompt039 pilot validation and demo readiness pack
- tag: prompt039-pilot-validation-and-demo-readiness-pack

PARTIAL or FAIL:

- no commit, no tag, report blocker and next command.

## Required final output

1. Preconditions
2. Implementation summary (synthetic corpus, eval cases, demo script)
3. Measured eval results (quoted from the actual run; abstain/no-answer honored)
4. Synthetic-data / no-secret safety result
5. Verification results
6. Docs/report paths
7. Git diff summary
8. Commit/tag result
9. Final judgment: PASS / PARTIAL / FAIL
10. Next recommendation
