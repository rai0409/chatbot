# Prompt017: Phase 5-D Real-Vector Guard Threshold Calibration

You are working in:

/home/rai/chatbot

## Goal

Calibrate the confidence-guard distance thresholds on the stamped real-vector collection using the expanded eval corpus from Prompt016, with measured evidence.

The Prompt016 keyword-only baseline (runs/eval/prompt016_real_corpus_baseline.json) showed retrieval ranks gold #1 in 41/41 gold-labeled cases while the guard over-abstains: 18/41 answerable cases fell back (2/22 stored-question, 11/13 answer-term, 5/6 paraphrase) and 2/10 abstain cases were answered. Vector evidence is absent in that mode, so the guard ran on keyword signals alone — the vector-distance thresholds (RAG_MAX_DISTANCE / soft thresholds) have never been calibrated against real distances.

## Execution mode

Proceed autonomously.

Do not ask for human confirmation for ordinary local edits, targeted tests, smoke checks, or local verification.

Stop only if one of the following occurs:

- A destructive operation would be required.
- User data would be deleted.
- .env, secrets, tokens, API keys, or private credentials would need to be read, printed, changed, or inferred.
- A remote push, force push, or remote deployment would be required.
- The target files cannot be found and the correct location is ambiguous.
- Verification fails in a way that cannot be safely classified after one bounded fix attempt.

If verification fails because of your changes, perform one bounded fix attempt, rerun targeted verification, and report final status.

## Preconditions to verify before implementing

- Prompt016 is complete: eval/cases/real_corpus_cases.jsonl (51 cases) and eval/cases/real_corpus_chunks.jsonl (30 chunks) exist; tests/test_build_eval_cases.py passes.
- A local vectorstore exists. Check whether a collection stamped with the active embedding fingerprint covers the real corpus chunks. If no stamped collection matches, ingest eval/cases/real_corpus_chunks.jsonl into a DEDICATED eval collection (never the production collection) using the existing ingest script with an explicit collection name, and verify the fingerprint stamp.
- Local embedding model is available (EMBED_PROVIDER=local); no network downloads — if the model is not already cached locally, stop and report PARTIAL rather than downloading.

## Scope

Implement only the following:

1. A measurement script eval/guard_distance_calibration.py:

- Runs the real-corpus cases through eval.runner with --real-vector against the dedicated eval collection.
- Collects per-case best_vector_distance (and guard evidence fields already in the trace) for answerable vs abstain-labeled cases.
- Outputs a distance-distribution report (JSON + Markdown) under runs/eval/: percentiles per label class, overlap region, and false-answer / false-abstain rates as a function of candidate thresholds (sweep, e.g. 0.50-1.00 step 0.05 plus the current defaults).
- Recommends RAG_MAX_DISTANCE (hard) and RAG_SOFT_DIST_* values with the measured trade-off stated. No threshold is changed automatically.

2. Apply the recommended thresholds ONLY if the measured evidence is unambiguous (clear separation with both false rates at or below the current baseline); otherwise report the recommendation and leave defaults unchanged. Any change must be env-default changes in config.py only, with before/after eval runs saved.

3. Targeted tests only:

- threshold sweep math on synthetic distance samples (no model, no vectorstore)
- report writer produces stable JSON schema
- no behavior change when calibration artifacts are absent

## Explicit non-goals

- cross-encoder promotion (separate gate)
- changing retrieval, rerank, API, auth, tenant behavior
- production vectorstore writes
- new dependencies; no model downloads

## Constraints

- No new required dependencies.
- Do not read or print .env.
- Deterministic smokes must remain green: smoke 21/21, qa_pair 7/7.
- Unit tests must not require network, model downloads, or an OpenAI API key (the real-vector measurement run itself may use the locally cached embedding model).
- Keep changes minimal and localized.

## Verification

Run targeted tests first, then:

python -m pytest --collect-only

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt017_smoke_check.json

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt017_qa_pair_check.json

Run the calibration measurement and save the report. Re-run the real-corpus baseline in the same mode before/after any threshold change and compare honestly.

If available and safe, run scripts/product_readiness_smoke.sh and the security suites.

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, git status, vectorstore/collection state, embedding model availability)
2. Implementation summary (files added/changed, measurement methodology, whether thresholds were changed and why)
3. Measured evidence (distance distributions, false-answer/false-abstain rates at current and recommended thresholds)
4. Verification results (targeted tests, collect-only, both smokes, before/after comparison if thresholds changed, skipped checks and why)
5. Git diff summary
6. Final judgment: PASS / PARTIAL / FAIL, and whether the cross-encoder promotion eval (hybrid_rerank vs hybrid_rerank_ce on the same collection) is ready as the next prompt.
7. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt018_phase5e_cross_encoder_promotion_eval.md covering the hybrid_rerank vs hybrid_rerank_ce comparison on the calibrated real-vector setup through eval/rerank_promotion_gate.py. Do not execute Prompt018 in this run.
