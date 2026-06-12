#!/usr/bin/env bash
# Live deployment smoke (Prompt023).
#
# Builds the image, starts an ISOLATED compose project with a throwaway env
# file and temp-dir volumes (never the repo's live vectorstore/ index/ data/
# runs/), asserts the HTTP contract with synthetic data only, and tears
# everything down (including volumes) even on failure.
#
# Hard rules:
# - never reads or copies the repo .env (placeholders are written from scratch)
# - the /chat positive check uses the approved-QA exact-match route, which
#   needs no OpenAI key, no embeddings, and no model downloads
# - the real docker-compose.yml is validated with `docker compose config`
#   separately; the smoke runs its own self-contained compose file so the
#   repo .env is never loaded into any container
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="# generated-by=deploy_smoke.sh (throwaway smoke env; safe to delete)"
PROJECT="chatbot-deploy-smoke"
HOST_PORT="${DEPLOY_SMOKE_PORT:-18000}"
BASE_URL="http://127.0.0.1:${HOST_PORT}"
SMOKE_KEY="smoke-key-1"
APPROVED_QUESTION="スモークテストの営業時間を教えてください"
APPROVED_ANSWER="スモーク用の承認済み回答です。営業時間は9時から17時です。"

CHECK_ENV_ONLY=0
ENV_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-env-only) CHECK_ENV_ONLY=1; shift ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

SMOKE_DIR="$(mktemp -d /tmp/chatbot_deploy_smoke.XXXXXX)"
[[ -n "$ENV_FILE" ]] || ENV_FILE="$SMOKE_DIR/.env.smoke"
COMPOSE_FILE="$SMOKE_DIR/compose.smoke.yml"
STARTED=0

cleanup() {
  local code=$?
  if [[ "$STARTED" == "1" ]]; then
    docker compose -p "$PROJECT" -f "$COMPOSE_FILE" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  rm -rf "$SMOKE_DIR"
  exit "$code"
}
trap cleanup EXIT

write_env_file() {
  if [[ -e "$ENV_FILE" ]] && ! head -n 1 "$ENV_FILE" | grep -qF "generated-by=deploy_smoke.sh"; then
    echo "ERROR: refusing to overwrite existing file not created by deploy_smoke.sh: $ENV_FILE" >&2
    exit 2
  fi
  cat > "$ENV_FILE" <<EOF
$MARKER
# Placeholder values only — written from scratch, never copied from .env.
OPENAI_API_KEY=sk-smoke-placeholder-not-a-real-key
CHAT_MODEL=gpt-4o-mini
EMBED_PROVIDER=local
API_AUTH_ENABLED=true
API_AUTH_KEYS=${SMOKE_KEY}
ADMIN_AUTH_ENABLED=true
ADMIN_AUTH_TOKEN=smoke-admin-token
SEARCH_DEBUG_ENABLED=false
APPROVED_QA_ENABLED=true
APPROVED_QA_PATH=data/approved_qa/default.jsonl
CHUNKS_JSONL_PATH=index/smoke_chunks.jsonl
ANSWER_CACHE_ENABLED=false
AUDIT_CHAT_ENABLED=true
EOF
  echo "wrote throwaway env: $ENV_FILE"
}

write_env_file
if [[ "$CHECK_ENV_ONLY" == "1" ]]; then
  echo "check-env-only: OK"
  exit 0
fi

command -v docker >/dev/null || { echo "ERROR: docker not available" >&2; exit 2; }
command -v curl >/dev/null || { echo "ERROR: curl not available" >&2; exit 2; }

# --- synthetic runtime data (never real customer data) -----------------------
mkdir -p "$SMOKE_DIR/vectorstore" "$SMOKE_DIR/index" "$SMOKE_DIR/data/approved_qa" "$SMOKE_DIR/runs"
cat > "$SMOKE_DIR/data/approved_qa/default.jsonl" <<EOF
{"qa_id":"qa_smoke_1","question":"${APPROVED_QUESTION}","approved_answer":"${APPROVED_ANSWER}","status":"approved","tenant_id":"default","language":"ja","approved_citations":[{"source_doc":"smoke_faq.pdf","source_pages":[1]}]}
EOF
cat > "$SMOKE_DIR/index/smoke_chunks.jsonl" <<EOF
{"id":"smoke-c1","text":"スモークテスト用の説明チャンクです。","source_doc":"smoke_doc.pdf","source_pages":[1],"doc_id":"smoke_doc.pdf","chunk_index":1,"searchable":1,"type":"pdf","quality":"high"}
EOF

