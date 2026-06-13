# Operations Guide

Deploy-ops reference for private deployments (Prompt023). Covers the live
deploy smoke, backup/restore, reverse proxy/TLS reference configs, and log
retention. Nothing here reads `.env`; all examples use placeholders.

## Deploy smoke

```bash
bash scripts/deploy_smoke.sh
```

What it does:

- validates `docker-compose.yml` syntax (`docker compose config --quiet`)
- builds the image and starts an **isolated** compose project
  (`chatbot-deploy-smoke`, port `127.0.0.1:18000`, override with
  `DEPLOY_SMOKE_PORT`)
- uses a **throwaway env file written from scratch** (never copies `.env`)
  and temp-dir volumes — the repo's live `vectorstore/ index/ data/ runs/`
  are never mounted
- asserts the HTTP contract with synthetic data only:
  `/health` 200, `/metrics` 200, `/chat` 401 without key / 403 wrong key,
  `/search/debug` 404 when disabled, and a **deterministic
  `approved_exact_match` answer** through `/chat` (no OpenAI key, no
  embeddings, no model downloads needed)
- tears everything down including volumes, also on failure

## Backup / restore

```bash
# nightly backup (cron-friendly); archives vectorstore/, data/approved_qa/, runs/audit/
bash scripts/backup.sh --output-dir backups

# verify + inspect into a staging dir (default, safe)
bash scripts/restore.sh backups/chatbot_backup_<TS>.tar.gz --target /tmp/restore_check

# restore over live data (explicit opt-in; stop the API first)
bash scripts/restore.sh backups/chatbot_backup_<TS>.tar.gz --in-place --source-dir .
```

- Every archive embeds `backup_manifest.sha256`; restore always verifies all
  hashes and fails non-zero on any mismatch.
- `backups/` is gitignored — move archives to off-host storage as part of
  the schedule.
- Recommended cadence: daily backup, keep 7 daily + 4 weekly; verify one
  restore per month (staging mode is non-destructive).
- Consistency: take backups while the API is stopped, or accept that an
  in-flight chroma write may invalidate that night's archive (the manifest
  check will catch it at restore time).

### Persistence reload / restore isolation verification

```bash
# synthetic, non-production, no .env, no network/model downloads
scripts/persistence_isolation_check.sh
```

- Proves, with synthetic two-tenant data in a temp store under a
  **non-production** collection (`pilot_persist_check_v1`), that stored records
  and the embedding fingerprint survive a Chroma client reload (simulated
  restart) and a hash-verified backup/restore, and that tenant isolation holds
  after both (each tenant retrieves only its own chunks).
- Backed by `tests/test_durable_multitenant_persistence.py`. The production /
  default vectorstore and collection are never opened or mutated.
- Scope note: this proves **single-node** durability and isolation on the local
  Chroma `PersistentClient` — not a managed, HA, or concurrent-writer
  multi-tenant database. Those remain general-production items.

## End-user chat UI (`GET /chat-ui`)

A minimal browser UI for non-engineer pilot users is served at **`GET /chat-ui`**
(static page `webapi/static/chat.html`, vanilla HTML/CSS/JS, no build/CDN).

- It **reuses the existing endpoints**: `POST /chat/stream` (SSE) for answers
  and `POST /chat/feedback` for feedback. It does **not** change answering,
  the `too_general` guard, tenant authorization/isolation, rate-limiting, or
  `production_safe` behavior — those stay enforced on the data endpoints.
- It shows the answer, **citations/sources**, an **abstain/no-answer** notice
  when the system has no supported answer, and good / bad / human-review
  feedback controls.
- **Access uses the existing API key model.** No key is hardcoded in the page;
  when `API_AUTH_ENABLED=true` the operator enters the pilot key at runtime
  (kept in browser memory only, sent as `X-Api-Key`). With API auth disabled
  the page works locally with no key, exactly like `/chat` today.
- Behind TLS in production (see below); never expose `:8000` directly. Backed
  by `tests/test_enduser_chat_ui.py`.

## Reverse proxy / TLS reference

The container serves plain HTTP on `:8000` and must not be exposed directly.
Terminate TLS in front of it.

### nginx

```nginx
server {
    listen 443 ssl;
    server_name chatbot.example.co.jp;

    ssl_certificate     /etc/ssl/certs/chatbot.crt;
    ssl_certificate_key /etc/ssl/private/chatbot.key;

    client_max_body_size 1m;          # /chat payloads are small JSON
    proxy_read_timeout   120s;        # LLM generation latency
    proxy_send_timeout   30s;

    location /health {
        proxy_pass http://127.0.0.1:8000/health;   # unauthenticated by design
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;                        # required for /chat/stream SSE
    }
}

server {                                            # redirect plain HTTP
    listen 80;
    server_name chatbot.example.co.jp;
    return 301 https://$host$request_uri;
}
```

