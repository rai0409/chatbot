# Prompt038: 蔵伝 / KuraDen — Commercial Chatbot Current-State Gap Analysis

Analysis/report only. Repo-evidence-based. No product runtime behavior changed;
no `.env` read, no secrets, no vectorstore mutation, no Docker/deploy/push.

- Branch: `eval/real-vector-evidence`; HEAD: `9358cf3` (`prompt037 simple enterprise auth bridge`).
- Working tree clean except two known orphans
  (`docs/reports/japan_rag_market_positioning_after_prompt030.md`,
  `prompts/claude/market/`) — left untouched.
- Test collection: **761 tests collected** (78 test files). The full suite was
  **not run** in this analysis prompt; collection + targeted prior-run evidence
  is cited. Prior implementation prompts (034–037) each recorded full-suite
  green at their commit.

Evidence labels: **verified** (code+test), **documented-not-verified**,
**implemented/tested, docs gap**, **missing**, **unclear**.

## 1. Executive judgment

- **Internal demo readiness: READY** — `/chat-ui` (`webapi/static/chat.html` +
  route at `webapi/main.py:934`), synthetic eval data, and green smokes
  (`product_readiness_smoke.sh`, `limited_beta_preflight.sh`) support a local
  demo today.
- **Manufacturing one-department PoC readiness: READY WITH CONDITIONS** — core
  workflow + on-prem + launch pack present; conditions: no
  manufacturing-specific validation pack yet and accuracy on real/representative
  manufacturing documents is **unverified**.
- **Limited external beta readiness: READY WITH CONDITIONS** — per
  `beta_go_no_go_assessment.md` (GO with conditions) and the complete launch
  pack (checklist/rollback/onboarding/preflight).
- **First paid annual contract readiness: PARTIAL** — needs measured PoC
  outcomes, deployment-side SSO, monitoring wired beyond the local checker, and
  real-document accuracy evidence.
- **General production readiness: NOT READY** — single-node local Chroma, no
  HA/SLA, no compliance pack, no scalable multi-tenant SaaS, cross-encoder
  parked.

## 2. Confirmed completed capabilities

| Capability | Evidence path | Type | Commercial significance | Limitation |
| --- | --- | --- | --- | --- |
| Multi-format ingestion (PDF/DOCX/XLSX/CSV/PPTX) | `rag_core/document_converters/*`, `tests/test_document_converters.py` | code+test | Ingests real enterprise file mix | Layout/table fidelity not benchmarked on real docs |
| Japanese chunking / normalization | `rag_core/chunking_ja.py`, `japanese_normalizer.py`; `tests/test_chunking_ja.py`, `test_japanese_normalizer.py` | code+test | Target-market language handling | Domain lexicon coverage unmeasured |
| Retrieval (hybrid + tenant filter) | `rag_core/retrieval.py`; `tests/test_retrieval_ja_integration.py`, `test_retrieval_batching.py` | code+test | Core RAG | Modest synthetic corpus |
| Chroma `$and`-safe where | `rag_core/retrieval.py` (`_to_chroma_where`); `tests/test_chroma_where_builder.py` | code+test (Prompt035) | Multi-tenant vector filter correctness | — |
| Citation generation | `rag_core/source_metadata.py`; `tests/test_no_answer_citations.py`, `test_source_metadata.py` | code+test | Trust / traceability | — |
| Approved Q&A exact-match | `rag_core/approved_qa.py`; `tests/test_approved_qa.py` | code+test | Deterministic, no-hallucination answers | Requires curated approved set |
| Abstain / too-general / no-answer guard | `rag_core/qa.py`; `tests/test_confidence_guard.py`, `test_too_general_guard_redesign.py` | code+test | "Never wrong" selling point | Tuned to current corpus |
| End-user chat UI | `webapi/static/chat.html`, route `webapi/main.py:934`; `tests/test_enduser_chat_ui.py` | code+test (Prompt034) | Non-engineer usability / demo | No SSO UI, no mobile-optimized view |
| Tenant isolation (reload/restore-proven) | `rag_core/retrieval.py`; `tests/test_tenant_isolation.py`, `test_durable_multitenant_persistence.py` | code+test (Prompt030) | Multi-dept safety | Single-node only |
| API-key tenant authorization (fail-closed) | `webapi/api_auth.py`; `tests/test_api_key_tenant_authorization.py`, `test_api_auth.py` | code+test | Gated access | Must be enabled per deploy |
| Rate limiting (default-off, per-process) | `webapi/rate_limit.py`; `tests/test_rate_limit.py` | code+test | Abuse protection | Per-process, not distributed |
| Enterprise auth bridge (default-off) | `webapi/enterprise_auth.py`; `tests/test_enterprise_auth_bridge.py` | code+test (Prompt037) | SSO gateway boundary | Boundary only; IdP external |
| Metrics (JSON + Prometheus) | `webapi/metrics_registry.py`; `tests/test_metrics_observability.py`, `test_observability_export.py` | code+test | Observability | Per-process counters |
| Local alert checker | `webapi/alerting.py`, `scripts/alert_check.py`; `tests/test_monitoring_alerts.py` | code+test (Prompt036) | Operator early-warning | Local snapshot only; not wired to a monitor |
| Deploy smoke / backup / restore | `scripts/deploy_smoke.sh`, `backup.sh`, `restore.sh`; `tests/test_deploy_ops.py` | code+test | On-prem ops baseline | Operator-run, not CI/automated |
| Dry-run onboarding + import manifest | `scripts/onboard_documents_dry_run.py`, `import_manifest.py`; `tests/test_multiformat_onboarding.py` | code+test | Pilot data intake safety | Manual review step |
| Readiness report + limited-beta pack | `eval/production_readiness_report.py`, `docs/reports/limited_beta_*`, `pilot_tenant_onboarding_runbook.md`; `tests/test_production_readiness_report.py` | code+test+docs | Launch governance | Static checks; decision `needs_review` |
| Streaming chat (SSE) | `webapi/main.py` `/chat/stream`; `tests/test_chat_stream.py` | code+test | Responsive UX | — |

