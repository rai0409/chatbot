# Prompt061: On-Prem Install / Upgrade / Release Packaging

Implementation report. Adds an on-prem install/upgrade/rollback + release-
packaging workflow with a **dependency-freeze verification** gate. No product
runtime change; no Docker run; no deploy.

## Implementation summary

- `scripts/release_check.py` (new, executable) — verifies the active environment
  matches the pinned `requirements.txt` (release gate): pinned `name==ver` must
  match the installed version; ranged deps must be installed. Exits non-zero on
  any missing package or pin mismatch. No `.env`, no network, no secrets.
- `docs/operations/onprem_install_upgrade_release.md` (new) — release-bundle
  checklist, environment prerequisites, install (incl. `release_check.py` +
  preflight smoke), upgrade (backup-first → freeze check → preflight), and
  rollback (previous tag + restore) steps.
- `tests/test_release_packaging.py` (new).

## Verification results

- `release_check.py` on the current env: **RELEASE CHECK: OK** (all 14 deps;
  pinned Authlib/cryptography/oauthlib/requests-oauthlib match).
- `tests/test_release_packaging.py` + `test_deploy_ops.py`: **12 passed** (incl.
  pin-mismatch and missing-package detection).
- Full suite: **854 passed, 0 failed** (+4). `limited_beta_preflight.sh` exit 0.
  Full suite WAS run.

## What is NOT validated externally

- A real packaged release on a fresh customer host (offline wheel mirror, real
  TLS/proxy) is operator-run at install time; here the freeze check + smoke are
  validated locally.

## Final judgment: PASS

## Next recommendation

Prompt062 — security / audit-log export / retention / compliance pack.
