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
- rate limiting at the proxy is a stopgap until the application-level
  limiter ships (separate security-ops batch)

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
