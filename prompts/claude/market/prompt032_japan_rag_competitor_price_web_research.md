# Prompt032: Japan RAG / Enterprise Chatbot Competitor and Pricing Web Research

You are working in:

/home/rai/chatbot

## Goal

Research actual Japanese-market RAG, enterprise chatbot, internal knowledge search, and corporate AI assistant services with web-verified sources.

The purpose is to replace the previous unverified market assumptions with evidence-backed competitor, pricing, positioning, and target-customer analysis.

This is a market research prompt. Do not implement product features.

## Execution mode

Proceed autonomously.

Commit and tag automatically only if this prompt reaches PASS and the git diff is limited to market research report artifacts.

If web access is unavailable, do not invent facts. Produce a PARTIAL report that clearly states web verification could not be completed.

Do not read .env.
Do not print or infer secrets.
Do not use private customer data.
Do not change source code.
Do not change tests.
Do not mutate vectorstores.
Do not run Docker.
Do not deploy.
Do not push remotely.
No new dependencies.

## Required research scope

Research at least 15 services, prioritizing Japanese-market relevance.

Include, if source evidence is available:

- JAPAN AI
- exaBase 生成AI / ExaWizards
- PKSHA AI Helpdesk / PKSHA Chatbot / BEDORE-related offerings
- KARAKURI
- Allganize / Alli
- OfficeBot
- ChatSense / ナレッジセンス
- Helpfeel
- Zendesk AI / Answer Bot in Japan
- Microsoft Copilot Studio / Azure OpenAI + AI Search
- Amazon Bedrock Knowledge Bases
- Google Vertex AI Search / Agentspace if relevant
- Dify-based Japanese SI offerings if verifiable
- Notion AI / Q&A enterprise if relevant
- any other strong Japanese RAG/chatbot competitors discovered during research

## What to collect for each service

For each service, collect only web-verified facts when possible:

- official service name
- company
- URL
- category:
  - enterprise RAG
  - internal knowledge chatbot
  - customer-support chatbot
  - AI FAQ
  - generative AI platform
  - cloud RAG platform
  - SI / custom build
- target customers:
  - SMB
  - mid-market
  - enterprise
  - public sector
  - manufacturing
  - finance
  - healthcare
  - IT/SaaS
- supported data sources or file types if published
- security claims:
  - private environment
  - tenant isolation
  - access control
  - SSO
  - audit logs
  - IP restriction
  - no training on customer data
  - on-premises or private cloud
- deployment model:
  - SaaS
  - private cloud
  - on-premises
  - managed enterprise
  - SI/custom
- pricing:
  - public price
  - quote-based
  - initial cost
  - monthly fee
  - per-seat
  - enterprise custom
  - unknown
- published case studies or customers
- manufacturing-related evidence
- internal-use evidence
- strengths
- weaknesses or gaps relative to our current repo
- source links and source dates

## Pricing rules

Do not guess exact prices.

Use these labels:

- public_price_verified
- quote_required
- pricing_not_public
- third_party_estimate
- outdated_or_uncertain

If pricing is not public, state that explicitly.

If a price is found from a non-official source, mark it as third_party_estimate and include source reliability.

Never present unverified prices as facts.

## Market share / adoption rules

Do not claim market share unless a reliable source gives it.

If market share cannot be verified, use:

- market_share_unknown

You may use proxy signals only if clearly labeled:

- number of case studies
- customer logos
- SEO visibility
- public enterprise examples
- funding/listed-company status
- partner ecosystem
- product maturity

## Comparison against our current repo

Use the repo evidence from recent reports if available:

- docs/reports/commercial_rag_chatbot_readiness_after_prompt028.md
- docs/reports/japan_rag_market_positioning_after_prompt030.md
- docs/reports/beta_go_no_go_assessment.md
- docs/reports/current_state_after_prompt026_limited_beta_pack.md
- artifacts/readiness/commercial_rag_chatbot_readiness_after_prompt028.json
- artifacts/readiness/production_readiness_report.json

Compare competitors against our current product on:

- Japanese internal-document RAG fit
- multi-format ingestion
- approved Q&A route
- abstain / hallucination guard
- citation behavior
- on-prem / private deployment
- tenant isolation
- audit / monitoring
- admin UX
- end-user UI
- SSO
- pricing transparency
- manufacturing fit
- speed to PoC
- ability to sell as a small vendor

## Required output report

Create:

- docs/reports/japan_rag_competitor_price_web_research.md
- artifacts/market/japan_rag_competitor_price_web_research.json

The markdown report must include:

1. Executive summary
2. Research method and date
3. Source reliability rules
4. Competitor table
5. Pricing table
6. Deployment/security comparison
7. Manufacturing/internal-use fit comparison
8. Market-share evidence or explicit unknowns
9. Top 10 realistic market opportunities for our product
10. Best first target segment
11. Recommended price range for our first PoC
12. Recommended one-page positioning
13. Claims we can safely make
14. Claims we must not make yet
15. Source list with URLs and access dates
16. Next recommended implementation prompt

The JSON must include:

- generated_at
- research_date
- web_access_available
- competitors
- pricing_summary
- sources
- market_share_findings
- top_10_opportunities
- recommended_first_segment
- recommended_poc_price_range
- unsupported_claims
- next_recommended_prompt

## Top 10 opportunity ranking

Rank the top 10 candidate segments for our product.

For each segment, include:

- rank
- industry
- department or job function
- expected user count
- buyer persona
- pain point
- why our current repo fits
- missing features
- expected PoC price range
- expected annual price range
- sales difficulty
- delivery difficulty
- security requirement level
- urgency
- realistic probability of winning
- recommended first outreach message angle

Prioritize realism over ambition.

## First target decision

Choose exactly one first commercial target.

Use this decision rule:

- prefer a segment where our current repo already covers the core workflow
- prefer a segment with strong need for private/on-prem deployment
- prefer a segment where no full SSO/HA/massive scale is required for first PoC
- prefer high willingness to pay
- avoid segments requiring heavy certifications or public-sector procurement first
- avoid B2C or mission-critical customer support as first target

Expected likely candidate:

- manufacturing internal technical knowledge / manuals / procedure QA

But verify against web evidence before deciding.

## Verification

Run safe repo checks only:

    git status --short
    git log --oneline --decorate -10

If web access is available, perform web research and cite sources.

If web access is unavailable:

- create a PARTIAL report
- clearly state that competitor/pricing verification is incomplete
- do not commit as PASS unless the report is explicitly useful and labeled as partial

Do not run tests unless you modified report-generation helpers, which should be avoided.

## Commit/tag policy

PASS:

- only if web-verified report is created with sources
- commit message: analysis japan rag competitor price web research
- tag: analysis-japan-rag-competitor-price-web-research

PARTIAL:

- no tag unless the report clearly states web unavailable and is intentionally committed as a draft
- preferred commit message if committing partial: analysis japan rag competitor price research partial draft

FAIL:

- no commit
- no tag
- report blocker and next command

## Required final output

1. Web access status
2. Research summary
3. Competitors researched
4. Pricing findings
5. Market-share findings
6. Top 10 opportunities
7. First target decision
8. Recommended PoC price
9. Report paths
10. Source quality notes
11. Git diff summary
12. Commit/tag result
13. Final judgment: PASS / PARTIAL / FAIL