### caddy

```caddy
chatbot.example.co.jp {
    request_body {
        max_size 1MB
    }
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1     # SSE streaming for /chat/stream
        transport http {
            read_timeout 120s
        }
    }
}
```

Notes:

- keep `API_AUTH_ENABLED=true` with per-deployment keys even behind TLS;
  the proxy is transport security, not authentication
- `/search/debug` should stay disabled (`SEARCH_DEBUG_ENABLED=false`) on any
  exposed deployment
- enable the application-level limiter (`RATE_LIMIT_ENABLED=true`, see
  `docs/security_operations.md`) on exposed deployments; proxy-level rate
  limiting remains useful as defense in depth

## Enterprise auth bridge (optional, default off)

A default-off bridge lets a customer-controlled **SSO/IdP gateway** (SAML,
OIDC, LDAP/AD, Okta, Azure AD — all **outside** this app) authenticate the user
and forward trusted identity headers. This app only verifies a shared trust
signal and maps the identity onto the existing tenant authorization; it is the
integration boundary, not an IdP.

Enable with:

```bash
ENTERPRISE_AUTH_ENABLED=true
ENTERPRISE_AUTH_TRUST_TOKEN=<shared secret only the proxy knows>
ENTERPRISE_AUTH_TENANT_MAP=entTenantA=tenant_a,grpX=tenant_b|tenant_c
```

The trusted proxy must (a) authenticate the user against the real IdP, (b)
**strip any client-supplied** `X-Enterprise-*` headers, then (c) inject:

- `X-Enterprise-Auth-Trust: <ENTERPRISE_AUTH_TRUST_TOKEN>` (required)
- `X-Enterprise-Tenant: <enterprise identity/tenant/group>` (mapped to allowed tenants)
- `X-Enterprise-User` / `X-Enterprise-Email` / `X-Enterprise-Groups` (optional; only a sha256 fingerprint is retained)

nginx sketch (the proxy sets the trust token from its own secret, never from
the client):

```nginx
proxy_set_header X-Enterprise-Auth-Trust "REPLACE_WITH_PROXY_SECRET";
proxy_set_header X-Enterprise-Tenant     $sso_tenant;   # from the IdP assertion
# and clear inbound spoofing:
proxy_set_header X-Enterprise-User       $sso_user;
```

Behavior:

- **Disabled (default):** `X-Enterprise-*` headers are never read; the API key
  path is exactly unchanged.
- **Enabled:** enterprise headers are honored only with a matching trust token;
  a missing trust token fails 401, an invalid one 403, an unmapped identity 403.
  The mapped identity sets `allowed_tenants`; the request `tenant_id` is still
  checked against it (cross-tenant access is rejected, never broadened). The API
  key path keeps working for requests without enterprise headers. No trust
  token, API key, raw identity, prompt, or document text is logged or returned
  (only a fingerprint and stable enum counters such as `api_enterprise_auth_total`
  / `api_auth_rejection_total`). See `webapi/enterprise_auth.py`.

## Log retention (runs/audit/)

What the audit JSONL contains: request/trace ids, tenant id, question text,
answer mode, guard reason, citation counts, latency. What it deliberately
does **not** contain: API keys, full candidate payloads, approved answer
bodies (IDs only).

Policy guidance (set per deployment contract):

- rotate `runs/audit/*.jsonl` daily or at 100 MB (e.g. logrotate `copytruncate`)
- retention placeholder: 90 days online, then delete or move to the
  customer's archival storage — align with the tenant's data agreement
- audit logs contain user question text: treat them with the same
  confidentiality as the source documents; include them in tenant
  offboarding deletion
- backups include `runs/audit/` — retention applies to archives too

## Metrics export

`GET /metrics` serves the in-process counters in two formats:

```bash
# JSON (default) — uptime, request totals, and a counters object
curl -s http://127.0.0.1:8000/metrics

# Prometheus text exposition (version 0.0.4) for a scraper
curl -s 'http://127.0.0.1:8000/metrics?format=prometheus'
```

The Prometheus output is generated from the same counters; no extra
dependency or collector process is involved. Counter labels are stable
enum-like values only (answer modes, guard reasons, error types, auth
rejection reasons, rate-limit bucket) — never API keys, key fingerprints,
or query text.

