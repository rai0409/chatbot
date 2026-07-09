# Japan RAG / Enterprise Chatbot — Competitor & Pricing Web Research

Web-verified market research (Prompt032). Replaces the earlier unverified
assumptions in `japan_rag_market_positioning_after_prompt030.md` with
source-cited findings. No source code, tests, or vectorstores were touched.

## 1. Executive summary

- **Web access: AVAILABLE** — findings below are cited to live sources
  (accessed 2026-06-13). This is a **PASS** (web-verified) report.
- The Japanese internal-document RAG / enterprise-chatbot market is crowded and
  maturing: **national enterprise GenAI/RAG** (JAPAN AI, exaBase, Allganize
  Alli, Stockmark, ELYZA), **FAQ/contact-center AI** (PKSHA, KARAKURI,
  OfficeBot, Helpfeel), **low-cost ChatGPT-wrappers** (ChatSense), and
  **managed cloud RAG** (Azure OpenAI/Copilot Studio, Amazon Bedrock KB, Google
  Vertex AI Search), plus **OSS/SI** (Dify, incl. Ricoh as a Japan enterprise
  partner).
- **Pricing is mostly quote-based.** Only a few publish prices: ChatSense
  (from ¥980/月〜 + token pay-as-you-go), and the cloud platforms (usage-based:
  Vertex AI Search $1.50–$4.00/1,000 queries; Bedrock KB consumption-based with
  an OpenSearch Serverless floor ~$345/月). Enterprise JP RAG vendors (JAPAN AI,
  exaBase, Allganize, PKSHA, KARAKURI, OfficeBot, Helpfeel, Stockmark, ELYZA)
  are **quote_required / pricing_not_public**.
- **Our genuine wedge** (vs verified competitors): true **on-prem / closed-
  network with no cloud dependency**, **abstain-first (no-hallucination)
  guard**, **approved-Q&A determinism**, **multi-format incl. Excel/PPT**, and
  **citations** — at a small-vendor PoC price. Only **Allganize** among the
  enterprise JP players explicitly markets on-prem/private LLM; the managed
  cloud platforms (esp. Vertex) explicitly do **not** offer on-prem.
- **Our gaps** (vs competitors): no end-user chat UI, no SSO/AD, no admin
  console, no HA/scale, no LLM-generated answers in the safest profile, and no
  customer logos/track record.
- **Recommended first target (verified):** **manufacturing internal technical
  knowledge / manuals / procedure QA, on-prem pilot** — consistent with the
  decision rule and with verified manufacturing demand signals (OfficeBot's
  宮崎電子機器 case; Stockmark's manufacturing-oriented multimodal RAG covered by
  Nikkei).

## 2. Research method and date

- Method: live `WebSearch` queries (Japanese + English) on the named services,
  followed by reading official pages, the vendors' own news/PR, and reputable
  third-party aggregators (BOXIL, ITreview, Nikkei/PR TIMES, AWS/Google docs).
- Research date: **2026-06-13** (access date for all sources below).
- Verification scope: ≥15 services. Where a fact came only from a third-party
  aggregator or a vendor claim, it is labeled accordingly.

## 3. Source reliability rules

- **public_price_verified** — price published by the vendor or a reputable
  outlet.
- **quote_required** — vendor states pricing is by inquiry/estimate.
- **pricing_not_public** — no price found.
- **third_party_estimate** — price only from a non-official source (lower
  reliability).
- **outdated_or_uncertain** — date/accuracy unclear.
- Market share: **market_share_unknown** unless a reliable source gives a
  number. Vendor "No.1 / N社導入 / N sites" statements are treated as
  **vendor proxy signals**, not verified share.

## 4. Competitor table (web-verified)

