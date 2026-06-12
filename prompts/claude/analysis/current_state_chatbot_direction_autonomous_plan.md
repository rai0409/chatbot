# Current State, Product Direction, And Autonomous Execution Plan

You are working in:

/home/rai/chatbot

## Goal

Do not implement code in this run.

Analyze the current repository state and produce a decision report that answers:

1. What is already completed, with evidence.
2. What kind of chatbot this repository should become commercially.
3. What is still missing from the current point.
4. How to split the remaining work by responsibility.
5. How to run the remaining work through Claude Code automatically with minimal human interruption.
6. Which prompts should be executed next, in what order, and which ones can be combined.

The user wants to stop manually executing one small prompt at a time. The output should become the basis for a larger autonomous Claude execution plan.

## Execution mode

Analysis only.

Do not modify production code.
Do not create implementation files.
Do not run Prompt017.
Do not execute any next prompt.
Do not commit.
Do not tag.
Do not push.
Do not read or print .env, secrets, tokens, API keys, or credentials.

You may inspect repository files, git history, tags, tests, eval artifacts, docs, prompts, and local reports.

You may run non-destructive local verification commands if they are safe and fast.

## Required checks

Run or inspect the equivalent of:

- pwd
- git branch --show-current
- git status --short
- git log --oneline --decorate -30
- git tag --list "prompt*" sorted naturally if possible
- git rev-list --count main..HEAD if main exists
- ls of key prompt files from Prompt012 through Prompt017
- ls of eval/cases
- ls of runs/eval
- grep for:
  - API_AUTH_TENANT_MAP
  - ApiAuthContext
  - enforce_tenant_authorization
  - CROSS_ENCODER_RERANK_ENABLED
  - hybrid_rerank_ce
  - approved_qa_pair
  - qa_pair
  - real_corpus_cases
  - RAG_MAX_DISTANCE
- inspect:
  - docs/reports/commercial_repo_competitor_analysis.md if present
  - runs/eval/prompt016_real_corpus_baseline.json if present
  - prompts/claude/prompt017_phase5d_real_vector_guard_calibration.md if present
  - README.md
  - docs/production_readiness_checklist.md if present

If safe and fast, run:

python -m pytest --collect-only -q

Do not run full test suites unless needed for analysis.

## Current known context to verify, not assume

The previous prompt series likely completed:

- Prompt012: deployment packaging
- Prompt013: API key to tenant authorization
- Prompt014: optional cross-encoder rerank
- Prompt015: Q+A pair chunks
- Prompt016: eval corpus expansion to 100+ labeled cases
- Prompt017 exists but has not been executed

Verify these from git tags, files, and local artifacts.

## Product decision requirements

Decide what chatbot this should become.

The decision must choose one primary product direction, not many vague options.

Evaluate these candidate directions:

1. Generic chatbot SaaS platform
2. Japanese private RAG chatbot for companies
3. PDF/table-style Q&A chatbot builder
4. Customer-support chatbot
5. Internal compliance/manual chatbot
6. Developer-facing RAG framework

For each, briefly state:

- fit with current repo
- commercial potential
- implementation gap
- risk

Then choose one primary direction and one secondary use case.

The recommended direction must be grounded in what this repo already does well:

- citation-first RAG
- approved Q&A exact-match route
- Q+A pair chunks
- tenant isolation and tenant authorization
- SSE chat API
- Docker packaging
- eval corpus and guard calibration path
- Japanese/table-style document handling

## Required responsibility split

Split remaining work by responsibility, not by tiny prompts.

Use these responsibility groups:

A. Accuracy and Evidence
B. Security and Tenant Operations
C. Deployment and Operations
D. Product/API Surface
E. Data Ingestion and Admin Workflow
F. Documentation and Commercial Packaging
G. Automation Runner For Claude Execution

For each group, provide:

- current status
- missing work
- acceptance criteria
- likely files to touch
- verification commands
- whether it should be done before beta, after beta, or later

## Required autonomous execution plan

Design a plan where Claude can run longer without stopping after every small prompt.

Output:

1. Recommended prompt batch structure.
2. Which existing Prompt017 should be kept as-is.
3. Which future prompts should be merged.
4. Which future prompts must remain separate because they are risky.
5. A proposed sequence of 5 to 8 larger prompts from current state to limited beta readiness.
6. For each larger prompt:
   - title
   - goal
   - included tasks
   - explicit non-goals
   - stop conditions
   - verification commands
   - expected artifacts
   - commit/tag name
7. A final "autonomous master prompt" design that can:
   - inspect current state
   - execute the next prompt
   - run verification
   - commit/tag on PASS
   - generate the next prompt
   - stop safely on FAIL/PARTIAL
   - never read secrets
   - never push remotely

Do not implement this master runner. Only design it.

## Important constraints

Be strict and evidence-based.

Do not hallucinate completed work.
If evidence is missing, say "not verified".
Do not claim production readiness unless supported.
Do not suggest broad rewrites.
Do not suggest adding a UI before accuracy/security/deploy gates are stable.
Do not require paid external services.
Do not require internet.
Do not require model downloads unless explicitly optional.

## Required report output

Write exactly one markdown report to:

docs/reports/current_state_chatbot_direction_autonomous_plan.md

The report must have this structure:

1. Executive Summary
2. Evidence Checked
3. Confirmed Current State
4. What Kind Of Chatbot This Should Become
5. Product Direction Decision
6. Remaining Gaps By Responsibility
7. Recommended Larger Prompt Batches
8. Proposed Autonomous Claude Execution Strategy
9. What To Do Next Immediately
10. Risks And Stop Conditions
11. Appendix: Commands Run

## Final console output

After writing the report, print:

- PASS / PARTIAL / FAIL
- report path
- 10 key findings
- immediate next command the user should run
