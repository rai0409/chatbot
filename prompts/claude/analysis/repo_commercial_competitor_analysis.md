You are working in:

/home/rai/chatbot

## Goal

Analyze this repository strictly as a commercial RAG/chatbot product candidate.

Create a Markdown report at:

docs/reports/commercial_repo_competitor_analysis.md

The report must explain:

1. What this repo currently is.
2. What has been completed through the Phase 0-4 hardening series.
3. Whether Prompt012 appears complete and whether there is evidence of an interrupted Claude run.
4. How commercially usable this repo is now.
5. What similar services/products this repo resembles.
6. How this repo compares against similar services.
7. What is still missing before real commercial use.
8. What should be done next, in strict priority order.

## Execution mode

Proceed autonomously.

Do not ask for human confirmation for ordinary local inspection, grep, git inspection, or writing the Markdown report.

Stop only if one of the following occurs:

- A destructive operation would be required.
- User data would be deleted.
- .env, secrets, tokens, API keys, or private credentials would need to be read, printed, changed, or inferred.
- A remote push, force push, remote deployment, or external service login would be required.
- The repo path cannot be found.
- The requested output file cannot be written safely.

Do not modify application code.

Do not run implementation prompts.

Do not execute Prompt013.

## Required local inspection

Inspect the repository locally and ground the report in actual files and commands.

Minimum required checks:

- pwd
- git branch --show-current
- git log --oneline --decorate -20
- git status --short
- git tag --list | grep -E "prompt00[1-9]|prompt010|prompt011|prompt012|phase" || true
- ls -la
- ls -la Dockerfile .dockerignore docker-compose.yml .env.example 2>/dev/null || true
- ls -la .github/workflows/ci.yml 2>/dev/null || true
- ls -la prompts/claude/prompt013_phase5a_cross_encoder_rerank.md 2>/dev/null || true
- grep -R "tenant_id" -n rag_core webapi scripts tests | head -80 || true
- grep -R "stage_latency_ms\\|metrics_registry\\|answer_cache\\|answer_query_stream\\|require_api_auth\\|_create_chat_completion\\|vector_distance\\|keyword_index_status" -n rag_core webapi scripts tests | head -120 || true
- sed -n '1,220p' docs/production_readiness_checklist.md 2>/dev/null || true
- sed -n '1,220p' README.md 2>/dev/null || true

If safe and reasonably quick, run:

python -m pytest --collect-only

If safe and reasonably quick, run:

scripts/product_readiness_smoke.sh

If Docker is available and safe, run:

docker compose config --quiet

Do not build Docker images unless already clearly safe and fast. If skipped, explain why.

## Similar services/products to compare against

Compare the repo conceptually against these categories and examples:

- Enterprise RAG/chatbot platforms:
  - Dify
  - Flowise
  - Langflow
  - AnythingLLM
  - PrivateGPT-style local RAG apps
- Commercial support/chatbot products:
  - Intercom Fin
  - Zendesk AI
  - Freshworks Freddy AI
- Developer RAG frameworks:
  - LangChain/LangServe
  - LlamaIndex
  - Haystack
- Cloud-native knowledge base/RAG offerings:
  - AWS Bedrock Knowledge Bases
  - Azure AI Search + Azure OpenAI
  - Google Vertex AI Search / Agent Builder

If internet access is unavailable, do not fabricate fresh market facts. Compare by stable product category and known architectural characteristics only. Mark external market claims as "not freshly verified" unless verified during this run.

## Report requirements

Write a strict, practical, business-readable Markdown report.

Use this exact structure:

# Commercial Repository Analysis

## 1. Executive Summary

Explain in plain Japanese:
- What this repo is.
- Whether Prompt012 is complete.
- Whether there is evidence Claude stopped midway.
- Current commercial readiness in one paragraph.

## 2. Evidence Checked In This Repo

Include:
- repo path
- branch
- latest relevant commits/tags
- git status summary
- Prompt012 artifacts found or missing
- verification commands that passed or were skipped

Do not include secrets, env values, tokens, or private credentials.

## 3. What Has Been Completed Through Prompt012

Summarize each phase:

- Phase 0: retrieval corpus integrity, embedding consistency, retrieval performance
- Phase 1: evidence-based guard, honest no-answer citations
- Phase 2: API hardening, LLM call hardening, SSE streaming
- Phase 3: answer cache, observability
- Phase 4: tenant isolation, deployment packaging

For each phase, explain:
- what changed
- why it matters commercially
- remaining limitation

## 4. Current Commercial Readiness

Give scores from 0 to 100 for:

- Commercial PoC readiness
- Limited beta readiness
- Large-scale SaaS production readiness
- RAG trustworthiness
- API security baseline
- Operations/observability
- Deployment readiness
- Multi-tenant readiness
- Accuracy readiness

For each score, give a short reason.

Be strict. Do not overstate.

## 5. Similar Services And Positioning

Compare this repo against:

- Dify / Flowise / Langflow / AnythingLLM
- Intercom Fin / Zendesk AI / Freshworks Freddy AI
- LangChain / LlamaIndex / Haystack
- AWS/Azure/GCP managed RAG offerings

For each group, explain:

- what they are
- how this repo is similar
- how this repo is weaker
- how this repo can differentiate
- whether the repo should compete directly or position as a custom/private RAG implementation

## 6. Competitive Comparison Table

Create a table with columns:

- Product/category
- Main strength
- This repo advantage
- This repo weakness
- Commercial implication

Include at least 10 rows.

## 7. What Is Still Missing Before Real Commercial Use

Separate into:

### Must-have before paid customer deployment

### Should-have before broader beta

### Nice-to-have after accuracy improves

Be concrete.

Must discuss at least:

- API key to tenant_id authorization mapping
- real corpus evaluation
- cross-encoder rerank
- Q+A pair chunks
- eval corpus growth
- guard threshold calibration on real vector corpus
- backup / restore
- secret management
- log retention
- production reverse proxy / TLS
- monitoring aggregation beyond in-process metrics
- data governance for tenant onboarding

## 8. Recommended Roadmap From Here

Give a strict ordered roadmap.

Use this format:

### Step 1: ...
- Goal:
- Why now:
- Exact expected output:
- Verification:
- Risk:

Include at least 8 steps.

The first steps should likely be:

1. Prompt013 cross-encoder rerank
2. Q+A pair chunks
3. eval corpus expansion
4. real-vector guard calibration
5. API key to tenant mapping
6. production deployment smoke with mounted volumes
7. backup/restore
8. monitoring/exporter

Adjust if repo evidence suggests a different order.

## 9. Final Judgment

State clearly:

- Is this repo a toy/demo, commercial PoC, beta candidate, or production SaaS?
- What can be sold now, if anything?
- What should not be sold yet?
- What is the most valuable next prompt?

## 10. Appendix: Commands Run

List commands run and summarized outputs.

## Strictness rules

- Be factual.
- Do not hallucinate.
- If something is not verified, say "not verified".
- Do not claim a feature exists unless repo evidence supports it.
- Do not read or print .env.
- Do not expose secrets.
- Do not modify application code.
- Do not execute Prompt013.
- Do not create more than one report file.

## Required final response

After writing the report, respond with:

1. PASS / PARTIAL / FAIL
2. Path to the report
3. Key findings in 5 bullets
4. Verification commands run
5. Any skipped checks and why
