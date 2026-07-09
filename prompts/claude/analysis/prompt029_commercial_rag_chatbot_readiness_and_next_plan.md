# Prompt029: Commercial RAG Chatbot Readiness Evaluation and Next Plan

You are working in:

/home/rai/chatbot

## Goal

Evaluate the current repository as a real commercial RAG / enterprise chatbot product after Prompt028, then decide the most valuable next implementation direction.

This is an analysis and planning prompt. Do not implement product features. Do not change runtime behavior. Do not modify source code unless a tiny report-generation helper is absolutely necessary, and prefer not to.

The final output must answer:

- What is currently completed?
- What is actually verified by repo evidence?
- How strong is the current product as a commercial RAG / chatbot?
- What can be used in a limited external beta today?
- What is not yet safe for general production?
- What are the highest-value next implementation prompts?
- What should be done first, second, and third, with reasons?
- What should not be done yet?
- What commercial positioning is realistic now?

## Execution mode

Proceed autonomously.

Commit and tag automatically only if this prompt reaches PASS and the git diff is limited to analysis/report artifacts and one next-prompt artifact.

Stop only for destructive operations, user-data deletion, secrets/.env access, remote push/deploy, production vectorstore/default collection mutation, required network/model downloads, ambiguous missing targets, or unresolved verification failure after one bounded fix attempt.

Do not read .env.
Do not print or infer secrets.
Do not download models.
Do not run Prompt020.
Do not change cross-encoder settings.
Do not change distance thresholds.
Do not change tenant authorization semantics.
Do not change tenant isolation semantics.
Do not change rate-limiter semantics.
Do not change the too_general guard.
Do not mutate production/default vectorstore.
Do not use real customer data.
Do not push remotely.
Do not deploy externally.
No new dependencies.

## Preconditions to verify

Verify and record:

- Current branch and HEAD.
- Working tree status.
- Tag `prompt028-chat-tenant-product-profile-runtime-wiring` exists.
- Tag `analysis-current-state-after-prompt026-limited-beta-pack` exists.
- Tag `prompt026-limited-beta-launch-pack` exists.
- The latest product/readiness reports exist if present:
  - `docs/reports/current_state_after_prompt026_limited_beta_pack.md`
  - `artifacts/readiness/current_state_after_prompt026_summary.json`
  - `docs/reports/beta_go_no_go_assessment.md`
  - `artifacts/readiness/production_readiness_report.json`
  - `artifacts/readiness/production_readiness_report.md`
  - `docs/reports/prompt028_chat_tenant_product_profile_runtime_wiring.md`
- The limited beta launch pack exists:
  - `docs/reports/limited_beta_launch_checklist.md`
  - `docs/reports/limited_beta_rollback_runbook.md`
  - `docs/reports/pilot_tenant_onboarding_runbook.md`
  - `scripts/limited_beta_preflight.sh`

## Analysis scope

### 1. Product capability assessment

Evaluate the current repo as a commercial Japanese enterprise internal-document RAG chatbot.

Assess these categories:

- Document ingestion
- Multi-format support: PDF, DOCX, XLSX, CSV, PPTX
- Canonical chunk generation
- FAQ / Q&A pair extraction
- Retrieval quality
- Citation quality
- Approved Q&A exact-match route
- Abstain / hallucination guard
- Japanese query handling
- Tenant isolation
- API key tenant authorization
- Admin/search debug controls
- Rate limiting
- Per-tenant product profile runtime wiring
- Metrics and Prometheus export
- Alert threshold documentation
- Audit logging and retention
- Deploy smoke
- Backup and restore
- Dry-run onboarding and import manifest
- Limited beta launch pack
- Rollback readiness
- Pilot tenant onboarding readiness
- Production readiness reporting

For each category, classify:

- ready
- partial
- not ready
- not applicable yet

For each category, include:

- evidence files
- proving tests/evals/scripts
- commercial value
- remaining risk
- what would make it production-grade

### 2. Commercial readiness score

Provide numerical scores from 0 to 100 for:

- Limited external beta readiness
- General production readiness
- RAG answer quality readiness
- Data onboarding readiness
- Security readiness
- Tenant isolation readiness
- Operations readiness
- Observability readiness
- Maintainability readiness
- Sales/demo readiness

For each score, give:

- score
- reason
- strongest evidence
- biggest missing item

Do not inflate scores. Use repo evidence only.

### 3. Real commercial use-case fit

Evaluate which use cases are realistic now:

- internal FAQ bot
- internal policy/manual chatbot
- PDF/DOCX/XLSX/PPTX document Q&A
- approved-answer support bot
- small pilot for one or few companies
- multi-tenant SaaS
- regulated enterprise production
- mission-critical customer support

