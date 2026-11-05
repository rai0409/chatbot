# Production hardening (single-node on-premises)

This page is the operator checklist for a customer deployment. The API refuses
to start with `APP_ENV=production` while any required setting is missing
(`webapi/production_guard.py` lists the problems in the error message).

## 1. Required settings (`.env`)

| Setting | Value |
|---|---|
| `APP_ENV` | `production` (the Docker image sets it; override to `development` only on a developer machine) |
| `API_AUTH_ENABLED` and `API_AUTH_KEYS`, or `ENTERPRISE_OIDC_ENABLED` | authentication for every chat/search route |
| `API_AUTH_TENANT_MAP` | key → tenants; without it a key reaches only the `default` tenant |
| `ADMIN_AUTH_ENABLED=true`, `ADMIN_AUTH_TOKEN` | 32+ random characters |
| `SEARCH_DEBUG_ENABLED=false` | the debug route exposes retrieval internals |
| `TENANT_FILTER_STRICT=true` | the default tenant is filtered too; run `scripts/backfill_tenant_id.py --apply` on every existing collection first (rows without `tenant_id` are invisible otherwise) |
| `EMBED_FINGERPRINT_STRICT=true` | a collection without a complete embedding stamp (provider, model, prefix scheme, normalization, distance, dimension) is refused instead of only logged; collections written by this code base's ingest are stamped completely |
| `ALLOWED_HOSTS` | host names served (Host header check) |
| `METRICS_TOKEN` | 32+ characters; scrape with `Authorization: Bearer <token>` |
| `OIDC_SESSION_SECRET` | 32+ bytes when OIDC is enabled |
| `FORWARDED_ALLOW_IPS` | address of the TLS proxy (only it may set `X-Forwarded-*`) |
| `INGEST_ALLOWED_ROOTS` | directories the admin ingestion may read |

Generate secrets with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

## 2. Network

- The container listens on `127.0.0.1:8000` only (`docker-compose.yml`). Put a TLS
  reverse proxy (nginx, Caddy, IIS ARR) on the same host in front of it.
- Limit the request body at the proxy as well (for example
  `client_max_body_size 2m;`); the API itself rejects bodies above
  `MAX_REQUEST_BYTES` (default 1 MiB) with 413.
- Enterprise header bridge (`ENTERPRISE_AUTH_ENABLED`): the proxy **must delete
  every incoming `X-Enterprise-*` header** from client requests and set them
  only from its own authentication result, for example in nginx:
  `proxy_set_header X-Enterprise-User $authenticated_user;` for each header used,
  and never pass client-supplied ones. Without this anybody can claim any
  identity. Prefer mTLS between proxy and API or a signed assertion when the
  proxy supports it.

## 3. Container

- Pin the base image by digest (see the comment at the top of `Dockerfile`).
- The compose file runs the container with a read-only root file system,
  `cap_drop: ALL`, `no-new-privileges`, memory/CPU/PID limits and a tmpfs `/tmp`.
  Writable state lives only in the mounted volumes: `vectorstore/`, `index/`,
  `data/`, `runs/`, `models_cache/` (Hugging Face cache, `HF_HOME`).
- Pre-download the embedding and reranker models into `models_cache/` on a
  connected machine when the customer network is closed.

## 4. What production mode changes

- `/health` returns only `{"status": "ok"}`; details are on `/admin/health`
  (admin token).
- `/metrics` requires `METRICS_TOKEN`.
- `/docs`, `/redoc`, `/openapi.json` are disabled (`API_DOCS_ENABLED=true` re-enables).
- Conversation history refuses callers without a per-user identity.
- Every response carries `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy` and a CSP (`SECURITY_CSP` to tighten).

## 5. Audit log

- Records carry the actor (key fingerprint, role) and a hash chain;
  verify with `python scripts/verify_audit_chain.py` (exit code 1 on tampering).
- Files rotate at `AUDIT_MAX_BYTES`. Ship rotated files to the customer's log
  archive; keep the chain files together.
- Question text: `AUDIT_QUESTION_MODE=plain|mask|hash|drop` per the customer's policy.
- Alert when `audit_write_failures_total` appears on `/metrics`.

## 6. Not covered yet

- Shared state for several API nodes (Postgres/Redis for audit, conversations,
  jobs, rate limits, caches) — single node only.
- Asynchronous ingestion jobs (a large upload runs inside the request).
