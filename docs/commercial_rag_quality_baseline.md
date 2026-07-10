# Commercial RAG Quality Baseline

## Executive Summary

This baseline fixes the current main branch extractive RAG/chatbot quality as the commercial reference point.

Verified facts:

- Branch: `main`
- HEAD commit: `c48dc88f36a7c5c672d8580c1a8c208fc1a3f116`
- Origin main commit: `c48dc88f36a7c5c672d8580c1a8c208fc1a3f116`
- Grounded extractive quality gate: passed, 14/14 cases
- Unknown abstention gate: passed, 32/32 abstained
- Exact QA gate: passed, 118/118 with answer match rate 1.0
- Normal retrieval: hybrid_hit@5 1.0
- Product readiness smoke: passed, 117 passed and 1 warning on final local rerun
- Unsupported answer count in unknown abstention: 0
- Failed checks in extractive validation summaries: []

Assessment:

- The validated commercial baseline is strong for local extractive RAG gates.
- This is not a full commercial production readiness claim.
- LLM mode quality is not validated.
- DOCX/CSV/XLSX/PPTX workflows are not validated.
- Production promotion, production overwrite, vectorstore deletion, collection reset, and ingestion reset were not performed.

## Current Branch And Commit

Facts collected locally:

- `git branch --show-current`: `main`
- `git rev-parse HEAD`: `c48dc88f36a7c5c672d8580c1a8c208fc1a3f116`
- `git ls-remote origin main`: `c48dc88f36a7c5c672d8580c1a8c208fc1a3f116`
- Initial `git status --short`: clean

Recent main history:

```text
c48dc88 Merge pull request #18 from rai0409/eval/real-vector-evidence
b18758f Fix CI dependencies and tenant isolation mock
5e2f4e0 Install pytest in CI
d0b80b4 Merge pull request #17 from rai0409/eval/real-vector-evidence
f6a70d6 Refresh unknown manual chat response artifact
94c99cd Add grounded extractive answer quality gate
2253bce Add controlled promotion decision and switch plan
3f80a93 Add canonical metadata and fingerprint audit tools
```

## Verified Gates

Verified from existing artifacts and local reruns:

- Python compile check: passed
- Grounded extractive quality: passed
- Free extractive chat mode: passed
- Product readiness smoke: passed on final local rerun

One transient product readiness run failed before generated validation side effects were restored. The failure was caused by dirty worktree content that included a tracked `uvicorn.log` path in the readiness report raw JSON, not by a failed product behavior assertion. After restoring those side effects, the same smoke script passed.

## Local Validation Results

Commands rerun locally:

```text
python -m compileall rag_core webapi tools
bash scripts/run_grounded_extractive_quality_check.sh
bash scripts/run_free_extractive_chat_mode_check.sh
bash scripts/product_readiness_smoke.sh
```

Results:

- `compileall`: passed
- `run_grounded_extractive_quality_check.sh`: passed, 14/14 grounded cases
- `run_free_extractive_chat_mode_check.sh`: passed
- `product_readiness_smoke.sh`: passed on final rerun, 117 passed and 1 warning

## CI Status

CI status source: user-provided known state. This snapshot did not call GitHub Actions APIs.

- `CI/test (push)`: successful
- `CI/test (pull_request)`: successful

## Extractive RAG Quality

Source artifacts:

- `artifacts/grounded_extractive_quality/validation_summary.json`
- `artifacts/grounded_extractive_quality/grounded_extractive_quality_report.md`

Verified metrics:

- Gate status: passed
- Total cases: 14
- Passed cases: 14
- Failed cases: 0
- Pass rate: 1.0
- HTTP errors: 0
- Empty answers: 0
- Answer mode mismatches: 0
- Missing citations: 0
- Source document misses: 0
- Page misses: 0
- Required term misses: 0
- Abstained count: 0
- Fallback count: 0
- Failed case IDs: []

## Unknown Abstention Quality

Source artifacts:

- `artifacts/free_extractive_chat_mode/validation_summary.json`
- `artifacts/free_extractive_chat_mode/unknown_abstention/unknown_abstention_results.jsonl`

Verified metrics:

- Total unknown cases: 32
- Errors: 0
- Abstained count: 32
- Unsupported answer count: 0
- Approved exact false positive count: 0
- Unknown abstention results JSONL line count: 32

