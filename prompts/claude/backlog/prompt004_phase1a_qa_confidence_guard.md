# Prompt004: Phase 1-A Q&A Confidence Guard and No-Answer Calibration

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 1-A for Q&A chatbot usage: make answerability and no-answer behavior rely on real retrieval evidence instead of fabricated pseudo-distances.

The goal is to reduce confident Q&A answers when the retrieved evidence is weak, irrelevant, or unsupported.

## Execution mode

Proceed autonomously.

Do not ask for human confirmation for ordinary local edits, targeted tests, smoke checks, or local verification.

Stop only if one of the following occurs:

- A destructive operation would be required.
- User data would be deleted.
- .env, secrets, tokens, API keys, or private credentials would need to be read, printed, changed, or inferred.
- A remote push, force push, or remote deployment would be required.
- Existing retrieval score fields cannot be safely understood.
- Existing no-answer or guard behavior cannot be safely identified.
- Evaluation cases for abstention are unavailable and no safe minimal fixture can be created.
- The target files cannot be found and the correct location is ambiguous.
- Verification fails in a way that cannot be safely classified after one bounded fix attempt.

If verification fails because of your changes, perform one bounded fix attempt, rerun targeted verification, and report final status.

## Preconditions

Before implementing, check whether Prompt001 to Prompt003 appear complete.

Required signs:

- keyword index health fields exist
- embedding provider/model mismatch is visible or blocked
- duplicate Q&A retrieval work has been reduced or clearly measured

If these signs are absent, do not implement Phase 1-A. Instead, write the appropriate fix prompt to prompts/claude/ and stop.

## Q&A-specific scope

Implement only the following:

1. Identify where the Q&A retrieval path assigns pseudo-distance or fabricated distance-like values to keyword or hybrid hits.

2. Separate ranking score from confidence evidence.

Ranking may still use fusion or heuristic scores.

No-answer guard logic must not treat fabricated rank-based pseudo-distance as real semantic similarity.

3. Add or expose real confidence evidence for Q&A guard logic using existing available signals.

Prefer signals such as:

- best raw vector distance
- best dense retrieval distance
- BM25 score
- keyword evidence strength
- number of strong supporting chunks
- salient mismatch flag, if already present
- too-general flag, if already present

Do not invent complex confidence math unless necessary.

4. Update Q&A no-answer guard logic so weak evidence can trigger a no-answer or guarded response.

Preserve existing conservative behavior where possible.

5. Keep thresholds configurable through existing config patterns.

Do not hardcode magic values in scattered locations.

6. Preserve approved exact-match behavior.

The approved exact-match Q&A path must remain deterministic and must not be affected by this guard change.

7. Preserve response contracts.

Do not redesign /chat response shape in this prompt.

If existing response metadata already supports answer mode or guard reason, use it.

If not, keep changes internal and test behavior through existing outputs.

## Explicit non-goals

Do not implement these in this prompt:

- citation support validation
- fallback answer contract redesign
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
- Do not change approved exact-match Q&A output.
- Do not change /chat or /search response contracts unless existing optional metadata already supports it.
- Do not remove existing heuristics unless tests prove behavior is preserved or improved.
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.
- Prefer targeted tests first.

## Targeted tests to add or update

Add targeted tests only for:

- weak retrieval evidence triggers no-answer or guarded answer
- strong vector evidence does not trigger no-answer
- keyword-only top rank does not automatically bypass the guard through pseudo-distance
- approved exact-match Q&A remains deterministic and unaffected
- existing citation format is not broadly changed
- guard thresholds are loaded through existing config patterns

If existing retrieval-aware eval cases with expected_abstain labels exist, use the smallest relevant subset.

## Verification

Run targeted tests first.

Then run:

python -m pytest --collect-only

If available and safe, run the smallest retrieval-aware eval using local fixtures.

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
- Prompt001 readiness check
- Prompt002 readiness check
- Prompt003 readiness check
- relevant Q&A guard files found

2. Implementation summary

Include:

- files changed
- pseudo-distance handling changed
- real confidence evidence added or exposed
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

If final judgment is PASS, write exactly one next recommended prompt to:

prompts/claude/prompt005_phase2a_qa_exposure_hardening.md

If prompts/claude/backlog/prompt005_phase2a_qa_exposure_hardening.md exists, use it as the source.

Do not execute Prompt005 in this run.
