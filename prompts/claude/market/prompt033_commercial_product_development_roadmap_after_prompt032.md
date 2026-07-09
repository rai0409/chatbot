# Prompt033: Commercial Product Development Roadmap After Japan RAG Market Research

You are working in:

/home/rai/chatbot

## Goal

Create a detailed markdown strategy report that explains how the current chatbot/RAG system should be developed into a sellable commercial product for the Japanese market.

This prompt must not implement product features.

It must convert the current repo capabilities, Prompt030 persistence evidence, and Prompt032 web-verified competitor/pricing research into a concrete product development roadmap.

The output must answer:

- What commercial product should this chatbot become?
- Who should it serve first?
- What exact use cases should it solve?
- What user count and customer size should we target?
- What industry and department should we target first?
- What pricing and support model should we offer?
- What features are already enough for a PoC?
- What features are missing before first paid PoC?
- What features are missing before annual production deployment?
- What technical roadmap should we follow?
- What should we not build yet?
- What should the next implementation prompt be?

## Execution mode

Proceed autonomously.

Do not ask for yes/no confirmation.

Do not implement code.
Do not change source files.
Do not change tests.
Do not change configs.
Do not mutate vectorstore.
Do not run Docker.
Do not deploy.
Do not push remotely.
Do not read .env.
Do not print or infer secrets.
Do not use real customer data.
No new dependencies.

Only create or update markdown/json report artifacts and, if useful, exactly one next implementation prompt.

## Required evidence to use

Use repo evidence and prior reports where available:

- docs/reports/japan_rag_competitor_price_web_research.md
- artifacts/market/japan_rag_competitor_price_web_research.json
- docs/reports/commercial_rag_chatbot_readiness_after_prompt028.md
- artifacts/readiness/commercial_rag_chatbot_readiness_after_prompt028.json
- docs/reports/prompt030_durable_multitenant_persistence_verification.md
- docs/reports/prompt028_chat_tenant_product_profile_runtime_wiring.md
- docs/reports/beta_go_no_go_assessment.md
- docs/reports/limited_beta_launch_checklist.md
- docs/reports/pilot_tenant_onboarding_runbook.md
- docs/reports/limited_beta_rollback_runbook.md
- artifacts/readiness/production_readiness_report.json if present

Also inspect relevant repo paths only as needed:

- webapi/
- rag_core/
- document_converters/
- scripts/
- tests/
- docs/

Do not read .env.

## Business premise

The latest web-verified Prompt032 finding selected the first target as:

Manufacturing internal technical knowledge / manuals / procedure QA, delivered as an on-premises or private single-department pilot.

Use this as the starting hypothesis, but verify it against repo evidence and Prompt032 report before finalizing.

## Report to create

Create:

- docs/reports/commercial_product_development_roadmap_after_prompt032.md
- artifacts/market/commercial_product_development_roadmap_after_prompt032.json

## Required markdown report structure

### 1. Executive decision

State the final commercial direction in 5 lines or fewer.

Must include:

- product category
- first target industry
- first target department
- deployment model
- first monetization model

### 2. Current product reality

Explain what the current chatbot actually is today.

Separate into:

- repo-verified capabilities
- beta-ready capabilities
- commercial gaps
- not production-ready areas

Use labels:

- [実] repo-verified
- [調] web-verified market research
- [推] reasoned assumption
- [不明] unknown

### 3. Recommended commercial product definition

Define the product as if we will sell it.

Include:

- working product name
- one-line value proposition
- primary customer
- primary user
- buyer persona
- admin persona
- first use case
- non-goals
- why this is not a generic ChatGPT wrapper

### 4. First customer profile

Make this very specific.

Include:

- industry
- company size
- department
- expected user count
- document volume range
- document types
- buyer title
- user title
- pain points
- why they need on-prem/private
- why they care about abstain-first behavior
- why they care about approved Q&A
- why they might reject us

### 5. Use case definition

Define the first 5 practical use cases.

For each:

- user story
- input documents
- expected answer style
- why citation matters
- what happens when the system is unsure
- current repo support level
- missing pieces

Focus on manufacturing technical knowledge, manuals, procedures, troubleshooting, quality, safety, and internal helpdesk.

### 6. Pricing and package strategy

Define practical commercial packages.

At minimum include:

- 3-month paid PoC package
- first-year single-department package
- support/maintenance package
- optional document onboarding package
- optional approved-QA creation package
- optional on-prem installation support

For each package:

- price range
- what is included
- what is explicitly not included
- customer success criteria
- support scope
- renewal or upsell path

Use Prompt032 as market anchor. Do not claim competitor prices unless verified in Prompt032.

### 7. Support model

Define what support we can realistically provide.

Include:

- setup support
- document ingestion support
- Q&A approval workflow support
- monthly accuracy review
- incident response expectation
- backup/restore support
- monitoring support
- what support we cannot promise yet

Be realistic for a small vendor or solo/few-person team.

