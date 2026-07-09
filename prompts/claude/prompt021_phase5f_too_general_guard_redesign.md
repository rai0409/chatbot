# Prompt021: Measurement-driven too_general Guard Redesign

## Goal

Reduce false-abstains caused by the too_general guard using measured evidence from the same real-corpus eval setup used in Prompt017.

Prompt017 found:

- real-vector gold hit: 41/41
- false-abstain: 19/41
- too_general caused 16/19 false-abstains
- distance thresholds are not the right lever because answerable and abstain distance distributions overlap
- guard thresholds were correctly left unchanged
- cross-encoder promotion eval is currently blocked/PARTIAL if BAAI/bge-reranker-v2-m3 is not locally cached

The goal is to redesign only the too_general decision logic so answerable real-corpus cases are not incorrectly blocked, while abstain safety does not regress.

## Execution mode

Proceed autonomously.

Do not ask for yes/no confirmation for safe local repository edits, targeted tests, evals, local reports, commits, or tags.

Commit and tag automatically only if the prompt reaches PASS and the git diff is limited to this prompt scope.

Stop only for:

- destructive operations
- user-data deletion
- .env, secrets, tokens, API keys, or credentials access
- remote push/deploy/external login
- production vectorstore/default collection mutation
- required network/model download
- ambiguous missing targets
- unresolved verification failure after one bounded fix attempt

Never read or print .env.
Never push remotely.
Never mutate production vectorstore.
Never download models.
Do not execute Prompt020.
Do not change cross-encoder settings.
Do not change global distance thresholds.

## Preconditions to verify

Verify:

- Repo path is /home/rai/chatbot.
- Branch is eval/real-vector-evidence.
- Prompt017 is complete:
  - eval/guard_distance_calibration.py exists
  - runs/eval/guard_distance_calibration.json exists
  - runs/eval/prompt017_realvector_before.json exists
  - tests/test_guard_distance_calibration.py passes
- Collection guard_calibration_eval_v1 exists and is stamped.
- Real corpus exists:
  - eval/cases/real_corpus_cases.jsonl
  - eval/cases/real_corpus_chunks.jsonl
- Current guard logic exists in rag_core/qa.py.
- Current too_general tests exist, likely in:
  - tests/test_qa_reranker_integration.py
  - tests/test_confidence_guard.py
- Prompt020 CE promotion is not required for this prompt.

If Prompt017 commit/tag exists, report it. If not, do not block solely on missing tag as long as artifacts are present.

## Scope

Implement a measured redesign of the too_general guard only.

The redesign must be evidence-based and conservative.

Allowed work:

1. Add an analysis script, for example:

   eval/too_general_guard_analysis.py

   It should:
   - load runs/eval/prompt017_realvector_before.json
   - identify false-abstain cases caused by too_general
   - compare them against correctly abstained cases
   - extract safe observable features from traces/cases/chunks
   - produce JSON + Markdown reports under runs/eval/

2. Modify too_general guard logic in rag_core/qa.py only if the evidence supports a safe improvement.

3. Add targeted tests for the new too_general behavior.

4. Re-run real-corpus eval in real-vector mode against guard_calibration_eval_v1.

5. Compare before/after:

   Required metrics:
   - false-abstain count
   - false-answer count
   - abstain correct count
   - answerable correct count
   - too_general-trigger count
   - reason breakdown

## Design constraints

The redesign must not simply disable too_general.

It must preserve safety for genuinely vague queries.

Prefer narrowing too_general so it does not fire when strong local evidence exists.

Candidate safe signals to evaluate:

- exact or near-exact business term match in top evidence
- quoted or identifier-like terms
- Japanese compound noun match
- answer-bearing chunk has high keyword/localized evidence
- top chunk title/section/source metadata strongly matches query
- query is short but specific, not merely generic
- best retrieved chunk has strong lexical evidence even if query is short
- gold-like evidence appears in searchable_text, title, section_path, source metadata, or QA pair text

Do not rely on LLM calls.

Do not use network.

Do not change vector distance thresholds.

Do not change approved Q&A exact route.

Do not change cross-encoder settings.

Do not change API/auth/tenant behavior.

Do not change multi-format converters.

Do not change production profile behavior unless directly required by tests, and explain why.

## Promotion rule

PASS with behavior change only if:

- false-abstain improves by at least 5 cases, OR too_general false-abstain count drops by at least 50%
- false-answer does not increase
- abstain-correct count does not decrease
- smoke eval remains 21/21
- qa_pair eval remains 7/7
- targeted guard tests pass
- security/tenant tests remain green

If evidence is insufficient or safety regresses:

- leave runtime behavior unchanged
- write the analysis report
- return PARTIAL or PASS-without-change depending on verification
- do not force a behavior change

## Verification

Run targeted tests first.

Required:

python -m pytest tests/test_confidence_guard.py tests/test_qa_reranker_integration.py -q

python -m pytest tests/test_guard_distance_calibration.py -q

python -m pytest --collect-only -q

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/prompt021_smoke_check.json

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/qa_pair_cases.jsonl \
  --chunks-jsonl eval/cases/qa_pair_chunks.jsonl \
  --output runs/eval/prompt021_qa_pair_check.json

Run real-vector before/after comparison against guard_calibration_eval_v1 and save artifacts under runs/eval/.

Run if safe:

python -m pytest tests/test_api_key_tenant_authorization.py tests/test_tenant_isolation.py -q

scripts/product_readiness_smoke.sh

## Required artifacts

Create or update only prompt-scoped artifacts such as:

- eval/too_general_guard_analysis.py
- tests/test_too_general_guard_redesign.py or targeted additions to existing guard tests
- runs/eval/too_general_guard_analysis.json
- runs/eval/too_general_guard_analysis.md
- runs/eval/prompt021_realvector_before.json
- runs/eval/prompt021_realvector_after.json
- runs/eval/prompt021_comparison.json
- runs/eval/prompt021_smoke_check.json
- runs/eval/prompt021_qa_pair_check.json
- prompts/claude/prompt022_multiformat_onboarding_or_deploy_next.md if PASS

Do not add real customer data.
Do not add private PDFs.
Do not add data/ or pdfs/ unless they are synthetic and clearly prompt-scoped.

## Required final output

Report in this exact order:

1. Preconditions
2. too_general failure analysis
3. Redesign methodology
4. Implementation summary
5. Measured before/after evidence
6. Verification results
7. Git diff summary
8. Commit/tag result
9. Final judgment: PASS / PARTIAL / FAIL
10. Recommended next prompt:
   - Prompt019 multi-format onboarding if guard improves
   - Prompt020 rerun if CE model is cached
   - deployment/backup smoke if accuracy track is paused

## Commit/tag policy

If PASS and the diff is limited to this prompt scope:

- commit with message:
  prompt021 measured too_general guard redesign

- tag:
  prompt021-phase5f-too-general-guard-redesign

If PARTIAL or FAIL:

- do not commit
- do not tag
- report exact blocker and next command