## Exact QA Quality

Source artifact:

- `artifacts/free_extractive_chat_mode/validation_summary.json`

Verified metrics:

- Total exact QA cases: 118
- Errors: 0
- Answer match rate: 1.0
- Approved exact rate: 1.0
- LLM fallback count: 0

## Retrieval Quality

Source artifact:

- `artifacts/free_extractive_chat_mode/validation_summary.json`

Verified metrics:

- Normal retrieval hybrid_hit@5: 1.0
- Still failed: []

Additional rerun output from the free extractive script reported:

- Normal retrieval total cases: 32
- Vector hit@5: 0.9375
- Hybrid hit@1: 0.9375
- Hybrid hit@3: 1.0
- Hybrid hit@5: 1.0
- Hybrid MRR: 0.96875
- Hybrid source_doc_match@5: 1.0
- Hybrid page_match@5: 0.96875
- Still failed: []

## Product Readiness Smoke

Final local rerun:

- Command: `bash scripts/product_readiness_smoke.sh`
- Exit code: 0
- Pytest result: 117 passed, 1 warning
- Py compile phase: completed

Observed warning:

- `AuthlibDeprecationWarning` from `webapi/oidc_auth.py`

Transient earlier attempt:

- Exit code: 1
- Pytest result: 116 passed, 1 failed, 1 warning
- Failed test: `tests/test_production_readiness_report.py::test_report_does_not_start_uvicorn_or_require_server`
- Reason recorded: dirty worktree content included a tracked `uvicorn.log` path in readiness report raw JSON after validation script side effects.
- Resolution: restore generated tracked side effects and rerun the smoke script; final rerun passed.

## Commercial Strengths

- Extractive answer quality has a focused grounded gate with citations, source document checks, page checks, and required term checks.
- Unknown and unsupported questions are covered by an abstention gate with 32 cases and unsupported_answer_count 0.
- Exact approved QA behavior is covered with 118 cases and answer match rate 1.0.
- Hybrid retrieval has verified hybrid_hit@5 1.0 in the normal retrieval gate.
- Local product readiness smoke passed after side-effect cleanup.
- CI is recorded as successful from the provided known state.
- No evaluator pass condition was relaxed in this snapshot task.

## Commercial Gaps

- This baseline is centered on local extractive mode gates, not a full production certification.
- Product readiness still has at least one warning.
- CI status was not independently rechecked through GitHub Actions in this task.
- The smoke test is sensitive to dirty worktree paths appearing in readiness report raw JSON.
- Admin operations, promotion workflows, rollback workflows, and long-running production operations are not fully covered by this baseline.
- Runtime behavior under production traffic, rate limits, tenant-specific load, and failure injection is not validated here.

## Not Yet Validated

- LLM mode answer quality.
- Production promotion.
- Production overwrite.
- Production rollback.
- Production traffic load.
- Long-running soak behavior.
- Security penetration testing.
- Secrets rotation procedure.
- Admin workflow end-to-end operation.
- Browser-based non-engineer upload workflow.
- QA Excel import workflow.
- RAG tuning import workflow.
- Staging-to-production approval workflow.
- DOCX/CSV/XLSX/PPTX workflows.
- Multitenant production isolation beyond the current tested scope.

## Non-Claims

This baseline does not claim:

- LLM mode quality is validated.
- DOCX/CSV/XLSX/PPTX workflows are supported or validated.
- Production promote was performed.
- Production overwrite was performed.
- Vectorstore deletion was performed.
- Collection reset was performed.
- Ingestion reset was performed.
- All commercial requirements are complete.
- All free-form user questions are guaranteed correct.
- CI was freshly rechecked through GitHub Actions APIs during this task.

## Required Next Steps

- Add a dedicated LLM mode quality gate before making any LLM mode quality claim.
- Harden product readiness reporting so generated log filenames in dirty worktree status do not create misleading failures.
- Add explicit document-format validation gates before claiming DOCX/CSV/XLSX/PPTX workflows.
- Add staging promotion, rollback, and audit validation before any production operation claim.
- Add admin workflow and non-engineer upload/import validation.
- Add production-like load, tenant isolation, and operational failure tests.
