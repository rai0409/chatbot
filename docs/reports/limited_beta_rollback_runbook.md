# Limited Beta Rollback Runbook

How to pull a limited-beta deployment back to a known-good state. Scope is the
limited external beta only. **No secrets, no real tenant names in this doc** —
use placeholders and read real values from the deployment environment.

The rollback **owner** (named in `limited_beta_launch_checklist.md` §9) drives
this runbook and is the single decision-maker during an incident.

## Rollback triggers

Initiate rollback when any of these hold during the beta:

- a credential leak is suspected or confirmed (key in logs, repo, ticket, or
  shared with the wrong party);
- error/abuse signals breach alert thresholds and do not recover:
  provider-error rate, fallback rate, guard-trip rate, 429 rate
  (`api_rate_limited_total`), or auth-rejection rate
  (`api_auth_rejection_total`) — see `docs/operations.md`;
- `/health` fails repeatedly (liveness/readiness);
- a pilot tenant reports wrong-tenant data exposure or clearly incorrect
  answers at scale;
- a bad deploy (regression found in smoke or by a pilot) needs reverting.

## 1. Immediate containment

1. Notify the rollback owner and start an incident note (timestamp, trigger,
   who is acting). Record the deployment id and the currently deployed tag.
2. Decide blast radius: single tenant vs. whole beta. When in doubt, contain
   the whole beta — the pilot is small by design.

## 2. Disable traffic at the reverse proxy

Stop external traffic before changing anything server-side. At the proxy
(nginx/caddy, see `docs/operations.md`):

- return `503` for the app location (maintenance), **or**
- stop/disable the proxy site, **or**
- remove the upstream so no requests reach `:8000`.

Keep `/health` reachable for your own monitoring if your proxy allows it.

- [ ] External traffic is blocked at the proxy.

## 3. Disable or rotate compromised keys

If a key leak triggered the rollback (see `docs/security_operations.md`,
"Leaked-key response"):

1. Remove the leaked key from `API_AUTH_KEYS` **and** `API_AUTH_TENANT_MAP`;
   restart the API. The affected client breaks until re-keyed — intended.
2. Issue a replacement key out of band and update both env vars together (a
   valid key missing from a non-empty tenant map fails closed with 403).
3. If broad compromise is suspected, rotate **all** pilot keys at once.

- [ ] Compromised key(s) removed/rotated; API restarted with the new env.

## 4. Restore from a hash-verified backup (if data is suspect)

Only if pilot data/state is corrupted or must be reverted. Backups embed
`backup_manifest.sha256` and restore verifies every hash.

```bash
# inspect/verify into a staging dir first (non-destructive)
bash scripts/restore.sh backups/chatbot_backup_<TS>.tar.gz --target /tmp/restore_check

# restore over live data (stop the API first; explicit opt-in)
bash scripts/restore.sh backups/chatbot_backup_<TS>.tar.gz --in-place --source-dir .
```

- [ ] Restore verified (hashes pass) before any in-place restore.
- [ ] In-place restore done with the API stopped.

## 5. Revert to a previous local git tag

Roll the code/config back to the last known-good tag (local only — **no
remote push**). Known-good tags in order:

- `prompt025-observability-beta-gate`
- `prompt024-security-ops`
- `prompt023-deploy-ops`

```bash
git status --short          # confirm a clean tree (stash/branch local edits first)
git checkout prompt025-observability-beta-gate
# rebuild the image from this checkout before bringing traffic back
bash scripts/deploy_smoke.sh
```

- [ ] Deployment rebuilt from the chosen known-good tag.

## 6. Re-run smoke checks after rollback

Before re-enabling traffic:

```bash
scripts/limited_beta_preflight.sh
bash scripts/deploy_smoke.sh
curl -s -o /dev/null -w '%{http_code}\n' https://<host>/health        # 200
```

- [ ] `limited_beta_preflight.sh` exits 0.
- [ ] Deploy smoke passes; `/health` is 200.
- [ ] Rotated-key smoke (valid key 200 for its tenant, 403 for others,
      missing key 401) passes per `docs/security_operations.md`.

Then re-enable the proxy (reverse §2) only if the trigger is resolved.

## 7. Audit-log review window

Scope the audit review (`runs/audit/*.jsonl`) to the incident:

- audit events do **not** record per-key identifiers (and never raw keys), so
  filter by `tenant_id` and `timestamp` from the moment of suspected exposure
  to the containment restart;
- look for anomalies the legitimate pilot client would not produce: volume
  spikes, off-hours traffic, probing questions;
- record findings (affected tenants, window, suspected vector) in the incident
  note. Apply the same confidentiality to audit logs as to source documents.

## 8. Pilot communication template (placeholders only)

> Subject: [BETA] Service interruption for <PILOT_PROGRAM_NAME>
>
> Hello <PILOT_CONTACT_NAME>,
>
> During our limited beta we temporarily paused <PILOT_PROGRAM_NAME> at
> <UTC_TIMESTAMP> to investigate <SHORT_NEUTRAL_REASON, e.g. "an operational
> issue">. No action is required from you. <IF_KEY_ROTATED: We are issuing you
> a new access key separately; please switch to it and discontinue the
> previous one.>
>
> We will confirm when service resumes. Next update by <UTC_TIMESTAMP>.
>
> — <TEAM_NAME>

Do not include secrets, raw keys, real tenant identifiers, or another pilot's
information in any communication.

## 9. Close-out

- [ ] Trigger resolved and verified by smoke.
- [ ] Traffic re-enabled (or beta intentionally kept paused).
- [ ] Incident note completed: trigger, actions, audit window, affected
      tenants, follow-ups, and any re-evaluation triggers for the beta
      assessment.
