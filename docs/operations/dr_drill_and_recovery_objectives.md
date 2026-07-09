# KuraDen Disaster Recovery Drill & Recovery Objectives

Backup/restore DR drill + proposed recovery objectives. Single-node today (no
HA); objectives below are **commercial ASSUMPTIONS to agree per deployment, not
guarantees**. Synthetic/local data only in the drill; no Docker, no secrets.

## DR drill (synthetic)

    scripts/dr_drill.sh

Creates a throwaway synthetic source with a `vectorstore/`, backs it up
(`backup.sh`), restores to a staging target (`restore.sh`, hash-verified), and
asserts the restored content matches. Exits non-zero on any mismatch. Never
touches the repo's real `vectorstore/`/`data/`/`runs/`. Backed by
`tests/test_dr_drill.py`.

## Proposed recovery objectives (ASSUMPTIONS — agree per deployment)

| Objective | Proposed assumption | Driver |
| --- | --- | --- |
| RPO (max data loss) | = backup cadence (e.g. **24h** with nightly backup; less with more frequent) | single-node file backup |
| RTO (max downtime) | **a few hours** (manual restore + restart on the same/replacement host) | manual, single-node |
| Backup integrity | 100% on tested archives (sha256 manifest verified) | `restore.sh` |

Frequent backups reduce RPO; HA (future, Prompt066) would reduce RTO. State these
as assumptions in the SLA, not guarantees.

## Restore-test report template

- Date: `<DATE>`  Operator: `<NAME>`  Archive: `<chatbot_backup_TS.tar.gz>`
- Restore mode: staging (non-destructive) / in-place
- Hash verification: `<pass/fail>`  Content match: `<pass/fail>`
- Restore duration: `<minutes>` (informs RTO)
- Notes: `<no secrets / no customer data>`

## Failure handling

- Manifest hash mismatch → archive is corrupt/incomplete; use the prior archive;
  investigate the backup window (in-flight chroma write — take backups with the
  API stopped).
- Restore-content mismatch → stop; do not promote; escalate per the incident
  runbook.

## Cadence (recommended)

- Daily backup; keep 7 daily + 4 weekly off-host; **verify one restore per
  month** (staging mode is non-destructive) via `scripts/dr_drill.sh` patterns.