## 3. Partially complete capabilities

| Capability | What exists | What is missing | Risk | Recommended next work |
| --- | --- | --- | --- | --- |
| Evaluation coverage | smoke (21), qa_pair (7), multiformat, real_corpus eval cases (`eval/cases/*`) | A **manufacturing procedure** validation set + answer/abstain/error metric harness for the pilot domain | PoC accuracy unknown; over/under-claiming | Synthetic manufacturing procedure docs + measured eval (next prompt) |
| Demo readiness | working UI + synthetic generic sample docs (`eval/cases/sample_docs/*`) | A scripted demo (5 Qs incl. too-general + no-answer + approved-exact) and a manufacturing-flavored sample set | Inconsistent demos; weak narrative | Demo script + manufacturing sample pack |
| Enterprise auth | default-off trusted-proxy bridge + `docs/operations.md` section | Concrete per-IdP reverse-proxy configs; deployment validation behind a real proxy | Integration friction at customer | Customer reverse-proxy/SSO guide (deployment) |
| Monitoring/alerting | local checker + documented thresholds | Scheduled execution + escalation path (cron/systemd timer + notify) | Alerts not actually firing in ops | Document/scheduled checker wiring (non-cloud) |
| Production/default collection handling | onboarding **refuses** prod/default collection | A **safe promotion** workflow (pilot → reviewed → served) | Manual, error-prone go-live | Safe collection promotion workflow |
| Persistence | single-node durability/isolation proven; `_to_chroma_where` now makes `IGNORE_SEARCHABLE` workaround unnecessary | Workaround still present at `tests/test_durable_multitenant_persistence.py:49` | Stale workaround masks real default path | Small cleanup prompt |

## 4. Missing capabilities

| Capability | Why it matters commercially | Minimum acceptable implementation | Order | Severity |
| --- | --- | --- | --- | --- |
| Manufacturing pilot validation pack | Proves accuracy/abstain on the target domain before promising anything | Synthetic procedure/SOP/safety docs + eval cases + measured first-answer/abstain/error report | 1 | **P0** |
| Pilot one-pager + demo script | Required to actually sell/run a PoC | 1-page value/scope/pricing doc + reproducible demo script | 2 | **P0** |
| Real-document evaluation workflow | Customer docs differ from synthetic; need a repeatable measure | A documented, synthetic-first eval procedure that a customer can run on sanitized docs | 3 | **P1** |
| Safe collection promotion workflow | Go-live without touching default/prod collection safely | Script/runbook: pilot collection → review → promote, with guards + tests | 4 | **P1** |
| SSO/AD deployment hardening guide | Enterprise rollout requirement | Per-proxy (nginx/oauth2-proxy) recipes + checklist (no in-app IdP) | 5 | **P1** |
| Scheduled monitoring + escalation (non-cloud) | Alerts must fire unattended | cron/systemd timer running `alert_check.py` + local notify hook | 6 | **P2** |
| HA / 24/7 SLA / compliance pack | General production / regulated buyers | Out of pilot scope; design later | — | **P2** |
| Scalable multi-tenant SaaS + pgvector/Qdrant migration | Beyond single-company on-prem | VectorStore adapter boundary then adapter spike | — | **P2** |
| Cross-encoder rerank promotion | Optional accuracy lift | Local-cache verification + promotion eval (parked: model not cached) | — | **P2** |

## 5. Customer-claim boundary

**Safe to claim for a limited manufacturing PoC:**

- Runs fully **on-premises / closed network, no cloud dependency** (code+test).
- Ingests **PDF/DOCX/XLSX/CSV/PPTX** and answers **with citations**.
- **Abstains ("分かりません") instead of hallucinating** when evidence is weak.
- **Deterministic approved-Q&A** exact-match answers.
- **Tenant isolation verified across reload and hash-verified restore.**
- **API-key tenant authorization** (fail-closed) and an optional **default-off
  enterprise-auth bridge** for a customer SSO gateway.
- **Backup/restore, deploy smoke, local metrics + alert checker** for on-prem ops.
- A **minimal end-user chat UI** for non-engineer pilot users.

**Not safe to claim yet:**

