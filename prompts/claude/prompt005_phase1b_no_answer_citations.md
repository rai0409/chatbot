# Prompt005: Phase 1-B Honest No-Answer Citations

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 1-B: stop fabricating citations on guard/no-answer fallback.

Today, when the guard fires, rag_core/qa.py builds a fallback answer that cites [S1] against a placeholder chunk ("該当なし" or "OCR", see qa.py around the guard_reason branches in _answer_query_impl), so no-answer responses carry a fabricated citation pointing at evidence that does not support anything.

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

If verification fails because of your changes, perform one bounded fix attempt, rerun the targeted verification, and report final status.

## Scope

Implement only the following:

1. When guard_reason is set (any no-answer/fallback branch in _answer_query_impl, including missing_procedure_evidence), the response must return:

- citations: [] (no fabricated [S1] payloads)
- answer_text without [S#] source tags
- answer_with_footnotes without a fabricated 参考資料 reference to a placeholder chunk

2. Do NOT add any new response field to /chat or AnswerResult. The existing field set is the contract: no-answer is already expressed by guard_reason (non-null) plus used_fallback=true. The trace's existing answer_mode ("fallback") stays as is.

3. Keep the human-readable Japanese fallback message text itself (the explanation lines); only remove the fabricated citation tags/payloads attached to it.

4. Keep the extractive-fallback path (validate_output failure on a real LLM answer) unchanged: it cites real retrieved chunks and is legitimate.

5. Update existing tests that assert fabricated citations on guard fallback, if any; add targeted tests only for:

- guard-fired /chat-level result has citations == [] and used_fallback == true and non-null guard_reason
- answer_text on guard fallback contains no [S#] tags
- answer_with_footnotes on guard fallback contains no fabricated reference entry
- grounded (non-guard) answers still produce real citations (regression)
- approved exact-match path unchanged (citations preserved from approved metadata)

## Explicit non-goals

Do not implement these in this prompt:

- changing guard logic or thresholds (Prompt004 owns that)
- changing fusion/rerank/retrieval
- adding new response fields or answer modes to AnswerResult
- citation support/NLI checking
- streaming, caching, auth, tenants, Docker/CI
- broad refactors

## Constraints

- No new dependencies.
- Do not read or print .env.
- Do not expose secrets.
- Do not change the /chat response field SET (values change only for guard-fired responses: empty citations, untagged text).
- Tests must not require network access or an OpenAI API key.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

Capture before/after deterministic eval outputs:

- runs/eval/prompt005_smoke_before.json (capture BEFORE modifying code)
- runs/eval/prompt005_smoke_after.json

PYTHONPATH=. .venv/bin/python -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/prompt005_smoke_<before|after>.json

Smoke must remain 21/21. If a smoke case asserts the old fabricated-citation behavior, update the case expectation and call this out explicitly in the report.

If available and safe, run scripts/product_readiness_smoke.sh.

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary, relevant files found; verify Prompt004 is complete — guard uses vector_distance / weak_keyword_evidence — before implementing)
2. Implementation summary (files changed, exact behavior added, explicit non-goals preserved)
3. Verification results (targeted tests, collect-only, smoke before/after 21/21, smoke script if run, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether it is safe to continue to Prompt006.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt006_phase2a_api_hardening.md covering API exposure hardening only (API-key auth middleware for non-admin endpoints, gating /search/debug behind auth or a non-production profile, and a CORS allowlist; no rate limiting yet, no new dependencies). Do not execute Prompt006 in this run.
