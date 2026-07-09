# Prompt004: Phase 1-A Real Confidence Guard and No-Answer Calibration

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 1-A: real confidence guard and no-answer calibration only.

Make abstention/no-answer behavior rely on real retrieval evidence instead of fabricated pseudo-distances.

The goal is to reduce confident answers when evidence is weak.

## Execution mode

Proceed autonomously.

Do not ask for human confirmation for ordinary local edits, targeted tests, smoke checks, or local verification.

Stop only if one of the following occurs:

- A destructive operation would be required.
- User data would be deleted.
- .env, secrets, tokens, API keys, or private credentials would need to be read, printed, changed, or inferred.
- A remote push, force push, or remote deployment would be required.
- Existing retrieval score fields cannot be safely understood.
- Evaluation cases for abstention are unavailable and no safe minimal fixture can be created.
- The target files cannot be found and the correct location is ambiguous.
- Verification fails in a way that cannot be safely classified after one bounded fix attempt.

If verification fails because of your changes, perform one bounded fix attempt, rerun targeted verification, and report final status.

## Preconditions

Before implementing, check whether Prompt001, Prompt002, and Prompt003 appear complete.

Required signs:

- keyword index health fields exist
- embedding provider/model mismatch is visible or blocked
- duplicate retrieval work has been reduced or clearly measured

If these signs are absent, do not implement Phase 1-A. Instead, write the appropriate fix prompt to prompts/claude/ and stop.

## Scope

Implement only the following:

1. Identify where pseudo-distance is assigned to keyword or hybrid hits.

2. Preserve rank ordering behavior if needed, but separate ranking score from confidence evidence.

3. Add or expose real confidence signals for guard logic, such as:

- best raw vector distance, if available
- BM25 score, if available
- keyword evidence strength, if already computed
- combined calibrated confidence, if safely derivable

4. Update guard logic so hard no-answer and soft no-answer decisions can use real retrieval evidence.

5. Keep thresholds configurable through existing config patterns.

6. Add targeted tests only for:

- weak evidence triggers no-answer or guarded response
- strong vector evidence does not trigger no-answer
- keyword-only top rank does not automatically bypass distance guard because of pseudo-distance
- existing approved exact-match path remains deterministic and unaffected
- existing citation format is not broadly changed in this prompt

7. If an existing retrieval-aware eval runner with expected_abstain labels is available and safe, run the smallest relevant eval.

## Explicit non-goals

Do not implement these in this prompt:

- citation support validation
- fallback answer contract changes
- streaming
- auth/rate limiting
- CORS changes
- tenant isolation changes
- Docker/CI changes
- cross-encoder reranker
- LLM query rewriting
- broad refactors
- unrelated formatting changes

## Constraints

- No new dependencies.
- Do not read or print .env.
- Do not expose secrets.
- Do not change /chat or /search response contracts unless no-answer metadata already exists and can be reused safely.
- Do not remove existing heuristics unless clearly superseded and tests prove behavior is preserved.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

If available and safe, run the smallest retrieval-aware eval using existing local fixtures.

If available and safe, run:

scripts/product_readiness_smoke.sh

Do not run broad, slow, or external-network-dependent tests unless necessary.

## Required final output

Report in this exact order:

1. Preconditions

Include:

- repo path
- branch
- initial git status summary
- Prompt001 to Prompt003 readiness check
- relevant files found

2. Implementation summary

Include:

- files changed
- exact confidence evidence added
- guard behavior changed
- explicit non-goals preserved

3. Verification results

Include:

- targeted tests
- pytest collect-only
- retrieval-aware eval, if run
- product readiness smoke, if run
- any skipped verification and why

4. Git diff summary

Include:

- git diff --stat
- do not paste large diffs

5. Final judgment

Use one of:

- PASS
- PARTIAL
- FAIL

Also state whether this is safe to continue to Prompt005.

6. Next prompt file

If final judgment is PASS, promote or write exactly one next recommended prompt to:

prompts/claude/prompt005_phase2a_exposure_hardening.md

If prompts/claude/backlog/prompt005_phase2a_exposure_hardening.md exists, use it as the source.

Do not execute Prompt005 in this run.
