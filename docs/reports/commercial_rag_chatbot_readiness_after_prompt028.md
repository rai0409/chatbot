# Commercial RAG Chatbot Readiness — After Prompt028

Analysis and planning report (Prompt029). No runtime behavior was changed;
only this report, its JSON summary, and one next-implementation prompt were
produced. All claims are backed by repo evidence at the commit below.

- Branch: `eval/real-vector-evidence`
- HEAD: `97b76a7` (`prompt028 chat tenant product profile runtime wiring`)
- Working tree: clean except the untracked analysis prompt (`prompt029_*.md`).
- Product framing: Japanese enterprise internal-document RAG chatbot.

## 1. Executive summary

The repository is a **credible limited-beta-ready** internal-document RAG
chatbot with strong security, tenant-authorization, ingestion, and
operations-documentation foundations, backed by **714 passing tests**, a green
product-readiness smoke and limited-beta preflight, and passing synthetic
evals (smoke 21/21, qa_pair 7/7). The static production-readiness report
decision is **`needs_review`** with **no critical code blockers**.

It is **not yet ready for general production**. The dominant gap is durability
and operational automation: persistence is single-node local Chroma and there
is **no test proving tenant isolation survives a reload or backup/restore**;
monitoring/alerting is documented but not wired; rollback and post-deploy
smoke are manual. Per-tenant `/chat` profile wiring (Prompt028) is real but its
only retrieval effect today is a candidate-limit clamp.

Recommended next implementation: **durable multi-tenant persistence
verification** — prove, with synthetic data on a non-production collection,
that tenant isolation and stored data survive reload and backup/restore. This
matches the decision rule (durable persistence + restart/restore isolation is
currently unproven).

## 2. Product capability assessment

Legend: ready / partial / not ready / n/a-yet.

