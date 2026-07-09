# Prompt044: Document Ingestion UI & Job Status

Implementation report. Adds an admin-gated document-ingestion **dry-run**
validation surface + job status, reusing the existing import-manifest path. No
vectorstore mutation; production/default collection refused.

## Files changed

- `webapi/ingestion_jobs.py` (new) — wraps `scripts.import_manifest.build_manifest`
  for dry-run validation (duplicate ids / tenant mismatch / collisions), an
  in-memory job registry (`run_dry_run`, `get_job`, `list_jobs`, `reset`), and
  `is_production_collection()` (refuses default/prod + configured collection
  names). Stores issue COUNTS + safe metadata only — no raw document text/secrets.
- `webapi/main.py` — admin-gated endpoints: `GET /admin/ingestion` (page),
  `POST /admin/ingestion/dry-run`, `GET /admin/ingestion/jobs`,
  `GET /admin/ingestion/jobs/{id}`. All behind `require_admin_auth`. Production/
  default collection rejected with 400. Added `ingestion_jobs` import +
  `IngestionDryRunRequest` model.
- `webapi/static/ingestion.html` (new) — minimal admin page to run a dry-run and
  view job history; no hardcoded secret.
- `tests/test_ingestion_ui_job_status.py` (new).
- `docs/reports/prompt044_document_ingestion_ui_and_job_status.md`.

## Safety / no-unsafe-mutation result

- **Dry-run only**: validation builds a manifest; it never ingests and never
  touches any vectorstore. The production/default collection (and configured
  `VECTORSTORE_COLLECTION_NAME`) is refused at both the module and endpoint layer
  (400 / ValueError) — verified.
- All ingestion routes require admin auth server-side (401/403 without a valid
  token) — verified; frontend cannot bypass.
- Job output carries issue counts + file/row metadata only; no raw document
  text, API keys, or admin token leaks (verified). Synthetic data only.

## Verification results

- `tests/test_ingestion_ui_job_status.py` + `test_multiformat_onboarding.py` +
  `test_admin_auth.py`: **32 passed**.
- Full suite: **793 passed, 0 failed** (+7). `product_readiness_smoke.sh` exit 0;
  `limited_beta_preflight.sh` exit 0. Full suite WAS run.

## Final judgment: PASS

## Next recommendation

Prompt045 — enterprise SSO architecture decision (analysis; gates Prompt046).
