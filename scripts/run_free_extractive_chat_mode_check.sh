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

ARTIFACT_DIR=artifacts/free_extractive_chat_mode
CHAT_URL=http://127.0.0.1:8010/chat
HEALTH_URL=http://127.0.0.1:8010/health
APPROVED_QA_RUNTIME="$ARTIFACT_DIR/approved_qa_118_runtime.jsonl"

mkdir -p "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR/chat_exact_qa"
mkdir -p "$ARTIFACT_DIR/unknown_abstention"
mkdir -p "$ARTIFACT_DIR/normal_retrieval_candidate"

python - <<'PY'
from pathlib import Path

out = Path("artifacts/free_extractive_chat_mode/approved_qa_118_runtime.jsonl")
sources = [
    Path("data/approved_qa/default.jsonl"),
    Path("artifacts/fixed_qa_eval/ingest/040219_approved_qa_ingest.jsonl"),
]
rows = []
for source in sources:
    rows.extend(line for line in source.read_text(encoding="utf-8").splitlines() if line.strip())
out.write_text("\n".join(rows) + "\n", encoding="utf-8")
if len(rows) != 118:
    raise SystemExit(f"expected 118 approved QA rows, got {len(rows)}")
PY

export APPROVED_QA_ENABLED=true
export APPROVED_QA_PATH="$APPROVED_QA_RUNTIME"