| Category | Status | Evidence files | Proving tests/evals/scripts | Commercial value | Remaining risk | Path to production-grade |
| --- | --- | --- | --- | --- | --- | --- |
| Document ingestion | ready | `scripts/ingest_canonical_jsonl.py`, `scripts/convert_document_to_canonical_jsonl.py` | `test_document_converters.py` | High | Source-quality variance | Scale + format edge-case corpus |
| Multi-format (PDF/DOCX/XLSX/CSV/PPTX) | ready | `rag_core/document_converters/*` | `test_document_converters.py` (36 collected w/ ja) | High | Complex layouts/tables | Layout/table fidelity tests on real docs |
| Canonical chunk generation | ready | `rag_core/chunking_ja.py`, converters | `test_chunking_ja.py` | High | Chunk-size tuning per corpus | Corpus-specific tuning |
| FAQ / Q&A pair extraction | ready | `scripts/approved_qa_to_pair_chunks.py` | `test_qa_pair_chunks.py`; qa_pair eval 7/7 | High | Small curated set | Larger curated business set |
| Retrieval quality | partial | `rag_core/retrieval.py`, `rag_core/qa.py` | smoke eval 21/21; `test_retrieval_*` | High | Modest synthetic corpus; CE parked | Bigger Japanese business eval set |
| Citation quality | ready | `rag_core/source_metadata.py` | `test_source_metadata.py`, `test_no_answer_citations.py` | High | Source metadata variance | Real-doc citation audits |
| Approved Q&A exact-match route | ready | `rag_core/approved_qa.py` | `test_approved_qa*.py`; deploy smoke | High | Needs curated approved set | Per-tenant approved sets |
| Abstain / hallucination guard | ready | `rag_core/qa.py`, `eval/guard_distance_calibration.py`, `eval/too_general_guard_analysis.py` | `test_too_general_guard_redesign.py`, `test_guard_distance_calibration.py`, `test_confidence_guard.py` | Very high | Tuned to current corpus | Per-corpus recalibration workflow |
| Japanese query handling | ready | `rag_core/japanese_normalizer.py`, `ja_text.py`, `question_normalization.py` | `test_japanese_normalizer.py`, `test_ja_text.py`, `test_retrieval_ja_integration.py` | High (target market) | Domain-term coverage | Domain lexicon expansion |
| Tenant isolation | partial | `rag_core/retrieval.py`, `webapi/api_auth.py` | `test_tenant_isolation.py` (query-level) | Very high | Not proven across reload/restore | Reload/restore isolation proof (next prompt) |
| API key tenant authorization | ready | `webapi/api_auth.py` | `test_api_key_tenant_authorization.py` | Very high | Must be enabled per deploy | n/a (fail-closed, tested) |
| Admin / search-debug controls | ready | `webapi/admin_auth.py`, `webapi/api_auth.py` | `test_admin_auth.py`, `test_api_auth.py` | Medium | Deploy-time config | n/a |
| Rate limiting | partial | `webapi/rate_limit.py` | `test_rate_limit.py` | Medium | Per-process, not distributed | Shared/distributed limiter |
| Per-tenant product profile runtime wiring | partial | `webapi/main.py`, `rag_core/tenant_profile.py` | `test_chat_tenant_product_profile_runtime.py` (14) | Medium | Effect limited to top_k clamp + audit | Richer per-tenant policy effects |
| Metrics & Prometheus export | ready | `webapi/metrics_registry.py`, `webapi/main.py` | `test_metrics_observability.py`, `test_observability_export.py` | Medium | Per-process counters | Cross-worker aggregation in scraper |
| Alert threshold documentation | partial | `docs/operations.md` | n/a (docs) | Medium | Documented, not wired | Wire to a live monitor |
| Audit logging & retention | ready | `rag_core/audit_log.py`, `docs/operations.md` | audit paths in suite | High | Retention is policy guidance | Enforced retention automation |
| Deploy smoke | ready | `scripts/deploy_smoke.sh` | `test_deploy_ops.py` | Medium | Operator-run | CI/post-deploy automation |
| Backup & restore | ready | `scripts/backup.sh`, `scripts/restore.sh` | `test_deploy_ops.py` (hash-verified) | High | Manual cadence; isolation-after-restore unproven | Restore-isolation proof + schedule |
| Dry-run onboarding & import manifest | ready | `scripts/onboard_documents_dry_run.py`, `scripts/import_manifest.py` | `test_multiformat_onboarding.py` | High | Manual review | Guided/automated review |
| Limited beta launch pack | ready | `docs/reports/limited_beta_launch_checklist.md` | `limited_beta_preflight.sh` (green) | High | Manual checklist | n/a (process) |
| Rollback readiness | partial | `docs/reports/limited_beta_rollback_runbook.md` | preflight presence | High | Manual procedure | Scripted rollback |
| Pilot tenant onboarding readiness | ready | `docs/reports/pilot_tenant_onboarding_runbook.md` | `test_multiformat_onboarding.py` | High | Manual | Automated onboarding flow |
| Production readiness reporting | ready | `eval/production_readiness_report.py`, `artifacts/readiness/*` | `test_production_readiness_report.py` | Medium | Static checks only | Live deployment checks |
| Durable multi-tenant persistence | not ready | `rag_core/store.py` (`PersistentClient`) | none (gap) | Very high | Single-node; reload/restore isolation unproven | Next prompt + managed/HA story |
| Customer-facing UX / integration surface | partial | `webapi/static/product_preview.html` | `test_product_preview_page.py` | High (sales) | Preview UI only | Productized UX/integration |

## 3. Commercial readiness scores (0-100, evidence-based, not inflated)