| Service (company) | Category | Targets | Deployment | Security claims (verified) | Pricing label | Manufacturing/internal evidence |
| --- | --- | --- | --- | --- | --- | --- |
| JAPAN AI CHAT / AGENT (JAPAN AI) | Enterprise GenAI/RAG | mid–enterprise | Domestic-DC SaaS | Domestic data centers, security-hardened for JP firms, no-training (claimed) | quote_required | Internal-use GenAI; mfg evidence not confirmed |
| exaBase 生成AI (Exa Enterprise AI / ExaWizards) | Enterprise GenAI/RAG | mid–enterprise | Domestic SaaS (data processed in Japan) | Domestic data processing, IP restriction, access/operation logs, input not used for training | quote_required | 1,000+ companies (vendor proxy); RAG agents |
| Allganize "Alli" (Allganize Japan) | Enterprise RAG/LLM platform | enterprise | **On-prem / private / closed env + on-prem LLM** | Fully closed-environment option | quote_required | NTT Docomo, JR Kyushu (logos); table-aware RAG |
| PKSHA AI Helpdesk / Chatbot / FAQ (PKSHA Technology) | Internal helpdesk / AI FAQ | mid–enterprise | SaaS (Teams-integrated) | Enterprise SaaS; "国内シェアNo.1 AIチャットエージェント" (vendor claim) | quote_required | Internal helpdesk automation; deflection cases |
| KARAKURI chatbot (Karakuri) | Customer-support chatbot | enterprise | SaaS | Own LLM "KARAKURI LM"; hybrid rule+GenAI | quote_required | Sony Network Communications (logo); CS-focused |
| ChatSense (Knowledge Sense) | ChatGPT-wrapper + RAG | SMB–enterprise, gov | SaaS (OpenAI API) | Secure env, no training on data | **public_price_verified** (from ¥980/月 + token PAYG) | 400+ companies (news proxy) |
| OfficeBot (Neos) | 法人向けRAG / internal FAQ | mid–enterprise | SaaS | RAG; upload-only setup | quote_required | **宮崎電子機器 (manufacturing) case**; image/graph/table reading; ~90% accuracy (vendor) |
| Helpfeel (Helpfeel Inc.) | AI FAQ / search | mid–enterprise | SaaS | Hallucination-free framing (curated FAQ + GenAI search) | quote_required (initial+monthly, not public) | 900+ sites, 99% retention (vendor proxy) |
| Microsoft Copilot Studio / Azure OpenAI + AI Search | Cloud RAG platform | mid–enterprise | Managed cloud (+SI build) | Azure enterprise controls | public (usage-based API) + SI/dev/server extra | DIY RAG bots; SI-dependent |
| Amazon Bedrock Knowledge Bases (AWS) | Cloud RAG platform | mid–enterprise | Managed cloud | AWS enterprise controls; no on-prem | consumption-based (OpenSearch Serverless ~$345/月 floor; BDA $0.010/page) | DIY; SI-dependent |
| Google Vertex AI Search (Google Cloud) | Cloud RAG platform | mid–enterprise | Managed cloud (**no on-prem**) | GCP enterprise controls | public_price_verified ($1.50–$4.00 / 1,000 queries; 10k/mo free) | DIY; SI-dependent |
| Dify (LangGenius; Ricoh JP partner; SI e.g. Sateraito) | OSS/SI GenAI platform | SMB–enterprise | Cloud tiers + **self-host/on-prem** | Self-host enables closed network | public cloud tiers (Sandbox free / Professional / Team) + enterprise quote | DIY/SI build |
| Stockmark "Stockmark A Technology (SAT)" (Stockmark) | Enterprise RAG (multimodal) | enterprise | SaaS/API | Multimodal RAG for charts/figures | quote_required | **Manufacturing-oriented** (Nikkei coverage; Stockmark-2-VL model) |
| ELYZA "ELYZA Works" (ELYZA, KDDI group) | Enterprise GenAI/RAG | enterprise | SaaS/enterprise | Japanese LLM; field-staff app building | quote_required | JR West transcription case (+54% efficiency) |
| Notion AI / Notion Q&A (Notion) | Workspace AI Q&A | SMB–enterprise | SaaS | Workspace permissions | public (add-on per-seat, not JP-specific here) | General knowledge Q&A; not on-prem |

