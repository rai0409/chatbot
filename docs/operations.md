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
