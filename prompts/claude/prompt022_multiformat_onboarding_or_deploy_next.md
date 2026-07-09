# Prompt022: Multi-format Onboarding (Prompt019) Or Deploy Ops — Next Track Decision

You are working in:

/home/rai/chatbot

## Context

The accuracy track has reached a clean checkpoint:

- Prompt017: guard distance calibration — thresholds correctly left unchanged (distributions overlap); stamped eval collection guard_calibration_eval_v1 created; fingerprint stamping bug fixed.
- Prompt021: too_general evidence-coverage bypass — real-vector false-abstain 19/41 → 8/41 (too_general false-abstains 16 → 5, −68.8%), zero abstain/false-answer regression, smoke 21/21, qa_pair 7/7.
- Remaining accuracy levers (NOT this prompt): 3 soft_distance false-abstains (needs threshold work ruled out by Prompt017 evidence), 5 partial-coverage paraphrase cases (cross-encoder territory — Prompt020, blocked until BAAI/bge-reranker-v2-m3 is locally cached), 2 false-answers (need content-level guards or corpus growth).

## Goal

Execute ONE of the two pending product tracks, chosen by precondition:

- Track A (preferred): prompts/claude/product/prompt019_multiformat_ingest_eval_and_onboarding.md — sample docs, multi-format eval corpus, import manifest, duplicate detection, one-command dry-run onboarding.
- Track B (fallback if Track A is blocked): a deploy-ops pack — scripts/deploy_smoke.sh (docker build → compose up → live /health,/chat,/metrics → down), scripts/backup.sh / restore.sh with restore verification, reverse proxy/TLS reference config (docs), log retention policy (docs).

Decide: run Track A unless its preconditions fail (Prompt018 converter tests must pass); fall back to Track B only with the blocker documented. Do not run both in one session.

## Execution mode

Proceed autonomously. Commit and tag automatically only on PASS with a prompt-scoped diff.

Stop only for: destructive operations, user-data deletion, secrets/.env access, remote push/deploy, production vectorstore/default collection mutation, required network/model downloads, ambiguous missing targets, or unresolved verification failure after one bounded fix attempt.

Never read .env. Never push. Never mutate the production vectorstore or default collection. Track A ingest (if any) only into an explicitly named non-production collection, default dry-run.

## Verification (both tracks)

Targeted tests first, then:

python -m pytest --collect-only -q

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt022_smoke_check.json

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt022_qa_pair_check.json

python -m pytest tests/test_api_key_tenant_authorization.py tests/test_tenant_isolation.py tests/test_too_general_guard_redesign.py -q

scripts/product_readiness_smoke.sh if safe.

## Commit/tag policy

- Track A PASS: commit "prompt022 multiformat onboarding dry-run", tag prompt022-multiformat-onboarding
- Track B PASS: commit "prompt022 deploy ops pack", tag prompt022-deploy-ops
- PARTIAL/FAIL: no commit, no tag; report the exact blocker and the next command.

## Required final output

1. Track decision and precondition evidence
2. Implementation summary
3. Verification results
4. Git diff summary
5. Commit/tag result
6. Final judgment: PASS / PARTIAL / FAIL
7. Next prompt file: if PASS, write exactly one next recommended prompt covering the track NOT chosen this run (deploy-ops if A ran; onboarding if B ran). Do not execute it.