stop_existing_8010_uvicorn() {
  local pids
  pids="$(lsof -tiTCP:8010 -sTCP:LISTEN 2>/dev/null || true)"
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

stop_existing_8010_uvicorn

uvicorn webapi.main:app --host 127.0.0.1 --port 8010 > "$ARTIFACT_DIR/uvicorn.log" 2>&1 &
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

url = "http://127.0.0.1:8010/health"
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

python - <<'PY'
import json
import urllib.error
import urllib.request
from pathlib import Path

url = "http://127.0.0.1:8010/chat"
payload = {
    "question": "大阪府の電子入札システムでJava Plug-in警告が出る原因は何ですか。",
    "top_k": 5,
}
body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
status = 0
raw = b""
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        status = resp.status
        raw = resp.read()
except urllib.error.HTTPError as exc:
    status = exc.code
    raw = exc.read()
data = json.loads(raw.decode("utf-8")) if raw else {}
if isinstance(data, dict):
    data["http_status"] = status
Path("artifacts/free_extractive_chat_mode/unknown_006_manual_chat_response.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

python tools/evaluate_chat_approved_qa.py \
  --cases artifacts/fixed_qa_eval/fixed_qa_cases.jsonl \
  --chat-url "$CHAT_URL" \
  --output-dir "$ARTIFACT_DIR/chat_exact_qa" \
  --timeout 60

python tools/evaluate_unknown_abstention.py \
  --cases artifacts/unknown_abstention_eval/unknown_questions.jsonl \
  --chat-url "$CHAT_URL" \
  --output-dir "$ARTIFACT_DIR/unknown_abstention" \
  --timeout 60

NORMAL_RETRIEVAL_COLLECTION="$CHROMA_COLLECTION" python tools/evaluate_normal_retrieval_vector_vs_hybrid.py \
  --cases artifacts/normal_retrieval_eval/normal_retrieval_cases.jsonl \
  --collection "$CHROMA_COLLECTION" \
  --output-dir "$ARTIFACT_DIR/normal_retrieval_candidate" \
  --top-k 5

python - <<'PY'
import json
from pathlib import Path

base = Path("artifacts/free_extractive_chat_mode")
health = json.loads((base / "health.json").read_text(encoding="utf-8"))
manual = json.loads((base / "unknown_006_manual_chat_response.json").read_text(encoding="utf-8"))
exact = json.loads((base / "chat_exact_qa/chat_exact_qa_eval_report.md").read_text(encoding="utf-8").encode("utf-8").decode("utf-8") or "{}") if False else None

def read_exact_summary() -> dict:
    rows = [
        json.loads(line)
        for line in (base / "chat_exact_qa/chat_exact_qa_eval_results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    total = len(rows)
    return {
        "total_cases": total,
        "errors": sum(1 for row in rows if row.get("error")),
        "answer_match_rate": sum(1 for row in rows if row.get("answer_match")) / total if total else 0,
        "approved_exact_rate": sum(1 for row in rows if row.get("approved_exact")) / total if total else 0,
        "llm_fallback_count": sum(1 for row in rows if not row.get("approved_exact")),
    }

unknown_summary = json.loads((base / "unknown_abstention/unknown_abstention_report_summary.json").read_text(encoding="utf-8")) if False else None
unknown_rows = [
    json.loads(line)
    for line in (base / "unknown_abstention/unknown_abstention_results.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
unknown = {
    "total_cases": len(unknown_rows),
    "errors": sum(1 for row in unknown_rows if row.get("classification") == "error"),
    "abstained_count": sum(1 for row in unknown_rows if row.get("abstained") is True),
    "unsupported_answer_count": sum(1 for row in unknown_rows if row.get("unsupported_answer") is True),
    "approved_exact_false_positive_count": sum(
        1 for row in unknown_rows if row.get("approved_exact_false_positive") is True
    ),
}
normal = json.loads((base / "normal_retrieval_candidate/vector_vs_hybrid_comparison.json").read_text(encoding="utf-8"))
exact_summary = read_exact_summary()

checks = [
    ("health.status", health.get("status") == "ok"),
    ("health.keyword_index_loaded", health.get("keyword_index_loaded") is True),
    ("health.keyword_index_records", health.get("keyword_index_records") == 116),
    ("health.chroma_collection", health.get("chroma_collection") == "chatbot_chunks_v1_aligned_candidate"),
    ("health.embed_provider", health.get("embed_provider") == "local"),
    ("health.chat_generation_mode", health.get("chat_generation_mode") == "extractive"),
    ("manual.http_status", manual.get("http_status") == 200),
    ("manual.answer_text_nonempty", bool(str(manual.get("answer_text") or manual.get("answer") or "").strip())),
    ("manual.used_fallback", manual.get("used_fallback") is True),
    ("exact.total_cases", exact_summary["total_cases"] == 118),
    ("exact.errors", exact_summary["errors"] == 0),
    ("exact.answer_match_rate", exact_summary["answer_match_rate"] == 1.0),
    ("exact.approved_exact_rate", exact_summary["approved_exact_rate"] == 1.0),
    ("exact.llm_fallback_count", exact_summary["llm_fallback_count"] == 0),
    ("unknown.total_cases", unknown["total_cases"] == 32),
    ("unknown.errors", unknown["errors"] == 0),
    ("unknown.abstained_count", unknown["abstained_count"] == 32),
    ("unknown.unsupported_answer_count", unknown["unsupported_answer_count"] == 0),
    (
        "unknown.approved_exact_false_positive_count",
        unknown["approved_exact_false_positive_count"] == 0,
    ),
    ("normal.hybrid_hit@5", normal.get("hybrid_hit@5") == 1.0),
    ("normal.still_failed", normal.get("still_failed") == []),
]
failed = [name for name, ok in checks if not ok]
summary = {
    "health": health,
    "manual_unknown_006": {
        "http_status": manual.get("http_status"),
        "answer_text_nonempty": bool(str(manual.get("answer_text") or manual.get("answer") or "").strip()),
        "used_fallback": manual.get("used_fallback"),
        "answer_mode": manual.get("answer_mode"),
        "guard_reason": manual.get("guard_reason"),
    },
    "exact_qa": exact_summary,
    "unknown_abstention": unknown,
    "normal_retrieval": {
        "hybrid_hit@5": normal.get("hybrid_hit@5"),
        "still_failed": normal.get("still_failed"),
    },
    "failed_checks": failed,
}
(base / "validation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
if failed:
    raise SystemExit(f"failed checks: {', '.join(failed)}")
PY

echo
echo "free/local-only extractive mode check passed"
echo "artifacts: $ARTIFACT_DIR"