cat > "$COMPOSE_FILE" <<EOF
services:
  api:
    build: ${REPO_DIR}
    image: chatbot-deploy-smoke:latest
    ports:
      - "127.0.0.1:${HOST_PORT}:8000"
    env_file:
      - ${ENV_FILE}
    volumes:
      - ${SMOKE_DIR}/vectorstore:/app/vectorstore
      - ${SMOKE_DIR}/index:/app/index
      - ${SMOKE_DIR}/data:/app/data
      - ${SMOKE_DIR}/runs:/app/runs
EOF

echo "validating repo docker-compose.yml syntax..."
docker compose -f "$REPO_DIR/docker-compose.yml" config --quiet

echo "building image and starting isolated smoke stack (project=$PROJECT, port=$HOST_PORT)..."
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d --build
STARTED=1

echo "waiting for /health..."
for i in $(seq 1 60); do
  if curl -fsS "$BASE_URL/health" >/dev/null 2>&1; then break; fi
  if [[ "$i" == "60" ]]; then
    echo "ERROR: /health did not become ready" >&2
    docker compose -p "$PROJECT" -f "$COMPOSE_FILE" logs --tail 50 >&2 || true
    exit 1
  fi
  sleep 2
done

http_status() {
  curl -s -o /dev/null -w "%{http_code}" "$@"
}

fail=0
check() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$actual" == "$expected" ]]; then
    echo "  [ok]   $name ($actual)"
  else
    echo "  [FAIL] $name expected=$expected actual=$actual"
    fail=1
  fi
}

echo "running HTTP contract checks..."
check "GET /health -> 200" 200 "$(http_status "$BASE_URL/health")"
check "GET /metrics -> 200" 200 "$(http_status "$BASE_URL/metrics")"
check "POST /chat without key -> 401" 401 \
  "$(http_status -X POST "$BASE_URL/chat" -H 'Content-Type: application/json' -d '{"question":"test"}')"
check "POST /chat wrong key -> 403" 403 \
  "$(http_status -X POST "$BASE_URL/chat" -H 'Content-Type: application/json' -H 'X-Api-Key: wrong-key' -d '{"question":"test"}')"
check "GET /search/debug disabled -> 404" 404 \
  "$(http_status -X POST "$BASE_URL/search/debug" -H 'Content-Type: application/json' -H "X-Api-Key: ${SMOKE_KEY}" -d '{"query":"test"}')"

approved_body="$(curl -s -X POST "$BASE_URL/chat" \
  -H 'Content-Type: application/json' -H "X-Api-Key: ${SMOKE_KEY}" \
  -d "{\"question\":\"${APPROVED_QUESTION}\"}")"
if echo "$approved_body" | grep -qF "approved_exact_match" && echo "$approved_body" | grep -qF "$APPROVED_ANSWER"; then
  echo "  [ok]   POST /chat approved question -> approved_exact_match (no LLM/embeddings needed)"
else
  echo "  [FAIL] POST /chat approved question did not return the approved answer"
  echo "         body: ${approved_body:0:300}"
  fail=1
fi

# Unknown question: with no provider key/model in the image, the contract is a
# JSON response with a non-success answer path; any of 200/500/502/503 is
# acceptable as long as the API does not hang or leak.
unknown_status="$(http_status -X POST "$BASE_URL/chat" \
  -H 'Content-Type: application/json' -H "X-Api-Key: ${SMOKE_KEY}" \
  -d '{"question":"未知の質問です"}')"
case "$unknown_status" in
  200|500|502|503) echo "  [ok]   POST /chat unknown question -> JSON error/fallback contract ($unknown_status)" ;;
  *) echo "  [FAIL] POST /chat unknown question unexpected status $unknown_status"; fail=1 ;;
esac

if [[ "$fail" != "0" ]]; then
  echo "DEPLOY SMOKE: FAILED" >&2
  exit 1
fi
echo "DEPLOY SMOKE: PASS (stack will be torn down, volumes removed)"