| Dimension | Score | Reason | Strongest evidence | Biggest missing item |
| --- | --- | --- | --- | --- |
| Limited external beta readiness | 80 | GO-with-conditions; full launch pack + green preflight | `limited_beta_preflight.sh` exit 0; 714 tests | Live deploy not yet exercised |
| General production readiness | 45 | Durability/monitoring/rollback not automated | `needs_review`, blockers [] but warnings remain | Durable persistence + isolation proof |
| RAG answer quality | 60 | Guard + approved route + qa-pair solid; corpus modest | smoke 21/21, qa_pair 7/7 | Larger realistic JA business eval set |
| Data onboarding | 75 | Multiformat + dry-run + manifest dup/tenant/collision checks | `test_multiformat_onboarding.py` | Automated review + scale |
| Security | 78 | API auth + fail-closed tenant authz + admin gating + secrets docs | `test_api_key_tenant_authorization.py` | External secret store; per-process limiter |
| Tenant isolation | 70 | Query-level isolation tested + fail-closed authz | `test_tenant_isolation.py` | Reload/restore isolation proof |
| Operations | 68 | Deploy smoke + backup/restore + runbooks | `test_deploy_ops.py` | Automation (post-deploy, rollback) |
| Observability | 65 | JSON+Prometheus metrics + 429/auth counters | `test_observability_export.py` | Alerts wired to a monitor |
| Maintainability | 82 | 714 tests, clear modules, tagged analysis trail | full suite green | n/a (sustain discipline) |
| Sales/demo | 60 | Product-preview UI + deterministic approved answers | `test_product_preview_page.py` | Polished customer UX + live demo |

## 4. Real commercial use-case fit

| Use case | Verdict | Why |
| --- | --- | --- |
| Internal FAQ bot | ready now (single pilot) | Approved exact-match + qa-pair + guard; deterministic, tested |
| Internal policy/manual chatbot | beta with conditions | Multiformat ingestion + retrieval + guard work; needs corpus review + safe profile |
| PDF/DOCX/XLSX/PPTX document Q&A | beta with conditions | Converters ready; real-doc fidelity needs validation per corpus |
| Approved-answer support bot | ready now (single pilot) | Deterministic approved route is strongest path |
| Small pilot for one/few companies | beta with conditions | Full limited-beta pack + conditions in launch checklist |
| Multi-tenant SaaS | not yet | Durable persistence + isolation-after-restore + management workflow missing |
| Regulated enterprise production | not yet | No managed persistence, wired monitoring, automated rollback, or external secret store |
| Mission-critical customer support | not yet | Single-node, manual ops, modest eval coverage |

## 5. Limited beta readiness decision

**GO with conditions** (limited external beta only; not general production).
Conditions are the 15 mandatory items in
`docs/reports/limited_beta_launch_checklist.md` and the GO-with-conditions set
in `docs/reports/beta_go_no_go_assessment.md`: `production_safe` profile,
API auth + per-tenant keys + `API_AUTH_TENANT_MAP`, `RATE_LIMIT_ENABLED`,
`ADMIN_AUTH_ENABLED`, `SEARCH_DEBUG_ENABLED=false`, TLS, named pilot allowlist,
human-in-the-loop review, knowledge-manifest review, backup before launch,
restore rehearsal, live deploy smoke, alert wiring, and a named rollback owner.
Optionally enable `CHAT_USE_TENANT_PROFILE=true` (Prompt028) with a complete
tenant map.

## 6. General production blockers (priority order)

| # | Blocker | Risk | Blocks beta? | Blocks gen-prod? | Recommended prompt | Complexity | Order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Durable persistence + reload/restore tenant-isolation proof | High | No | Yes | prompt030 durable multitenant persistence verification | Medium | 1st |
| 2 | Production/default vectorstore safety under managed ops | High | No | Yes | guarded prod ingest + collection promotion | Medium | 2nd |
| 3 | Actual monitoring/alert wiring | Medium | No (condition for beta) | Yes | wire metrics scrape + alerts | Medium | 3rd |
| 4 | Real customer data onboarding controls (PII/DPA/deletion) | High | No (synthetic-only beta) | Yes | data-handling + deletion controls | Large | 4th |
| 5 | Post-deploy smoke automation | Medium | No | Yes | automate post-deploy smoke in pipeline | Small | 5th |
| 6 | Automated rollback path | Medium | No | Yes | script revert+restore+smoke | Small | 6th |
| 7 | Richer per-tenant policy effects beyond top_k clamp | Medium | No | Partial | extend chat profile effects safely | Medium | 7th |
| 8 | Multi-tenant admin/management workflow | Medium | No | Yes | tenant admin/management surface | Large | 8th |
| 9 | External secret store integration | Medium | No | Partial | optional managed secret store | Medium | later |
| 10 | Distributed rate limiting | Medium | No | Partial | shared-store limiter | Medium | later |
| 11 | Customer-facing UX / integration surface | Medium | No | Partial (sales) | productized UX/integration | Large | later |
| 12 | Larger realistic JA business eval coverage | Medium | No | Yes (quality) | expand eval corpus | Medium | parallel |
| 13 | Latency/cost under load | Medium | No | Yes | load + cost measurement | Medium | parallel |
| 14 | Cross-encoder rerank promotion | Low | No | No | parked (model not cached) | Small | parked |

