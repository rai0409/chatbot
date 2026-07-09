# Prompt020: Phase 5-E Cross-Encoder Promotion Eval

Renumbered from the planned prompt018_phase5e slot: prompt018 was taken by the multi-format ingestion foundation (tag prompt018-multiformat-ingestion-foundation) and prompt019 by the onboarding prompt, after Prompt017 was authored.

You are working in:

/home/rai/chatbot

## Goal

Decide, with measured evidence, whether the optional cross-encoder rerank stage (Prompt014, default off) earns promotion: compare hybrid_rerank vs hybrid_rerank_ce on the calibrated real-vector setup through eval/rerank_promotion_gate.py. Promotion is a gate decision — no default flips without passing it.

Context from Prompt017 (runs/eval/prompt017_realvector_before.json, runs/eval/guard_distance_calibration.json):

- gold hit 41/41 on the real corpus in real-vector mode, but gold best rank is 2-7 (median ~5) — fused/heuristic ranking leaves clear headroom for a semantic reranker.
- Guard thresholds remain at defaults (distributions overlap; distance cannot separate answerable from abstain on this corpus).
- Dedicated stamped collection exists: guard_calibration_eval_v1 (30 chunks, local paraphrase-multilingual-MiniLM-L12-v2).

## Execution mode

Proceed autonomously. Stop only for: destructive operations, user-data deletion, secrets/.env access, remote operations, ambiguous missing targets, model downloads (if the cross-encoder model is not already cached locally, stop and report PARTIAL — do not download), or unrecoverable verification failure after one bounded fix attempt.

## Preconditions to verify before implementing

- Prompt017 is complete: eval/guard_distance_calibration.py exists, runs/eval/guard_distance_calibration.json and prompt017_realvector_before.json exist, tests/test_guard_distance_calibration.py passes.
- Collection guard_calibration_eval_v1 exists with a verified embedding fingerprint stamp.
- CROSS_ENCODER_MODEL (default BAAI/bge-reranker-v2-m3) is available in the local HF cache. Check offline (HF_HUB_OFFLINE=1). If absent, report PARTIAL with the exact model name the human must pre-cache, and stop.

## Scope

1. Run the comparison: eval over eval/cases/real_corpus_cases.jsonl + real_corpus_chunks.jsonl with --real-vector against guard_calibration_eval_v1, once per mode (hybrid_rerank, hybrid_rerank_ce), via eval/runner.py --modes if supported or two runs otherwise. Save both result JSONs under runs/eval/.

2. Feed both into eval/rerank_promotion_gate.py (inspect its expected input format first; adapt the invocation, not the gate). Save the gate verdict JSON.

3. Decision rules:

- PROMOTE only if the gate passes AND gold_chunk_best_rank improves (median and worst-case) AND no abstain-case regression.
- On PROMOTE: enable the cross-encoder ONLY in the pilot_high_accuracy profile (configs/ or rag_core/product_profile.py — inspect how profiles gate features). production_safe stays unchanged. Document the change.
- On NO-PROMOTE: leave everything unchanged; write the verdict report with reasons.

4. Targeted tests only: profile gating test if a profile change is made (pilot_high_accuracy enables, production_safe does not); no tests requiring model downloads (fake CE pattern from tests/test_cross_encoder_rerank.py).

## Explicit non-goals

- changing CROSS_ENCODER_RERANK_ENABLED global default (stays false)
- guard threshold changes (Prompt017 settled: defaults stay)
- too_general guard redesign (known dominant false-abstain cause — separate future prompt)
- retrieval/API/auth/tenant changes, new dependencies, model downloads

## Verification

Targeted tests first, then:

python -m pytest --collect-only -q

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt020_smoke_check.json

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt020_qa_pair_check.json

python -m pytest tests/test_cross_encoder_rerank.py tests/test_api_key_tenant_authorization.py tests/test_tenant_isolation.py -q

If safe: scripts/product_readiness_smoke.sh.

## Required final output

1. Preconditions (incl. CE model cache check result)
2. Comparison methodology and runs
3. Measured evidence (per-mode gold rank distribution, gate verdict, latency note if measured)
4. Decision: PROMOTE (profile-gated) / NO-PROMOTE, with reasons
5. Verification results
6. Git diff summary
7. Final judgment: PASS / PARTIAL / FAIL, and recommended next prompt (suggested: too_general guard redesign measured against the same corpus, OR Prompt019 onboarding if accuracy track should pause)
8. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt021_phase5f_too_general_guard_redesign.md covering measurement-driven redesign of the too_general guard (the dominant false-abstain cause: 16/19 in real-vector mode), gated on the existing eval corpora. Do not execute it.