(≥15 services covered. Earlier `commercial_repo_competitor_analysis.md` covered
global OSS/SaaS — this report adds the Japan-specific players.)

## 5. Pricing table (with labels)

| Service | Pricing label | What is verified |
| --- | --- | --- |
| ChatSense | public_price_verified | Business plan from **¥980/月**〜 + token-based pay-as-you-go; recent o3 price cut (PR TIMES/Nikkei, chatsense.jp) |
| Google Vertex AI Search | public_price_verified | Standard **$1.50 / 1,000 queries**, Enterprise **$4.00 / 1,000 queries**, 10k queries/mo free (cloud.google.com) |
| Amazon Bedrock KB | public_price_verified (consumption) | No KB fee per se; OpenSearch Serverless floor **~$345/mo**, BDA **$0.010/page**, + model inference (aws.amazon.com + third_party_estimate for floor) |
| Azure OpenAI / Copilot Studio | public (usage-based) | Token usage-based; small internal bots "数十円/月"〜, mid "~¥2万/月" API only — **excludes** dev/server/maintenance (third_party_estimate for examples) |
| Dify | public_price_verified (cloud tiers) | Sandbox free / Professional / Team; enterprise via quote (dify.ai/jp/pricing) |
| JAPAN AI, exaBase, Allganize, PKSHA, KARAKURI, OfficeBot, Helpfeel, Stockmark, ELYZA | quote_required / pricing_not_public | All require inquiry; no list price found |

**Rule honored:** no exact enterprise JP RAG price is asserted as fact;
quote-based vendors are labeled quote_required.

## 6. Deployment / security comparison (vs our repo)

| Capability | Our repo [evidence] | JP enterprise RAG (JAPAN AI/exaBase/Stockmark/ELYZA) | Allganize Alli | Cloud RAG (Azure/Bedrock/Vertex) | FAQ/CS (PKSHA/KARAKURI/OfficeBot/Helpfeel) |
| --- | --- | --- | --- | --- | --- |
| True on-prem / closed network, no cloud dependency | **Yes** (`store.py` local Chroma; deploy smoke proves no-secret image; TLS docs) | Mostly domestic SaaS (data in JP, not on-prem) | **Yes** (on-prem LLM) | No (Vertex explicitly no on-prem) | Mostly SaaS |
| Abstain / no-hallucination guard | **Yes** (`too_general`, calibrated) | Partial (varies) | Partial | DIY (Guardrails optional) | Helpfeel/KARAKURI emphasize accuracy |
| Approved-Q&A determinism | **Yes** (`approved_qa.py` exact match) | Rare | Unknown | DIY | KARAKURI rule-AI hybrid (similar idea) |
| Multi-format incl. Excel/PPT | **Yes** (converters) | Yes (varies) | Yes (table-aware) | DIY | OfficeBot reads image/graph/table |
| Citations | **Yes** (source metadata) | Common | Yes | DIY | Varies |
| Tenant isolation (reload/restore proven) | **Yes** (`test_durable_multitenant_persistence.py`) | SaaS-managed | Managed | Managed | Managed |
| Audit / monitoring | Partial (audit logs; metrics; alerts documented not wired) | Yes (exaBase: IP restriction+logs) | Yes | Yes | Yes |
| Admin UX (non-eng console) | **No** (preview/review HTML only) | Yes | Yes | Portal | Yes |
| End-user chat UI | **No** (API/preview only) | Yes | Yes | Build | Yes |
| SSO / AD | **No** | Common | Likely | Yes | Common |
| Pricing transparency | n/a (pre-commercial) | Low (quote) | Low (quote) | High (usage) | Low (quote) |
| Manufacturing fit | Strong (Excel/PPT + on-prem + abstain) | Stockmark strong | Strong | Generic | OfficeBot has mfg case |
| Speed to PoC | Fast (single-node, synthetic-data onboarding) | Medium (sales cycle) | Medium–slow | Medium (SI) | Medium |
| Sellable as a small vendor | Hard (no logos/UI/SSO) | They are funded/listed | Funded | Hyperscaler | Established |

