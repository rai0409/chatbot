# Prompt023: Deploy Ops Pack (Track B)

Numbered 023: prompt020 is the parked CE promotion eval, prompt021 the guard redesign, prompt022 the multiformat onboarding run that wrote this file.

You are working in:

/home/rai/chatbot

## Goal

Turn "packaged" into "operable": prove the container actually runs the product end-to-end with mounted data, and give operators backup/restore, TLS reference, and log-retention guidance. This is batch B4 from docs/reports/current_state_chatbot_direction_autonomous_plan.md.

## Execution mode

Proceed autonomously. Commit and tag automatically only on PASS with a prompt-scoped diff.

Stop only for: destructive operations, user-data deletion, secrets/.env access (never read or copy .env — compose smoke must use a generated throwaway env file with placeholder values), remote push/deploy, production vectorstore/default collection mutation, required network/model downloads, ambiguous missing targets, or unresolved verification failure after one bounded fix attempt.

## Preconditions to verify before implementing

- Prompt022 complete: tag prompt022-multiformat-onboarding exists; scripts/onboard_documents_dry_run.py and scripts/import_manifest.py present; tests/test_multiformat_onboarding.py passes.
- Docker availability: `docker --version` and `docker compose version`. If Docker is unavailable in this environment, implement everything, validate scripts with bash -n / shellcheck-style review and unit-testable pieces, run `docker compose config --quiet`, and report PARTIAL for the live-smoke portion only (scripts still committed if everything else passes — judge PASS if only the live Docker run is environmentally impossible and say so explicitly).

## Scope

1. scripts/deploy_smoke.sh:

- Builds the image, generates a throwaway .env.smoke from .env.example placeholders (NEVER copying .env), starts compose with an isolated project name and a dedicated smoke vectorstore/data volume set (e.g. bind-mounts of a temp dir — never the repo's live vectorstore/), waits for /health, curls /health and /metrics, POSTs /chat with API auth enabled via smoke-only keys (expecting a deterministic guard/fallback response without an OpenAI key — assert HTTP behavior, not answer quality; a 502/error JSON from the missing provider is acceptable if the API contract holds), then tears everything down including volumes.
- Exit 0 only if all checks pass; cleanup must run on failure too (trap).

2. scripts/backup.sh and scripts/restore.sh:

- backup: tar vectorstore/, data/approved_qa/, runs/audit/ (existing paths only) into a timestamped archive under backups/ (gitignored), with a manifest (file list + sha256).
- restore: takes an archive, restores into a target directory (default: a staging dir, NOT in-place over live data unless --in-place is passed explicitly), verifies the manifest hashes, and prints what was restored.
- A restore-verification helper or test that round-trips a tiny synthetic vectorstore/data dir.

3. docs/operations.md:

- Reverse proxy / TLS reference config (nginx and caddy snippets: TLS termination, proxy to :8000, body size limit, timeouts, /health passthrough).
- Log retention policy for runs/audit/ JSONL (rotation guidance, retention period placeholder, what the logs contain / do not contain).
- Backup/restore runbook using the new scripts.

4. Targeted tests only:

- bash syntax checks (bash -n) for the three scripts
- backup/restore round-trip on synthetic dirs (pytest, tmp_path, invoking the scripts)
- deploy smoke script refuses to run if .env.smoke generation would overwrite an existing file it did not create

## Explicit non-goals

Kubernetes/helm, CD pipelines, cloud-specific configs, monitoring exporters (separate batch), rate limiting (separate batch), UI, changes to app code, new dependencies.

## Verification

Targeted tests first, then:

python -m pytest --collect-only -q

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/smoke_cases.jsonl --chunks-jsonl eval/cases/smoke_chunks.jsonl --output runs/eval/prompt023_smoke_check.json

PYTHONPATH=. .venv/bin/python -m eval.runner --cases eval/cases/qa_pair_cases.jsonl --chunks-jsonl eval/cases/qa_pair_chunks.jsonl --output runs/eval/prompt023_qa_pair_check.json

docker compose config --quiet

bash scripts/deploy_smoke.sh  (if Docker available)

python -m pytest tests/test_api_key_tenant_authorization.py tests/test_tenant_isolation.py -q

scripts/product_readiness_smoke.sh if safe.

## Commit/tag policy

PASS: commit "prompt023 deploy ops pack", tag prompt023-deploy-ops. PARTIAL/FAIL: no commit, no tag, report blocker (exception: the documented Docker-unavailable case above).

## Required final output

1. Preconditions (incl. Docker availability)
2. Implementation summary
3. Verification results (incl. live deploy smoke output or the environmental reason it was skipped)
4. Git diff summary
5. Commit/tag result
6. Final judgment: PASS / PARTIAL / FAIL
7. Next prompt file: if PASS, write exactly one next prompt to prompts/claude/product/prompt024_security_ops_pack.md (rate limiting + key rotation runbook + secrets handling doc — batch B5). Do not execute it.
