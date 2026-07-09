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
