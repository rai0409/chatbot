#!/usr/bin/env bash
set -euo pipefail

cd /home/rai/chatbot
source .venv/bin/activate

export PYTHONPATH=.
export CHROMA_COLLECTION=chatbot_chunks_v1_aligned_candidate
export EMBED_PROVIDER=local
export LOCAL_EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
export CHAT_GENERATION_MODE=extractive
export OPENAI_API_KEY=
export API_AUTH_ENABLED=false
export APPROVED_QA_ENABLED=true
export APPROVED_QA_PATH=artifacts/free_extractive_chat_mode/approved_qa_118_runtime.jsonl

ARTIFACT_DIR=artifacts/grounded_extractive_quality
CHAT_URL=http://127.0.0.1:8011/chat
HEALTH_URL=http://127.0.0.1:8011/health

mkdir -p "$ARTIFACT_DIR"

stop_existing_8011_uvicorn() {
  local pids
  pids="$(lsof -tiTCP:8011 -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi
  for pid in $pids; do
    local cmd
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [[ "$cmd" == *uvicorn* && "$cmd" == *webapi.main:app* ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}

stop_existing_8011_uvicorn

uvicorn webapi.main:app --host 127.0.0.1 --port 8011 > "$ARTIFACT_DIR/uvicorn.log" 2>&1 &
UVICORN_PID=$!

cleanup() {
  if kill -0 "$UVICORN_PID" 2>/dev/null; then
    kill "$UVICORN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

python - <<'PY'
import json
import time
import urllib.request

url = "http://127.0.0.1:8011/health"
last = None
for _ in range(120):
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if payload.get("status") == "ok":
            break
    except Exception as exc:
        last = exc
        time.sleep(0.5)
else:
    raise SystemExit(f"health check did not become ready: {last}")
PY

curl -sS "$HEALTH_URL" > "$ARTIFACT_DIR/health.json"
python -m compileall config.py rag_core webapi tools

python tools/evaluate_grounded_extractive_quality.py \
  --cases "$ARTIFACT_DIR/grounded_extractive_quality_cases.jsonl" \
  --chat-url "$CHAT_URL" \
  --output-dir "$ARTIFACT_DIR" \
  --timeout 60

echo
echo "grounded extractive quality check completed"
echo "artifacts: $ARTIFACT_DIR"