## 7. Ordered next steps (first/second/third)

1. **Durable multi-tenant persistence verification** (prompt030). Reason:
   highest-risk gen-prod blocker and the decision rule's first trigger;
   tenant isolation across reload/restore is currently unproven. Low product
   risk (verification + synthetic data on a non-production collection).
2. **Production/default vectorstore safety + guarded promotion**. Reason: once
   durability is proven, the next durability concern is safe managed ingest and
   collection promotion without touching the default collection.
3. **Wire monitoring/alerting** to the documented thresholds. Reason: it is a
   limited-beta condition and a gen-prod requirement; turns documented
   thresholds into live protection.

## 8. Recommended next implementation prompt

`prompts/claude/product/prompt030_durable_multitenant_persistence_verification.md`
(generated by this analysis). It verifies, with synthetic data on a
non-production collection, that records and the embedding fingerprint survive a
client reload and a hash-verified backup/restore, and that tenant isolation
(alpha-only / beta-only) holds after both — without mutating the
production/default vectorstore or changing retrieval/guard behavior.

## 9. Do-not-do-yet list

- Do not claim general production or multi-tenant SaaS readiness.
- Do not onboard real customer data (synthetic/sanitized only).
- Do not promote cross-encoder rerank (model not cached; parked).
- Do not swap in a new storage backend in the next prompt (verify first).
- Do not change distance thresholds, the too_general guard, cross-encoder
  settings, tenant authorization/isolation semantics, or rate-limiter
  semantics.
- Do not wire external secret stores or distributed rate limiting yet.

## 10. Risk register

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Tenant data bleed after restart/restore (unproven) | Medium | High | prompt030 reload/restore isolation proof |
| Beta launched without all conditions | Medium | High | enforce launch checklist + preflight sign-off |
| Single-node store loss | Low | High | pre-launch backup + verified restore rehearsal |
| Alert gaps (documented, not wired) | Medium | Medium | wire alerting before launch (condition 14) |
| Quality regression on real JA docs | Medium | Medium | expand eval corpus; abstain-first guard |
| Per-process limiter under-protects multi-worker | Medium | Medium | size per worker; proxy-level limit |
| Real data used prematurely | Low | High | synthetic-only mandate in runbooks |

## 11. Exact commands used

    git status --short
    git log --oneline --decorate -20
    git tag --list
    .venv/bin/python -m pytest --collect-only -q
    .venv/bin/python -m pytest tests/test_chat_tenant_product_profile_runtime.py tests/test_api_key_tenant_authorization.py tests/test_tenant_isolation.py -q
    .venv/bin/python -m pytest tests/test_rate_limit.py tests/test_metrics_observability.py tests/test_observability_export.py tests/test_production_readiness_report.py -q
    .venv/bin/python -m pytest -q
    bash scripts/product_readiness_smoke.sh
    scripts/limited_beta_preflight.sh
    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt029_smoke_check.json
    PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt029_qa_pair_check.json
    # documented, not run automatically (docker present):
    scripts/limited_beta_preflight.sh --with-docker-smoke

## 12. Unknowns and assumptions

- Live deployment behavior (TLS, proxy, live `/health` and `/metrics`) is not
  exercised here; all checks are repo-local with synthetic data.
- Tenant isolation is verified at the query layer; behavior across a real
  process restart or restore is **assumed**, not yet proven — this is exactly
  what prompt030 closes.
- Eval corpus is modest and synthetic; production-scale Japanese-business
  accuracy, latency, and cost are not established.
- Cross-encoder model availability is unknown (not cached by policy); rerank
  remains off.
- Scores are judgment calibrated to repo evidence and prior reports
  (Prompt025/027); they are directional, not externally audited.
