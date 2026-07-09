# Prompt015: Phase 5-B Approved Q&A → Q+A Pair RAG Chunks

You are working in:

/home/rai/chatbot

## Goal

Implement the README's documented-but-unbuilt answer route 3: convert approved Q&A records into canonical Q+A pair chunks so retrieval sees the question and the answer together in one chunk.

Generic PDF chunking can split a table-row question from its answer. For table-style Q&A documents, a dedicated Q+A pair chunk is the preferred retrieval unit. This directly targets the main real corpus (table-style Q&A PDFs, e.g. the 22-record approved set from 58887_95105_misc.pdf).

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

- Prompt013 security is complete: tests/test_api_key_tenant_authorization.py passes.
- Prompt014 is complete: rag_core/cross_encoder_reranker.py exists, "hybrid_rerank_ce" is in eval/runner.py, tests/test_cross_encoder_rerank.py passes.
- The approved-QA loader (rag_core/approved_qa.py) and the canonical-chunks ingest path are present.

## Scope

Implement only the following:

1. A converter script scripts/approved_qa_to_pair_chunks.py:

- Input: an approved-QA JSONL file (same schema as data/approved_qa/default.jsonl, validated via rag_core/approved_qa.py loading).
- Output: canonical chunk JSONL records, one per approved record, with:
  - text: question and approved answer together in a stable layout (question line, then answer), suitable for citation-first answers
  - searchable_text: question + answer (so BM25 and the cross-encoder see both)
  - metadata: doc_type="qa_pair", approved_qa_id, tenant_id (from the record, default "default"), source_doc/source_pages preserved from approved citations when present
  - a deterministic chunk id derived from approved_qa_id (re-runs must not produce duplicate ids)
- Skips records that are not status=approved; reports counts (written, skipped, by reason).
- Pure stdlib + existing repo modules; no new dependencies; no network.

2. Retrieval compatibility (no behavior change by default):

- qa_pair chunks must flow through the existing keyword/vector/hybrid retrieval as ordinary canonical chunks (tenant filtering must apply to them exactly as to other chunks).
- Do not auto-prefer qa_pair chunks in the reranker in this prompt; doc_type is metadata only.

3. Eval cases:

- Add a small eval corpus + cases file under eval/cases/ (e.g. qa_pair_cases.jsonl + qa_pair_chunks.jsonl) with at least 5 cases where the gold chunk is a qa_pair chunk, including at least one case whose question wording differs from the stored question (paraphrase) and one abstain-labeled case.
- Cases must run with the deterministic stub path (no network, no model downloads).

4. Targeted tests only:

- converter: approved record → expected chunk fields (text contains both Q and A, doc_type, deterministic id, tenant_id propagation)
- converter: non-approved records skipped; duplicate approved_qa_id handling is deterministic
- retrieval: a qa_pair chunk is retrievable via keyword retrieval for a query matching the answer text only (proving Q+A are searched together)
- tenant isolation: qa_pair chunks tagged tenant_a are invisible to tenant_b

## Explicit non-goals

Do not implement:

- automatic ingest into the production vectorstore (the script writes JSONL; ingest stays the existing manual workflow)
- similar-question auto-answering or any change to the approved exact-match route
- reranker preference for qa_pair chunks
- changes to guard thresholds, API behavior, auth, tenant authorization, cache, streaming
- new dependencies

## Constraints

- No new required dependencies.
- Do not read or print .env.
- Default behavior unchanged: deterministic eval smoke must remain 21/21.
- Tests must not require network access, model downloads, or an OpenAI API key.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

Run the deterministic eval smoke (must remain 21/21):

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/prompt015_smoke_check.json

Run the new qa_pair eval cases with the runner (deterministic mode) and report the summary.

If available and safe, run scripts/product_readiness_smoke.sh and the security suites:

python -m pytest tests/test_api_key_tenant_authorization.py tests/test_tenant_isolation.py -q

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary; verify Prompt013 security and Prompt014 cross-encoder are complete before implementing)
2. Implementation summary (files added/changed, exact chunk schema produced, explicit non-goals preserved)
3. Verification results (targeted tests, collect-only, eval smoke, qa_pair eval summary, smoke script if run, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether it is safe to continue to Prompt016.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt016_phase5c_eval_corpus_expansion.md covering expansion of the labeled eval corpus toward 100+ real-document-derived cases (retrieval, abstain, and approved-QA regression), as the prerequisite for real-vector guard calibration and the cross-encoder promotion gate. Do not execute Prompt016 in this run.