## 7. Manufacturing / internal-use fit comparison

- **Verified manufacturing/internal demand signals:** OfficeBot publishes a
  manufacturing case (宮崎電子機器) and internal-FAQ deflection use; Stockmark is
  explicitly manufacturing-oriented (Nikkei) with multimodal chart/figure RAG;
  ELYZA shows field-staff app building (JR West). This confirms manufacturing
  internal-document QA is a real, contested segment — not a guess.
- **Where we fit:** our on-prem + Excel/PPT ingestion + abstain-first + approved
  answers map directly onto "factory/technical procedures and manuals where
  data cannot leave and wrong answers are unacceptable." Allganize is the
  closest on-prem competitor; Stockmark/OfficeBot compete on accuracy/multimodal
  but are SaaS-leaning.
- **Where we lose today:** no end-user UI, no SSO, no logos — i.e., we lose on
  "ready to roll out company-wide," not on the core retrieval/answer workflow.

## 8. Market-share evidence / explicit unknowns

- **market_share_unknown** for every player — no reliable third-party % found.
- **Vendor proxy signals only** (not market share): PKSHA "国内シェアNo.1
  AIチャットエージェント" (vendor claim); exaBase "1,000社+"; ChatSense "400社+";
  Helpfeel "900サイト+ / 継続率99%"; Allganize logos (NTT Docomo, JR Kyushu);
  KARAKURI (Sony Network Communications); ELYZA (KDDI group, JR West). Treat as
  traction indicators, not share.

## 9. Top 10 realistic market opportunities for our product

PoC/annual ranges below are **our_proposed_pricing** (anchored to the fact that
JP enterprise RAG is quote-based; not competitor-verified prices). "Win prob"
is a realism estimate, not data.

| # | Industry | Dept/function | Users | Buyer persona | Pain | Why our repo fits | Missing features | PoC range (proposed) | Annual range (proposed) | Sales diff. | Delivery diff. | Security req. | Urgency | Win prob | First-outreach angle |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Manufacturing | 技術/品質/情シス | 500–5,000 | DX推進室/情シス部長 | 技術伝承・手順検索・データ越境不可 | on-prem+Excel/PPT+abstain+approved | UI, SSO | ¥1.5M–¥5M | ¥3M–¥12M | Med | Med | High | Med-High | **High** | 「閉域・誤答ゼロで手順書を即答」 |
| 2 | Manufacturing | 製造現場/安全 | 300–3,000 | 工場長/安全管理 | 現場手順・安全規程の正確な即答 | abstain+approved+on-prem | モバイルUI | ¥1.5M–¥4M | ¥3M–¥9M | Med | Med | High | Med | High | 「現場で間違えないSOPボット」 |
| 3 | IT/SaaS | 開発/SRE | 100–1,000 | VPoE/情シス | 設計・運用ナレッジ属人化 | 多形式+API+citations | UI | ¥1M–¥3M | ¥2M–¥6M | Low | Low | Med | Med | High | 「社内Wiki/PDFを根拠付き検索」 |
| 4 | 全業種 | 情シスヘルプデスク | 300–5,000 | 情シス部長 | 申請手順・IT FAQ工数 | approved-QA+feedback+review | UI | ¥1M–¥3M | ¥2M–¥8M | Med | Low | Med | High | Med-High | 「情シス問い合わせを自己解決化」(PKSHA競合) |
| 5 | BtoB製造/IT | 営業/提案 | 200–2,000 | 営業企画 | 仕様・事例検索の遅さ | PPT/XLSX取込+出典 | UI, CRM連携 | ¥1M–¥4M | ¥2M–¥8M | Med | Med | Med | Med | Med | 「提案資料・事例を出典付きで即引き」 |
| 6 | 建設/物流 | 現場/安全 | 300–3,000 | 安全/品質管理 | 規程・手順の現場参照 | on-prem+abstain | モバイルUI | ¥1.5M–¥4M | ¥3M–¥9M | Med | Med | Med-High | Med | Med | 「現場端末で規程を即答」 |
| 7 | 医療法人 | 院内事務/医療安全 | 500–3,000 | 事務長/医療安全 | 院内規程・閉域・個人情報 | on-prem+abstain | 権限/監査, SSO | ¥2M–¥5M | ¥3M–¥10M | High | Med | High | Med | Med | 「院内規程を閉域で正確に」 |
| 8 | 金融/保険 | コンプラ/事務 | 1,000–10,000 | コンプラ/事務企画 | 規程QA・誤答不可・監査 | approved-QA+abstain | SSO,監査強化,HA | ¥3M–¥8M | ¥6M–¥30M | High | High | Very High | Med | Low-Med | 「規程を言い切りで・監査対応」 |
| 9 | 公共/自治体 | 内部事務 | 500–5,000 | 情報政策課 | 例規・要綱・越境不可 | on-prem+citations | 調達対応,監査,アクセシビリティ | ¥2M–¥6M | 入札¥数M | High | Med | High | Low-Med | Low-Med | 「閉域で例規を根拠付き検索」 |
| 10 | 大手法務/知財 | 法務部 | 50–500 | 法務部長 | 雛形・規程の機密検索 | approved-QA+出典+分離 | 細粒度権限 | ¥1.5M–¥4M | ¥3M–¥9M | High | Med | High | Low | Low-Med | 「機密文書を閉域で正確参照」 |