- Any **accuracy/quality number** on real or manufacturing documents (unmeasured).
- **General production / 24×7 SLA / HA**; single-node only.
- **Multi-tenant SaaS** at scale; **compliance** certifications.
- **Full SSO/SAML/OIDC/AD in-app** (only a trusted-proxy boundary exists).
- **Wired/always-on monitoring** (only a local, on-demand checker exists).
- **Superiority over a named competitor** (no benchmark run).

## 6. Recommended build plan

### Stage 1 — sellable demo readiness
- Goal: a crisp, repeatable manufacturing-flavored demo.
- Tasks/prompts: `prompt039` (pilot validation & demo pack) → synthetic
  manufacturing docs + demo script.
- Deliverables: synthetic SOP/procedure/safety sample set; demo script (5 Qs
  incl. too-general + no-answer + approved-exact + citation); measured baseline.
- Done criteria: demo runs end-to-end on `/chat-ui`; eval report produced.
- Dependencies: existing UI, ingestion, eval runner (all present).

### Stage 2 — measurable paid PoC readiness
- Goal: defensible accuracy/abstain/error metrics on the pilot domain + sales kit.
- Tasks/prompts: pilot one-pager + demo script doc; real-document evaluation
  workflow (synthetic-first); safe collection promotion workflow.
- Deliverables: one-pager, eval workflow doc, promotion script+runbook.
- Done criteria: a PoC can be scoped, priced, run, and measured by an operator.
- Dependencies: Stage 1 validation pack.

### Stage 3 — first annual contract readiness
- Goal: dependable single-department production for one customer.
- Tasks/prompts: SSO/AD deployment hardening guide; scheduled monitoring +
  escalation; post-deploy smoke automation; rollback automation.
- Deliverables: per-proxy SSO recipes, scheduled alert checker, automated
  smoke/rollback.
- Done criteria: measured PoC success + ops automation + SSO behind proxy.
- Dependencies: Stage 2.

### Stage 4 — general production hardening
- Goal: multi-tenant scale, HA, compliance.
- Tasks/prompts: VectorStore adapter boundary → pgvector/Qdrant spike; HA design;
  compliance pack; cross-encoder promotion if cached.
- Deliverables: adapter boundary, HA plan, compliance docs.
- Done criteria: scale/HA/compliance evidence.
- Dependencies: Stage 3.

## 7. Next prompt recommendations

1. `prompt039_pilot_validation_and_demo_readiness_pack` — synthetic
   manufacturing procedure docs + measured answer/abstain/error eval + demo
   script for `/chat-ui`. **(immediate; generated by this prompt)**
2. `prompt040_pilot_one_pager_and_sales_kit` — 1-page value/scope/pricing
   one-pager and reproducible demo narrative for a manufacturing PoC.
3. `prompt041_safe_collection_promotion_workflow` — script+runbook to promote a
   reviewed pilot collection to served, never touching the default/prod collection.
4. `prompt042_real_document_evaluation_workflow` — synthetic-first, repeatable
   evaluation procedure a customer can run on sanitized documents.
5. `prompt043_sso_reverse_proxy_deployment_guide` — per-IdP (nginx/oauth2-proxy)
   recipes that set the Prompt037 trusted headers; no in-app IdP.
6. `prompt044_cleanup_obsolete_ignore_searchable_workaround` — remove the now
   unnecessary `IGNORE_SEARCHABLE=True` workaround in the durable-persistence
   test (Prompt035 made it unnecessary), keeping isolation coverage.

## 8. Recommended immediate next prompt

**`prompt039_pilot_validation_and_demo_readiness_pack`** — generated at
`prompts/claude/product/prompt039_pilot_validation_and_demo_readiness_pack.md`.

Why: it is the single highest-leverage, lowest-risk step. Every readiness
judgment above is gated by one unknown — **accuracy/abstain behavior on the
target manufacturing domain** — and by the absence of a repeatable demo. A
synthetic manufacturing validation pack + measured eval + demo script directly
unblocks PoC claims (Section 5), turns "READY WITH CONDITIONS" into a measured
position, and reuses existing ingestion/eval/UI with synthetic data only (no
new runtime risk, no real customer data).

## Evidence checked

git HEAD/tags/status; `docs/reports/*` (incl. Prompt034–037 reports + beta
pack); `artifacts/{market,readiness}/*`; `webapi/{main,api_auth,enterprise_auth,
rate_limit,metrics_registry,alerting}.py` and `webapi/static/chat.html`;
`rag_core/{retrieval,qa,approved_qa,chunking_ja,source_metadata}.py` and
`document_converters/*`; `scripts/*` (smoke/backup/restore/preflight/alert/
onboard/import_manifest/persistence); `tests/` (78 files, 761 collected);
`eval/cases/*` incl. `sample_docs/*`.

## Checks run (lightweight; no full suite, no Docker)

- `git log/tag/status`, report/script/test existence checks, targeted greps.
- `pytest --collect-only` → 761 collected. Full suite **not run** in this prompt.
- Confirmed `IGNORE_SEARCHABLE=True` workaround still present at
  `tests/test_durable_multitenant_persistence.py:49`.
- Readiness report decision = `needs_review`, blockers `[]`.
