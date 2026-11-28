# KuraDen On-Prem Install / Upgrade / Rollback / Release Packaging

Operator guide for a first-annual-contract-grade on-prem lifecycle. Local-only;
no Docker required, no deploy, no secrets. Pairs with `docs/operations.md`.

## Release bundle checklist

A release bundle (a tagged git revision) must include:

- [ ] Pinned `requirements.txt` (dependency freeze).
- [ ] `webapi/`, `rag_core/`, `eval/`, `scripts/`, `deploy/`, `configs/` sources.
- [ ] Ops scripts: `deploy_smoke.sh`, `backup.sh`, `restore.sh`,
      `limited_beta_preflight.sh`, `monitoring_runner.sh`, `release_check.py`,
      `promote_collection.py`.
- [ ] Docs: operations, security, SSO, monitoring acceptance, runbooks.
- [ ] A recorded git tag (e.g. `prompt0XX-...`) as the release identifier.

## Environment prerequisites

- Linux host, Python venv; outbound network only for the initial dependency
  install (offline-installable wheels recommended for closed networks).
- TLS-terminating reverse proxy in front of `:8000` (never expose `:8000`).
- Persistent storage for `vectorstore/`, `data/`, `runs/`.

## Install

1. Create venv; `pip install -r requirements.txt`.
2. **Dependency-freeze verification:** `python scripts/release_check.py`
   (must print `RELEASE CHECK: OK`; fails on missing pkg or pinned-version
   mismatch).
3. Configure env (auth, tenants, optional SSO, branding) per the onboarding
   checklist. Never commit `.env`.
4. **Smoke:** `scripts/limited_beta_preflight.sh` (exit 0) and, if Docker is
   available and the operator opts in, `scripts/deploy_smoke.sh`.

## Upgrade

1. **Back up first:** `bash scripts/backup.sh --output-dir backups` (verify the
   archive).
2. Check out the new release tag; `pip install -r requirements.txt`.
3. `python scripts/release_check.py` → OK.
4. `scripts/limited_beta_preflight.sh` → exit 0.
5. Restart the service; confirm `/health` 200 and `/metrics` 200.

## Rollback

1. Stop the service.
2. Check out the previous release tag; reinstall pinned deps; `release_check.py`.
3. If data changed, restore from the pre-upgrade backup
   (`bash scripts/restore.sh <archive> --in-place --source-dir .` with the API
   stopped).
4. Restart; re-run preflight + `/health`.

## Notes

- Single-node; no HA. Upgrades require a brief maintenance window.
- For closed networks, mirror the pinned wheels internally; `release_check.py`
  enforces the freeze.

## Dependency, model and index upgrades (2026-11)

- Install from the lock files only: `pip install --require-hashes -r requirements.lock.txt` (and
  `requirements-embedding.lock.txt` for the embedding extras). Regenerate them with `scripts/lock_dependencies.sh`.
- A change of the embedding model, tokenizer, dictionary or an embedding/tokenizer library changes the embedding
  fingerprint: do NOT serve the old vectors. Re-embed into a NEW generation (`switch_generation.py register ...
  --reembedded`, `verify`, `activate`) and keep the previous one for `rollback`.
- `python scripts/release_check.py --fingerprint` compares the running embedding profile with the served generation's
  manifest and fails on a difference or a missing manifest. Run it after every upgrade, before opening to users.
- A chromadb upgrade may migrate the on-disk store irreversibly: take a backup (`scripts/backup.sh`) first and test
  the restore.
- Cadence: review the lock files and run the release check monthly, and after every security advisory.

## Pre-release checklist additions (2026-11)

- [ ] `AUDIT_QUESTION_MODE=hash` for production and pilot customers (`mask` does not mask Japanese names/addresses).
- [ ] If `CHAT_USE_TENANT_PROFILE=true` is planned: every tenant profile checked first; the shipped product profiles
      set `allow_llm_calls=false` and would make `/chat` answer extractively.
- [ ] Clients and the console poll mail ingestion: `POST /admin/ingestion/mail` with `execute=true` returns 202; they
      poll `GET /admin/ingestion/jobs/{job_id}` until it is no longer `queued`/`running` (`ok`, `issues_found` or `failed`).
- [ ] Mail ingestion runs on a single node when `INGESTION_REGISTRY_BACKEND=postgres` (mail state is node-local).
- [ ] Conversation clients send `source_docs` with saved turns; `CONVERSATION_RETENTION_DAYS` decided for old turns.
- [ ] Legacy untyped rows stamped with `scripts/stamp_legacy_types.py` before promotion (not `PROMOTION_ALLOW_UNTYPED`).

## CI and branch protection (2026-11)

- Protect `main`: pull request required, required status check **`test`** (the CI job), branch up to date, no bypass.
  The job's final step fails when any gated step fails; the step ids, the skip allowlist and the audit-ignore policy
  are in `docs/operations/ci_gates.md`.
- A release is cut only from a commit whose `test` check passed. Ignored pip-audit advisories
  (`security/pip-audit-ignore.txt`) carry an expiry date; an expired entry fails CI and must be re-reviewed before the
  release.