## 10. Best first target segment (decision)

**#1 Manufacturing internal technical-knowledge / manuals / procedure QA,
delivered as an on-prem (closed-network) single-department pilot.**

Decision-rule check (all satisfied):
- Core workflow already covered by the repo (ingestion, retrieval, approved-QA,
  abstain, citations, tenant isolation, on-prem) — verified in prior readiness
  reports.
- Strong, **web-verified** need for private/on-prem in manufacturing; only
  Allganize among enterprise JP RAG markets on-prem strongly.
- No full SSO/HA/massive scale required for a 1-department pilot.
- Higher willingness to pay than IT/SaaS DIY segment; avoids heavy
  certification (vs public sector/finance) and B2C/mission-critical CS.

## 11. Recommended price range for our first PoC

- **PoC (3 months, on-prem install + document ingestion + KPI measurement):
  ¥1.5M–¥5M** (`our_proposed_pricing`).
- **First-year license (1 department + maintenance): ¥3M–¥12M/year**
  (`our_proposed_pricing`).
- Rationale/anchor: JP enterprise RAG is quote-based, so buyers expect a quote
  in this band; ChatSense's ¥980/月 floor shows the commodity SaaS low-end we
  should **not** compete with on price — we sell on-prem + accuracy, not cheap
  seats. **Not** a competitor-verified figure.

## 12. Recommended one-page positioning

"**閉域（オンプレ）で動く、誤答しない社内文書アシスタント。** クラウドにデータを出さず、
Excel・PowerPoint・PDF の社内手順書/規程を取り込み、**出典付き**で答え、**自信が無ければ
『わからない』と返す**。承認済みQ&Aは言い切りで回答。製造業の技術伝承・現場手順・情シス
問い合わせを、1部門から短期PoCで。" Differentiator vs SaaS-leaning competitors:
no cloud dependency + abstain-first + approved determinism at small-vendor speed.

## 13. Claims we can safely make

- "Runs fully on-premises / closed network; no cloud dependency" [repo evidence].
- "Ingests PDF/DOCX/XLSX/CSV/PPTX with citations" [repo evidence].
- "Abstains ('わからない') instead of hallucinating when evidence is weak"
  [repo evidence].
- "Deterministic approved-Q&A exact-match answers" [repo evidence].
- "Tenant isolation verified across reload and hash-verified restore" [tests].
- "Limited-beta ready under documented conditions" [prior readiness reports].

## 14. Claims we must NOT make yet