### 8. Product roadmap

Create a strict 4-phase roadmap.

Phase 0: current state
Phase 1: first paid PoC
Phase 2: first annual production customer
Phase 3: enterprise expansion

For each phase include:

- objective
- required features
- excluded features
- verification evidence required
- price level it unlocks
- estimated implementation complexity
- risk

### 9. Technical development priorities

Prioritize the next implementation work.

Use this order unless evidence says otherwise:

1. Minimal end-user chat UI for on-prem pilot
2. Chroma multi-key where $and-safe retrieval fix
3. Monitoring and alert wiring
4. SSO/AD or simple enterprise auth bridge
5. Safe production/default collection promotion workflow
6. VectorStore adapter boundary
7. pgvector non-production adapter spike
8. Qdrant comparison only if needed

For each item include:

- why it matters commercially
- why it matters technically
- whether it blocks PoC
- whether it blocks annual deployment
- recommended prompt name
- acceptance criteria

### 10. What not to build yet

Explicitly list what should not be built now.

Include likely examples:

- full SaaS multi-tenant billing
- advanced admin console
- public marketplace
- mobile app
- custom LLM training
- cross-encoder promotion if model is not cached
- HA cluster
- complex role-based UI
- public-sector procurement features
- finance-grade compliance pack

Explain why each is premature.

### 11. Sales and demo plan

Define the first demo scenario.

Include:

- demo audience
- sample document set
- 5 demo questions
- expected system responses
- one deliberate too-general question
- one approved-QA exact answer
- one citation-based answer
- one no-answer case
- how to explain safety
- how to explain on-prem/private deployment

### 12. Risk register

Create a risk table.

Include:

- risk
- severity
- probability
- business impact
- technical impact
- mitigation
- owner/action

Must include:

- no end-user UI
- SSO/AD gap
- monitoring not wired
- Chroma where issue
- single-node deployment
- real customer data onboarding
- accuracy unknown on large real Japanese documents
- support burden
- price objection
- competitor response

### 13. Final recommendation

Answer clearly:

- Should we sell now?
- If yes, under what exact conditions?
- If no, what exactly unlocks sellability?
- What is the next implementation prompt?
- What is the next sales artifact prompt?
- What is the next validation experiment?

## JSON summary

Create JSON with:

- generated_at
- branch
- head_commit
- reports_used
- final_product_direction
- first_target_segment
- expected_user_count
- recommended_poc_price_range
- recommended_annual_price_range
- phase_roadmap
- technical_priorities
- next_implementation_prompt
- next_sales_artifact_prompt
- risk_register_summary
- sellability_decision

## Next prompt generation

Generate exactly one next implementation prompt only if the report concludes it is needed.

Expected likely next prompt:

- prompts/claude/product/prompt034_minimal_enduser_chat_ui_for_onprem_pilot.md

This next prompt should be implementation-ready, but it must not be executed by this prompt.

If generated, it must:

- focus on minimal end-user chat UI for on-prem manufacturing pilot
- use existing /chat/stream and /chat/feedback
- expose no raw API key in browser
- keep API auth and tenant authorization semantics unchanged
- keep production_safe behavior unchanged
- add tests for UI serving, unauthorized behavior, safe config, no secret exposure
- avoid .env reading
- avoid vectorstore mutation
- avoid new dependencies if possible
- include commit/tag policy
- include final output requirements
- contain no markdown triple-backtick fences

## Verification

Run safe commands:

    git status --short
    git log --oneline --decorate -15
    git tag --list | tail -50

Check that expected reports exist where available:

    test -f docs/reports/japan_rag_competitor_price_web_research.md
    test -f artifacts/market/japan_rag_competitor_price_web_research.json
    test -f docs/reports/prompt030_durable_multitenant_persistence_verification.md

Do not run tests unless you modify source code, which you should not.

If you generate a next prompt, verify no markdown fences:

    python3 -c "from pathlib import Path; p=Path('prompts/claude/product/prompt034_minimal_enduser_chat_ui_for_onprem_pilot.md'); bad=chr(96)*3; raise SystemExit(1 if bad in p.read_text(encoding='utf-8') else 0)"

## Commit/tag policy

PASS:

- commit only report artifacts and optional next-prompt artifact
- commit message: analysis commercial product development roadmap after prompt032
- tag: analysis-commercial-product-development-roadmap-after-prompt032

PARTIAL:

- no tag unless the report is clearly useful and limitations are explicit
- commit message if committing partial: analysis commercial product development roadmap partial draft

FAIL:

- no commit
- no tag
- report blocker and next command

## Required final output

1. Preconditions
2. Reports used
3. Commercial product direction
4. First target segment
5. Pricing/package decision
6. Roadmap summary
7. Technical priority order
8. Next implementation prompt path
9. Report paths
10. Git diff summary
11. Commit/tag result
12. Final judgment: PASS / PARTIAL / FAIL

