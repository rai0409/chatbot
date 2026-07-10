# Commercial Quality Baseline Summary

## Green

- Branch `main` is at `c48dc88f36a7c5c672d8580c1a8c208fc1a3f116`.
- `origin/main` resolves to the same commit.
- `python -m compileall rag_core webapi tools` passed.
- Grounded extractive quality passed: 14/14.
- Unknown abstention passed: 32/32 abstained.
- Exact QA passed: 118/118 with answer match rate 1.0.
- Normal retrieval passed with hybrid_hit@5 1.0.
- Product readiness smoke passed on final local rerun: 117 passed, 1 warning.
- Unsupported answer count is 0.
- Failed checks are [].

## Not Validated

- LLM mode answer quality.
- DOCX/CSV/XLSX/PPTX workflows.
- Production promotion, overwrite, and rollback.
- Production load and long-running soak behavior.
- Admin workflow end-to-end operation.
- Browser-based non-engineer upload workflow.
- QA Excel import workflow.
- RAG tuning import workflow.
- Staging-to-production approval workflow.
- Security penetration testing and secrets rotation.

## Top 5 Next Steps

1. Add a dedicated LLM mode quality gate before any LLM mode quality claim.
2. Harden product readiness reporting against dirty worktree path noise.
3. Add explicit document-format validation before claiming DOCX/CSV/XLSX/PPTX workflows.
4. Add staging promotion, rollback, and audit validation.
5. Add admin and non-engineer import/upload workflow validation.

## Non-Claims

- No git commit or git push was performed.
- No production promote or production overwrite was performed.
- No vectorstore deletion, collection reset, or ingestion reset was performed.
- No evaluator pass condition was relaxed.
- This baseline does not claim LLM mode quality is validated.
- This baseline does not claim DOCX/CSV/XLSX/PPTX workflows are supported or validated.
