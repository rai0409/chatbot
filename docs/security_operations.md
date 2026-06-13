# Security Operations

Security-ops reference for private deployments (Prompt024). Covers
application-level rate limiting, zero-downtime API key rotation, leaked-key
response, and secrets handling. Nothing here reads `.env`; all examples use
placeholders — never paste real keys into a terminal history, ticket, or doc.

## Application-level rate limiting

Default OFF. Knobs (see `.env.example` and
`docs/production_readiness_checklist.md`):

- `RATE_LIMIT_ENABLED` (default `false`)
- `RATE_LIMIT_REQUESTS_PER_MINUTE` (default `60`)

Behavior when enabled:

- enforced on the API-key-protected POST endpoints (`/chat`, `/chat/stream`,
  `/chat/product-preview`, `/chat/feedback`, `/search`) **after**
  authentication — 401/403 rejections never consume budget
- `/health` and `/metrics` are never limited
- budget is keyed by the sha256-derived API key fingerprint computed by the
  auth layer; raw keys never enter limiter state. Requests without a
  fingerprint (auth disabled) share one anonymous bucket.
- over-budget requests get `429` with a `Retry-After: <seconds>` header and
  body `{"detail": "rate limit exceeded"}`
- the limiter is in-process and fixed-window: with N uvicorn workers the
  effective global limit is up to N × the configured per-minute budget (same
  caveat as the `/metrics` counters). Keep reverse-proxy limits as defense
  in depth; distributed limiting is out of scope.

## Zero-downtime API key rotation

`API_AUTH_KEYS` is comma-separated, so old and new keys can be valid
simultaneously — rotation needs no downtime window.

1. **Generate the new key** out of band (e.g. `openssl rand -hex 32` on the
   operator machine). Do not echo it into shared shells or logs.
2. **Add the new key alongside the old one** in the deployment's `.env`:

   ```bash
   # before
   API_AUTH_KEYS=OLD_KEY
   # during rotation (both valid)
   API_AUTH_KEYS=OLD_KEY,NEW_KEY
   ```

3. **Update the tenant map in the same edit** — a valid key missing from a
   non-empty `API_AUTH_TENANT_MAP` fails closed with 403, so the new key must
   carry the same tenant grants as the key it replaces:

   ```bash
   # before
   API_AUTH_TENANT_MAP=OLD_KEY=tenant_a
   # during rotation (same tenants for both)
   API_AUTH_TENANT_MAP=OLD_KEY=tenant_a,NEW_KEY=tenant_a
   ```

4. **Restart/reload the API** to pick up the env (e.g.
   `docker compose up -d` recreates the container with the new env).
5. **Verify both keys work** (smoke commands below), then **roll clients**
   to the new key at their own pace.
6. **Remove the old key** from both `API_AUTH_KEYS` and
   `API_AUTH_TENANT_MAP` once all clients are migrated, restart again, and
   verify the old key is now rejected with 403.

### Rotation smoke commands

Same pattern as `scripts/deploy_smoke.sh` (status-code asserts, keys read
from the environment — never inline real keys in the command line of a
shared host; prefer exporting from a restricted file):

```bash
BASE_URL=https://chatbot.example.co.jp

# health stays unauthenticated
curl -s -o /dev/null -w "%{http_code}\n" "$BASE_URL/health"            # expect 200

# during rotation: both keys accepted
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BASE_URL/chat" \
  -H 'Content-Type: application/json' -H "X-Api-Key: ${NEW_KEY}" \
  -d '{"question":"smoke"}'                                            # expect 200
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BASE_URL/chat" \
  -H 'Content-Type: application/json' -H "X-Api-Key: ${OLD_KEY}" \
  -d '{"question":"smoke"}'                                            # expect 200

# after old-key removal
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BASE_URL/chat" \
  -H 'Content-Type: application/json' -H "X-Api-Key: ${OLD_KEY}" \
  -d '{"question":"smoke"}'                                            # expect 403

# no key at all
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BASE_URL/chat" \
  -H 'Content-Type: application/json' -d '{"question":"smoke"}'        # expect 401
```

For a tenant-mapped deployment also assert the tenant grants carried over:
a `/chat` request with the new key and an authorized `tenant_id` returns
200, and an unauthorized `tenant_id` returns 403.

## Leaked-key response

A leaked key is removed immediately, not rotated gracefully:

1. **Remove the leaked key now** from `API_AUTH_KEYS` and
   `API_AUTH_TENANT_MAP`; restart the API. Affected clients break until they
   receive a replacement key — that is the intended trade-off.
2. **Issue a replacement key** via the rotation steps above.
3. **Audit the tenant map**: confirm which tenants the leaked key could
   access; only those tenants' data is in scope for the review.
4. **Review the audit log window** (`runs/audit/*.jsonl`): audit events do
   not record per-key identifiers (and never raw keys), so scope the review
   by `tenant_id` (the tenants from step 3) and `timestamp`, from the moment
   of suspected exposure to the removal restart. Look for query patterns the
   legitimate client would not produce (volume spikes, off-hours traffic,
   probing questions).
5. **Record the incident**: exposure vector, window, affected tenants,
   and the follow-up (e.g. notify the tenant per contract).

## Secrets handling

### .env rules

- **Never commit `.env`** — it is gitignored; `.env.example` carries
  placeholders only. If a real value ever lands in git history, treat it as
  leaked and rotate.
- **Restrict file permissions** on deployment hosts: `chmod 600 .env`,
  owned by the service user.
- **Never bake `.env` into images**: `.dockerignore` already excludes it
  (and `vectorstore/`, `data/`, `runs/`); env is injected at container start
  via compose `env_file`/environment. `scripts/deploy_smoke.sh` proves this
  end to end — it builds the image and boots it with a **throwaway env file
  written from scratch**, so a successful smoke run demonstrates the image
  itself carries no secrets and gets everything from runtime env.
- **Placeholder discipline**: docs, tests, and smoke scripts use clearly
  fake values (`sk-REPLACE_ME`, `sk-smoke-placeholder-not-a-real-key`).
  Anything that looks real in a committed file is a finding.
- Raw API keys must never appear in logs, `/metrics`, audit events, error
  bodies, or rate-limiter state; only sha256-derived fingerprints are used
  for correlation (enforced by tests).

### External secret stores (optional hardening)

Documentation only — no integration ships with this repo. For deployments
that outgrow file-based `.env`:

- inject env at start from a managed store (e.g. AWS Secrets Manager / SSM,
  GCP Secret Manager, Vault, or `docker compose` secrets) instead of a file
  on disk; the application reads plain env vars either way, so no code
  change is needed
- scope store access to the service host/role; audit reads
- store rotation then becomes: update the secret version, restart the
  service — the comma-separated `API_AUTH_KEYS` overlap procedure above is
  unchanged