**Per-process caveat (important for alerting):** counters are per-process.
With N uvicorn workers each worker exposes only its own numbers, and a
scrape hits one worker. Aggregate across workers/instances in the scraper
(e.g. `sum without (instance)`), and size rate-based thresholds with the
worker count in mind. This is the same caveat as the JSON counters and the
rate limiter.

Counters of interest (all `_total`):

| Counter | Labels | Meaning |
| --- | --- | --- |
| `app_requests_total` | — | requests handled by this process |
| `app_error_requests_total` | — | errored requests in this process |
| `chat_answer_mode_total` | answer mode | grounded / fallback / approved_exact_match / candidate_only |
| `chat_guard_reason_total` | guard reason | confidence-guard trips by reason |
| `chat_used_fallback_total` | — | answers that fell back to no-answer/extractive |
| `chat_provider_error_total` | error type | LLM provider errors (rate_limited, timeout, …) |
| `chat_cache_hit_total` | — | answer-cache hits |
| `api_rate_limited_total` | `authenticated`/`anonymous` | requests rejected with 429 by the limiter |
| `api_auth_rejection_total` | `missing_credentials`/`invalid_credentials`/`tenant_forbidden` | requests rejected with 401/403 by API auth |

## Alert thresholds

Starting points only — **tune per deployment** against an observed baseline,
and remember the per-process caveat above. Rates are over a rolling window
(suggest 5–15 min) unless noted.

| Signal | Metric / source | Starting threshold | Rationale |
| --- | --- | --- | --- |
| Provider error rate | `chat_provider_error_total` ÷ chat requests | warn >2%, page >10% over 5 min | sustained LLM-provider failures degrade answers; spikes mean an outage or quota exhaustion |
| Fallback rate | `chat_used_fallback_total` ÷ chat requests | warn >30%, page >60% over 15 min | high no-answer/extractive fallback means retrieval or corpus coverage regressed |
| Guard-trip rate | `chat_guard_reason_total` (sum) ÷ chat requests | warn >40% over 15 min | the confidence guard abstaining often signals a query/corpus mismatch or a mis-calibrated threshold |
| 429 rate | `api_rate_limited_total` | warn >0 sustained, page on sharp climb | persistent 429s mean a client exceeds its budget or an abusive caller — investigate before raising limits |
| Auth-rejection rate | `api_auth_rejection_total` | warn on `invalid_credentials` spikes; page on sustained bursts | a burst of `invalid_credentials`/`tenant_forbidden` suggests a misconfigured client or credential probing |
| `/health` failures | `GET /health` non-200 from the uptime check | page on 2 consecutive failures | the liveness/readiness signal; back it with the reverse-proxy or external monitor, not `/metrics` |

Notes:

- thresholds are starting points to calibrate against your own baseline; the
  right numbers depend on traffic mix and tenant behavior
- `/health` is unauthenticated by design and is the canonical liveness probe;
  alert on it from the proxy or an external monitor (it is never rate limited)
- denominators (chat request counts) come from `app_requests_total` and the
  `chat_answer_mode_total` buckets; compute rates in the scraper

### Local alert checker (no external monitor required)

For a limited on-prem/private pilot you can evaluate these thresholds locally,
without Prometheus/Grafana/cloud, against a saved `/metrics` JSON snapshot:

```bash
# evaluate a snapshot (exit 0 OK / 1 WARN / 2 CRITICAL)
curl -s http://127.0.0.1:8000/metrics > snap.json
python scripts/alert_check.py snap.json
# or stream it
curl -s http://127.0.0.1:8000/metrics | python scripts/alert_check.py -   --json
```

- The thresholds live in `webapi/alerting.py` (`DEFAULT_THRESHOLDS`) and mirror
  the table above; override per deployment by editing that dict or passing your
  own to `evaluate_alerts(payload, thresholds=...)`.
- Signals evaluated: chat **error rate**, **fallback (abstain/no-answer) rate**,
  **guard-trip rate**, **zero-success on a non-empty window** (CRITICAL),
  **429** and **auth-rejection** counts, and — when present —
  **feedback human-review rate** (`chat_feedback_total`) and an optional
  **p95 latency** field. Rate signals report OK below `min_requests_for_rate`
  to avoid alerting on tiny windows.
- The checker reads only metric names (stable enum labels) and integer counts:
  no API keys, prompts, document text, or `.env` values. It makes no network
  call and is per-process (same caveat as `/metrics`).
