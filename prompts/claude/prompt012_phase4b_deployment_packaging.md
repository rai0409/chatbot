# Prompt012: Phase 4-B Deployment Packaging

You are working in:

/home/rai/chatbot

## Goal

Implement Phase 4-B: deployment packaging only — container image, compose file, env template, and a minimal CI workflow.

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

1. Dockerfile (repo root): python:3.12-slim base, install requirements.txt, copy the app, run uvicorn webapi.main:app on 0.0.0.0:8000 as a non-root user. Keep the image minimal; do not bake any data, vectorstore, .env, pdfs/, runs/, or index/ contents into the image (use .dockerignore).

2. .dockerignore: exclude .git, .venv, .env*, vectorstore/, runs/, pdfs/, data/, index/, artifacts/, __pycache__, .pytest_cache, prompts/, node_modules.

3. docker-compose.yml: one service for the API with named volumes (or bind mounts) for vectorstore/, index/, data/, and runs/; env_file: .env (compose reads it at runtime — the file itself is never committed); port 8000 mapped; healthcheck hitting /health.

4. .env.example: placeholder values ONLY (e.g. OPENAI_API_KEY=sk-REPLACE_ME). Include the operative knobs with their defaults, grouped and commented: OPENAI_API_KEY, CHAT_MODEL, EMBED_PROVIDER, OPENAI_EMBED_MODEL/LOCAL_EMBED_MODEL, CHUNKS_JSONL_PATH, APPROVED_QA_ENABLED/APPROVED_QA_PATH, API_AUTH_ENABLED/API_AUTH_KEYS, ADMIN_AUTH_ENABLED/ADMIN_AUTH_TOKEN, SEARCH_DEBUG_ENABLED, CORS_ALLOW_ORIGINS, ANSWER_CACHE_ENABLED/ANSWER_CACHE_MAX_ENTRIES, CHAT_COMPLETION_* knobs. Never copy a real value from the local .env — write placeholders from documentation only.

5. .github/workflows/ci.yml: on push/pull_request, Python 3.12, pip install -r requirements.txt, then run the product readiness smoke pytest subset (the same file list as scripts/product_readiness_smoke.sh) plus tests/test_api_auth.py, tests/test_tenant_isolation.py, tests/test_answer_cache.py, tests/test_confidence_guard.py, and python -m pytest --collect-only. No deployment steps, no secrets required.

6. Update README setup section with a short "Run with Docker" subsection (build, compose up, where volumes live).

7. Targeted verification only:

- python -m pytest --collect-only still passes
- scripts/product_readiness_smoke.sh still passes
- Validate compose file syntax if docker compose is available (docker compose config); otherwise note it as skipped
- Do NOT build the image or start containers unless docker is available and fast; building is optional, syntax-level validation is required

## Explicit non-goals

Do not implement these in this prompt:

- Kubernetes manifests, helm, cloud-specific deploy
- TLS termination / reverse proxy config
- multi-stage GPU images or local embedding model downloads in the image
- CD pipelines or registry pushes
- changing any application code
- new Python dependencies

## Constraints

- Do not read or print .env. The .env.example must be written from documented knob names and defaults only.
- Do not expose secrets.
- No application code changes (packaging files + README only).
- Keep changes minimal and localized.
- Do not run full test suites unless targeted verification clearly requires it.

## Required final output

Report in this exact order:

1. Preconditions (repo path, branch, initial git status summary, relevant files found; verify Prompt011 is complete — tenant filtering in retrieval — before implementing)
2. Implementation summary (files added, exact behavior, explicit non-goals preserved)
3. Verification results (collect-only, smoke script, compose validation or why skipped, any skipped verification and why)
4. Git diff summary (git diff --stat, no large diffs)
5. Final judgment: PASS / PARTIAL / FAIL, and whether the Phase 0-4 hardening series is complete enough to revisit the accuracy roadmap (cross-encoder rerank, Q+A pair chunks, eval corpus growth) as the next track.
6. Next prompt file: if PASS, write exactly one next recommended prompt to prompts/claude/prompt013_phase5a_cross_encoder_rerank.md covering an optional, profile-gated cross-encoder rerank stage (local sentence-transformers cross-encoder via the existing optional-import pattern, applied to the fused top-N, promoted only through the existing rerank promotion gate evals). Do not execute Prompt013 in this run.
