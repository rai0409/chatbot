# Prompt016: Phase 5-C Eval Corpus Expansion Toward Real-Document Scale

You are working in:

/home/rai/chatbot

## Goal

Expand the labeled eval corpus toward 100+ real-document-derived cases covering retrieval, abstain, and approved-QA regression. This is the prerequisite for the two pending evidence gates: real-vector guard threshold calibration and the cross-encoder (hybrid_rerank vs hybrid_rerank_ce) promotion decision.

The current labeled coverage is too small to support either gate: ~25 retrieval comparison cases, 21 deterministic smoke cases, 7 qa_pair cases, and one real PDF (104 canonical chunks, 22 approved Q&A records).

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

- Prompt015 is complete: scripts/approved_qa_to_pair_chunks.py exists, eval/cases/qa_pair_cases.jsonl passes 7/7, tests/test_qa_pair_chunks.py passes.
- The real corpus artifacts exist locally: data/approved_qa/default.jsonl (22 records) and the canonical chunks JSONL referenced by config CHUNKS_JSONL_PATH.

## Scope

Implement only the following:

1. A case-generation helper script scripts/build_eval_cases_from_approved_qa.py:

- Input: an approved-QA JSONL file plus the matching qa_pair chunk JSONL (from scripts/approved_qa_to_pair_chunks.py).
- Output: eval-runner case JSONL where each approved record yields:
  - one stored-question-wording retrieval case (gold_chunk_ids = the qa_pair chunk id)
  - one answer-term case when a salient answer-only term can be extracted heuristically (skip with a counted reason when not)
- Deterministic output (no randomness, stable case_ids derived from qa_id).
- Generated cases carry gold labels only (gold_chunk_ids/gold_doc_ids); do not auto-write expected_guard_reason/expected_used_fallback — strict expectations stay hand-authored.

2. A real-corpus case file eval/cases/real_corpus_cases.jsonl:

- Generate from data/approved_qa/default.jsonl + its qa_pair chunks (expected ~22 stored-question + up to ~22 answer-term cases).
- Hand-add at least 10 abstain-labeled cases (questions plausibly asked but not answerable from the corpus; should_abstain/expected_abstain true).
- Hand-add at least 5 paraphrase cases with gold labels.
- Target: 50+ cases in this file; combined labeled cases across eval/cases/ should approach or exceed 100.

3. A baseline measurement run (deterministic keyword-only mode):

- Run eval.runner over the new case file with the qa_pair chunk corpus merged with the existing canonical chunks for that PDF.
- Record the baseline summary JSON under runs/eval/ (gold hit rates, abstain accuracy) — this becomes the reference point for guard calibration and cross-encoder comparison.

4. Targeted tests only:

- generator: one approved record → expected case rows with stable case_ids and correct gold_chunk_ids
- generator: answer-term extraction skip path is counted and deterministic
- generated case file loads through eval.runner load_cases without errors
- duplicate case_id detection across generated + hand-authored sections

## Explicit non-goals

Do not implement:

- guard threshold changes (measurement only; calibration is the next prompt)
- cross-encoder promotion or default changes
- LLM-generated paraphrases or any network/model-download dependency
- changes to retrieval, rerank, API, auth, or tenant behavior
- new dependencies

## Constraints

- No new required dependencies.
- Do not read or print .env.
- Default behavior unchanged: deterministic eval smoke must remain 21/21 and qa_pair cases 7/7.
- Tests must not require network access, model downloads, or an OpenAI API key.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

Deterministic eval smokes (must remain green):

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/prompt016_smoke_check.json

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/qa_pair_cases.jsonl \
  --chunks-jsonl eval/cases/qa_pair_chunks.jsonl \
  --output runs/eval/prompt016_qa_pair_check.json

Run the new real-corpus case file and report the baseline summary (gold hit rate, abstain accuracy). Failures of gold-only cases are measurements, not regressions — report them honestly.

If available and safe, run scripts/product_readiness_smoke.sh.

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary; verify Prompt015 artifacts before implementing)
2. Implementation summary (files added/changed, case counts by category, generation skip reasons)
3. Verification results (targeted tests, collect-only, both smokes, real-corpus baseline summary, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether the corpus now supports real-vector guard calibration as the next prompt.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt017_phase5d_real_vector_guard_calibration.md covering measurement of vector-distance distributions on the stamped real-vector collection over the expanded corpus (answerable vs abstain cases), recommended RAG_MAX_DISTANCE / soft-threshold values with evidence, and before/after false-answer and false-abstain rates. Do not execute Prompt017 in this run.

Final clarification before execution:

Eval scope:

- Implement eval corpus expansion only.
- Do not change production retrieval, guard thresholds, citations, API behavior, auth, tenant authorization, cache, streaming, metrics, Docker, or cross-encoder defaults.
- Do not ingest into production vectorstore.
- Do not read or print .env.
- Do not require network access, model downloads, or an OpenAI API key.

Case quality:

- Every new eval case must have an explicit reason and label source.
- Prefer real-document-derived cases from approved-QA records, qa_pair fixtures, and existing canonical chunks.
- Do not add filler cases merely to increase the count.
- If reaching 100+ high-quality combined cases is not possible from available local data, create the maximum high-quality set and report PARTIAL with the exact count and reason.
- Do not fabricate gold_chunk_ids, gold_doc_ids, or expected abstain labels.

Coverage:

- Include retrieval-positive cases.
- Include stored-question approved-QA regression cases.
- Include answer-only-term qa_pair cases.
- Include paraphrase cases.
- Include abstain/no-answer cases.
- Include tenant-isolation-sensitive cases where safe.
- Preserve or document gold_chunk_ids, gold_doc_ids, expected_abstain, and any expected guard labels.

Baseline:

- Run and save a deterministic baseline report for the expanded corpus.
- Baseline must include the current deterministic mode supported locally.
- Gold-only case failures are measurements, not automatic regressions; report them honestly.
- Do not run real-vector or model-download comparisons unless local stamped vectorstore and optional models are already available and safe.
- Do not claim accuracy improvement unless measured.

Safety:

- Existing smoke eval must remain 21/21.
- Existing qa_pair eval must remain passing.
- Existing Prompt013 security tests must remain passing.
- Existing Prompt014 cross-encoder tests must remain passing.

Next prompt:

- Prompt017 should only calibrate real-vector guard thresholds after this corpus exists.
- Do not perform Prompt017 calibration in this run.

If any instruction conflicts, follow this Final clarification section.
