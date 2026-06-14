# Prompt039: Pilot Validation & Demo Readiness Pack (Synthetic Manufacturing)

Implementation report. Adds a synthetic, clearly-fictional manufacturing
procedure corpus, a **measured** answer/abstain/no-answer eval, a reproducible
`/chat-ui` demo script, and a buyer one-pager. Additive data + eval + docs only;
**no product runtime behavior, retrieval/guard/auth/rate-limit semantics, or UI
changed**; no new dependencies; synthetic data only.

## Files added

- `scripts/build_manufacturing_pilot_pack.py` — reproducible builder for the
  corpus + cases (no network, no `.env`, no Docker).
- `eval/cases/manufacturing_pilot/manufacturing_chunks.jsonl` — 8 canonical
  chunks: procedure (起動/段取り替え), quality (外観検査合否), safety (保護具/ロック
  アウト), troubleshooting (アラームE12), IT helpdesk (VPN申請), and one
  **approved-Q&A pair**. Clearly fictional (架空精機 / 装置X).
- `eval/cases/manufacturing_pilot/manufacturing_cases.jsonl` — 11 Japanese eval
  cases.
- `tests/test_pilot_validation_pack.py` — corpus/case/demo validation + an
  end-to-end eval run asserting abstain/no-answer behavior.
- `docs/reports/pilot_demo_script_manufacturing.md` — reproducible demo script.
- `docs/sales/kuraden_pilot_one_pager_prompt039.md` — buyer one-pager (honest).
- This report.

## Measured eval results (quoted from the actual run)

Command (default eval = real keyword retrieval, **generation stubbed**):
`python -m eval.runner --cases .../manufacturing_cases.jsonl --chunks-jsonl
.../manufacturing_chunks.jsonl` → **`Summary: passed=11/11 failed=0`**.

By category (all behaved as designed):
- **Answerable + grounded citation (7) + approved-Q&A exact match (1) = 8/8:**
  each retrieved the **correct top-1 source chunk** with `guard=None,
  fallback=False` → **first-answer rate on answerable = 8/8 (100%)**, each with a
  source citation.
- **Must-abstain / too_general (2): 2/2** returned `guard=too_general,
  fallback=True` ("関連情報が見つかりませんでした") → **correct-abstain rate = 2/2
  (100%)**.
- **Out-of-corpus / no-answer (1): 1/1** fell back (no fabricated answer) →
  **correct no-answer = 1/1 (100%)**.

No regression in existing safety evals: smoke **21/21**, qa_pair **7/7**.

## Metrics explicitly NOT measured (and why)

- **Real LLM answer-text quality / phrasing**: the eval stubs generation
  ("根拠に基づく回答です" placeholder), so it measures **retrieval correctness +
  abstain/no-answer behavior**, not the wording quality of a real generated
  answer. (Avoids model downloads / network / `.env` keys.)
- **Real-vector retrieval accuracy**: the default eval uses keyword/BM25 with
  vectors stubbed; real-embedding ranking is not exercised here.
- **Accuracy on real customer documents**: not measurable without real (non-
  synthetic) data — out of scope and prohibited.
- **Latency / cost under load**: not measured.

These are honest limitations; the pack measures the **safety-critical** behaviors
(grounded retrieval-with-citation, abstain-first, no-answer-on-out-of-corpus) on
the target domain, deterministically and repeatably.

## Synthetic-data / no-secret safety result

- Corpus/cases/demo are clearly fictional (`架空精機` / `装置X`); a test asserts
  the fictional marker is present and that no secret-like token or real-document
  marker appears (`sk-`, `Bearer `, `X-Api-Key`, `*_SECRET`, `*_TOKEN`,
  `password`, real-doc id `58887_95105`). Eval runs operate on the provided
  `--chunks-jsonl`; the production/default vectorstore is never ingested or
  mutated. `runs/` eval outputs are gitignored and not committed.

## Verification results

- `tests/test_pilot_validation_pack.py`: **7 passed**.
- Regression (`test_enduser_chat_ui`, `test_chroma_where_builder`,
  `test_monitoring_alerts`, `test_enterprise_auth_bridge`, `test_tenant_isolation`):
  **50 passed**.
- Full suite: **839 passed, 0 failed** (+7). `product_readiness_smoke.sh` exit 0;
  `limited_beta_preflight.sh` exit 0. Manufacturing eval **11/11**; smoke 21/21;
  qa_pair 7/7. **Full suite WAS run.**

## Safe to claim now (after this pack) vs still not

**Now safe to claim (on the synthetic pilot domain):** on grounded manufacturing
procedure/quality/safety/troubleshooting/FAQ questions the system retrieves the
correct source and cites it; on vague (too-general) and out-of-corpus questions
it **abstains / returns no-answer instead of fabricating** — demonstrated by a
repeatable 11/11 measured eval.

**Still NOT safe to claim:** accuracy numbers on the customer's **real**
documents (measured during the PoC, not in advance); real-LLM answer-text
quality; general production / HA / 24×7 SLA / compliance; end-to-end SSO against
a real IdP (mock-tested only). No production-readiness or guaranteed-accuracy
claim is made here.

## Confirmation of non-changes

No change to product runtime, retrieval thresholds, the `too_general` guard,
cross-encoder settings, tenant authorization/isolation, API-key/OIDC/RBAC/
rate-limiter semantics, `production_safe`, or the Prompt034 UI / Prompt035 Chroma
where / Prompt036 monitoring / Prompt037 enterprise-auth behavior. No new
dependencies. Orphans untouched.

## Final judgment: PASS

## Next recommendation

The P0 commercial blocker (measured manufacturing-domain abstain/answer behavior
+ a reproducible demo) is now closed on synthetic data. Next: (1) run a real
on-prem PoC on the customer's **sanitized** documents to measure real accuracy;
(2) safe collection-promotion workflow; (3) end-to-end SSO validation against the
customer's IdP. Readiness after Prompt039: manufacturing one-department PoC
remains **READY WITH CONDITIONS**, now with a **measured** synthetic-domain
baseline rather than an unmeasured assumption.