- Any specific competitor price as fact (most are quote_required).
- Any market-share % (market_share_unknown).
- "Enterprise/general-production ready," "multi-tenant SaaS," HA/scale, SSO,
  wired monitoring, or managed-cloud parity — not supported by repo evidence.
- Manufacturing accuracy/latency at production scale — unmeasured.
- "More accurate than [named competitor]" — no benchmark performed.

## 15. Source list (accessed 2026-06-13)

- JAPAN AI CHAT — https://japan-ai.co.jp/chat/ ; JAPAN AI AGENT (BOXIL) — https://boxil.jp/service/50441/
- exaBase 生成AI — https://exawizards.com/exabase/gpt/ ; domestic data PR — https://exawizards.com/archives/25896/ ; agent collection — https://exawizards.com/archives/31092/
- Allganize Alli on-prem — https://www.allganize.ai/ja/alli-llm-ops ; doc-answer — https://www.allganize.ai/ja/alli-gpt ; cases — https://blog-ja.allganize.ai/tag/use_case/
- PKSHA AI Helpdesk — https://aisaas.pkshatech.com/ai-helpdesk/ ; PKSHA Chatbot — https://aisaas.pkshatech.com/chatbot/ ; PKSHA FAQ (BOXIL) — https://boxil.jp/service/9194/
- KARAKURI — https://karakuri.ai/ ; cases — https://karakuri.ai/case/ ; Sony NC PR — https://prtimes.jp/main/html/rd/p/000000101.000025663.html
- ChatSense — https://chatsense.jp/ ; pricing PR — https://prtimes.jp/main/html/rd/p/000000208.000073671.html ; 400社 (Nikkei) — https://www.nikkei.com/compass/content/PRTKDB000000117_000073671/preview
- OfficeBot — https://officebot.jp/function/ ; mfg case — https://officebot.jp/interview/miyazakidenshikiki/
- Helpfeel (BOXIL) — https://boxil.jp/service/6943/ ; PRONI — https://saas.imitsu.jp/cate-faq-system/service/2264
- Microsoft Copilot Studio / Azure OpenAI pricing — https://biz.techvan.co.jp/tech-microsoft/blog/contents/azure_openai_service_price.html
- Amazon Bedrock pricing — https://aws.amazon.com/bedrock/pricing/ ; KB floor (third_party) — https://faun.pub/aws-bedrock-knowledge-base-minimum-cost-beware-f2a2dac383d0
- Google Vertex AI Search pricing — https://cloud.google.com/generative-ai-app-builder/pricing
- Dify pricing (JP) — https://dify.ai/jp/pricing ; SI — https://www.sateraito.jp/Dify/index.html
- Stockmark SAT — https://stockmark.co.jp/news/20240625 ; mfg (Nikkei) — https://www.nikkei.com/article/DGXZQOUC2879U0Y5A520C2000000/
- ELYZA Works — https://note.com/elyza/n/n7cd59e020596
- Reference comparison guide — https://exawizards.com/column/article/ai/generative-ai-for-business/

(Source dates: pages accessed 2026-06-13; some vendor figures e.g. "1,000社/400社/900サイト"
are vendor-stated and may post-date or pre-date this access — labeled as proxy.)

## 16. Next recommended implementation prompt

**`prompts/claude/product/prompt033_minimal_enduser_chat_ui_for_onprem_pilot.md`**
— build a minimal end-user chat UI + simple access gating over the existing
`/chat/stream` + `/chat/feedback` with `production_safe`. Rationale from this
research: across every verified competitor, a usable **end-user UI** is table
stakes, and it is our single largest gap for closing a manufacturing on-prem
pilot; the core RAG/answer workflow is already in place. Constraints to carry:
no `.env`, no external calls, no production/default vectorstore mutation,
synthetic data only, no change to guard/cross-encoder/distance/tenant/rate-limit
semantics, no new dependencies, tests + no-secret-leak checks.

Parallel (sell-blocking but separate): SSO/AD, `_build_base_where` `$and` fix
(from Prompt030), and wiring monitoring/alerts.
