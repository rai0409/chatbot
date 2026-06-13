# Limited Beta Launch Checklist

Operational gate for a **limited external beta** under the GO-with-conditions
recommendation in `beta_go_no_go_assessment.md`. This is **not** general
production approval. Every box must be checked for the specific deployment
before the first external pilot caller is allowed in.

Rules that apply to the whole list:

- **No real customer data** in tests, smokes, or examples — synthetic or
  sanitized only.
- **No raw API keys** in docs, logs, metrics, audit events, error bodies, or
  command examples. Use placeholders (`REPLACE_*`) and read real values from
  the deployment environment, never inline them in a shared shell.
- Run all repo checks with `scripts/limited_beta_preflight.sh` first; it must
  exit 0.

## 0. Pre-flight (repo-local, no deployment needed)

```bash
scripts/limited_beta_preflight.sh
```

- [ ] `scripts/limited_beta_preflight.sh` exits 0 (targeted tests, evals,
      readiness smoke, artifact/doc presence, required tags).

## 1. Required environment toggles (placeholders only)

Set on the deployed instance (never commit real values):

```bash
API_AUTH_ENABLED=true
API_AUTH_KEYS=REPLACE_PILOT_KEY_1,REPLACE_PILOT_KEY_2
API_AUTH_TENANT_MAP=REPLACE_PILOT_KEY_1=pilot_tenant_a,REPLACE_PILOT_KEY_2=pilot_tenant_b
ADMIN_AUTH_ENABLED=true
ADMIN_AUTH_TOKEN=REPLACE_ADMIN_TOKEN
SEARCH_DEBUG_ENABLED=false
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS_PER_MINUTE=60   # size for worker count; per-process limit
```

- [ ] `API_AUTH_ENABLED=true` with non-empty `API_AUTH_KEYS`.
- [ ] `ADMIN_AUTH_ENABLED=true` with a non-empty `ADMIN_AUTH_TOKEN`.
- [ ] `SEARCH_DEBUG_ENABLED=false`.
- [ ] `RATE_LIMIT_ENABLED=true`, budget sized for the worker count
      (limiter is per-process — see `docs/security_operations.md`).
- [ ] Served via the `production_safe` product profile (similar auto-answer,
      LLM answer/rerank, debug comparison all off).

## 2. Tenant map requirement

- [ ] `API_AUTH_TENANT_MAP` maps **every** pilot key to **only** its allowed
      `tenant_id`(s); no key is left unmapped (unmapped valid keys fail closed
      with 403).
- [ ] The mapped tenant ids match the product tenant mapping
      (`configs/product_tenants/default.json`).

## 3. TLS termination requirement

- [ ] TLS terminated in front of the container (nginx/caddy reference in
      `docs/operations.md`); `:8000` is never exposed directly.
- [ ] HTTP redirects to HTTPS; SSE buffering disabled for `/chat/stream`.

## 4. Data onboarding (synthetic / sanitized only)

- [ ] Dry-run onboarding completed for each pilot tenant
      (`pilot_tenant_onboarding_runbook.md`):

```bash
.venv/bin/python scripts/onboard_documents_dry_run.py \
  --input-dir <sanitized_docs_dir> --tenant-id <pilot_tenant>
```

- [ ] Import manifest reviewed and clean (no `duplicate_ids`,
      `duplicate_texts`, `tenant_mismatches`, `unexpected_tenants`,
      `collisions`):

```bash
.venv/bin/python scripts/import_manifest.py \
  --inputs <canonical_jsonl>... --output runs/onboarding/<tenant>/import_manifest.json \
  --expected-tenant <pilot_tenant>
```

- [ ] Knowledge manifest generated and reviewed for the beta corpus
      (`docs/knowledge_manifest.md`).
- [ ] Ingest done **only** into an explicit non-production / pilot collection
      (the onboarding tool refuses the production/default collection).

## 5. Backup and restore rehearsal (before launch)

- [ ] Fresh backup taken and stored off-host:

```bash
bash scripts/backup.sh --output-dir backups
```

- [ ] Restore rehearsal into a staging dir succeeds (hash-verified, non-
      destructive):

```bash
bash scripts/restore.sh backups/chatbot_backup_<TS>.tar.gz --target /tmp/restore_check
```

## 6. Live deploy smoke (against the actual deployment)

- [ ] Container deploy smoke passes (synthetic data only):

```bash
bash scripts/deploy_smoke.sh
```

- [ ] `/health` returns 200 (unauthenticated by design, never rate limited):

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<host>/health        # 200
```

- [ ] `/metrics?format=prometheus` serves the text exposition (labels are
      enum-only; no keys, fingerprints, or query text):

```bash
curl -s 'https://<host>/metrics?format=prometheus' | head
```

- [ ] Rotated-key smoke from `docs/security_operations.md` confirms a valid
      pilot key returns 200 for its tenant and 403 for others; missing key
      returns 401.

## 7. Observability and alerting

- [ ] Alert thresholds wired per `docs/operations.md` (Alert thresholds):
      provider-error rate, fallback rate, guard-trip rate, 429 rate
      (`api_rate_limited_total`), auth-rejection rate
      (`api_auth_rejection_total`), `/health` failures.
- [ ] Per-process counter caveat accounted for in the scraper aggregation.

## 8. Scope and process controls

- [ ] Pilot tenant **allowlist** is explicit and small; no open self-serve
      signup. Only allowlisted tenant ids are configured in the tenant map.
- [ ] Human-in-the-loop review process is staffed: the review queue
      (`/admin/review`) is monitored and `human_review_requested` feedback is
      triaged during the pilot.
- [ ] Pilot scope and exit criteria are documented
      (`pilot_tenant_onboarding_runbook.md`).

## 9. Rollback readiness

- [ ] Rollback **owner** named and on call.
- [ ] Rollback runbook reviewed (`limited_beta_rollback_runbook.md`); the
      revert tag and restore command are identified before launch:
  - revert to previous tag, e.g. `git checkout prompt025-observability-beta-gate`
  - restore: `bash scripts/restore.sh <backup> --in-place --source-dir .`

## 10. Final go

- [ ] All boxes above checked for this deployment.
- [ ] Decision recorded with date, deployment id, pilot tenants, and rollback
      owner. Re-run `scripts/limited_beta_preflight.sh` and attach its output.