For each, classify:

- ready now
- beta with conditions
- not yet

Explain why.

### 4. Remaining blockers

Analyze remaining blockers in priority order.

At minimum evaluate:

- durable multi-tenant persistence
- restart / restore tenant-isolation proof
- production/default vectorstore handling
- real customer data onboarding controls
- post-deploy smoke automation
- automated rollback path
- cross-encoder rerank promotion decision
- external secret store integration
- distributed rate limiting
- actual monitoring/alert wiring
- richer per-tenant policy effects beyond top_k clamp
- multi-tenant admin/management workflow
- customer-facing UX or integration surface
- evaluation coverage with more realistic Japanese business documents
- latency and cost measurement under load

For each blocker, include:

- risk level: high / medium / low
- whether it blocks limited beta
- whether it blocks general production
- recommended implementation prompt
- estimated complexity: small / medium / large
- recommended order

### 5. Next-prompt decision

Decide exactly one next implementation prompt.

Use this decision rule:

- If durable persistence and tenant isolation across restart/restore are unproven, recommend this first.
- Else if production/default vectorstore safety is unproven, recommend this first.
- Else if actual monitoring/alert wiring is unimplemented, recommend this first.
- Else if real customer onboarding controls are still manual-only, recommend this first.
- Else recommend cross-encoder promotion if the model is locally cached; otherwise keep it parked.

The recommended next prompt should be specific and implementable.

Expected likely next direction:

- Durable multi-tenant persistence verification with synthetic data, non-production collection, restart/reload, backup/restore, and tenant-isolation assertions.

But verify from repo evidence before deciding.

### 6. Generate the next implementation prompt

After the analysis, write exactly one next implementation prompt file under:

- `prompts/claude/product/`

Use a filename based on the chosen next action.

If durable persistence is chosen, use:

- `prompts/claude/product/prompt030_durable_multitenant_persistence_verification.md`

The next implementation prompt must:

- include clear goal
- include strict safety constraints
- avoid reading `.env`
- avoid production/default vectorstore mutation
- use synthetic data only
- require non-production collection
- include tests
- include verification commands
- include commit/tag policy
- include required final output
- avoid markdown triple-backtick fences inside the prompt body

Also include a self-check that the generated prompt contains no markdown triple-backtick fences.

### 7. Reports to create

Create:

- `docs/reports/commercial_rag_chatbot_readiness_after_prompt028.md`
- `artifacts/readiness/commercial_rag_chatbot_readiness_after_prompt028.json`

The markdown report must include:

- Executive summary
- What is completed
- What is verified
- Commercial readiness scores
- Real use-case fit
- Limited beta readiness decision
- General production blockers
- Ordered next steps
- Recommended next implementation prompt
- Do-not-do-yet list
- Risk register
- Exact commands used
- Unknowns and assumptions

The JSON summary must include:

- branch
- head_commit
- tags_detected
- limited_beta_readiness_score
- general_production_readiness_score
- category_scores
- realistic_use_cases
- blockers
- next_steps_ordered
- recommended_next_prompt_path
- verification_results
- generated_at

Do not include secrets, raw API keys, private document contents, or `.env` contents.

## Verification commands

Run safe verification commands only.

Required:

    git status --short
    git log --oneline --decorate -20
    git tag --list

    python -m pytest --collect-only -q

    python -m pytest tests/test_chat_tenant_product_profile_runtime.py tests/test_api_key_tenant_authorization.py tests/test_tenant_isolation.py -q

    python -m pytest tests/test_rate_limit.py tests/test_metrics_observability.py tests/test_observability_export.py tests/test_production_readiness_report.py -q

    scripts/product_readiness_smoke.sh

    scripts/limited_beta_preflight.sh

Run synthetic evals if safe:

    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt029_smoke_check.json

    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt029_qa_pair_check.json

Do not run commands that read `.env`.
Do not mutate production/default vectorstore.
Do not run Docker smoke automatically; document the optional command instead.

## Commit/tag policy

PASS:

- commit message: `analysis commercial rag chatbot readiness after prompt028`
- tag: `analysis-commercial-rag-chatbot-readiness-after-prompt028`

PARTIAL or FAIL:

- no commit
- no tag
- report blocker and next command

## Required final output

1. Preconditions
2. Commercial readiness summary
3. Scores
4. Limited beta decision
5. General production blockers
6. Ordered next steps
7. Recommended next implementation prompt path
8. Verification results
9. Report paths
10. Git diff summary
11. Commit/tag result
12. Final judgment: PASS / PARTIAL / FAIL
