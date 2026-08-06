#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage: bash scripts/product_readiness_smoke.sh [--python <executable>]

Run product-readiness checks with an explicit Python interpreter. Selection order:
  1. --python <executable>
  2. PRODUCT_READINESS_PYTHON
  3. python3 on PATH
  4. python on PATH
EOF
}

die() {
  echo "product readiness smoke: $*" >&2
  exit 2
}

EXPLICIT_PYTHON=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)
      [[ $# -ge 2 ]] || die "--python requires an executable"
      EXPLICIT_PYTHON="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --*)
      die "unknown option"
      ;;
    *)
      die "positional arguments are not supported"
      ;;
  esac
done

if [[ -n "$EXPLICIT_PYTHON" ]]; then
  PYTHON="$EXPLICIT_PYTHON"
elif [[ -n "${PRODUCT_READINESS_PYTHON:-}" ]]; then
  PYTHON="$PRODUCT_READINESS_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON="$(command -v python)"
else
  die "no Python interpreter found"
fi

[[ -x "$PYTHON" ]] || die "selected Python interpreter is not executable"

echo "== Product readiness smoke: pytest =="
"$PYTHON" -m pytest \
  tests/test_admin_auth.py \
  tests/test_review_queue_page.py \
  tests/test_review_actions.py \
  tests/test_product_profile.py \
  tests/test_product_route_policy.py \
  tests/test_production_readiness_report.py \
  tests/test_product_preview_profiles.py \
  tests/test_product_preview_chat.py \
  tests/test_product_preview_feedback_rerank.py \
  tests/test_product_preview_feature_rerank.py -q

echo
echo "== Product readiness smoke: py_compile =="
"$PYTHON" -m py_compile \
  webapi/main.py \
  webapi/admin_auth.py \
  eval/production_readiness_report.py \
  rag_core/product_profile.py \
  rag_core/product_route_policy.py

echo
echo "== Manual curl examples =="
echo "These commands require a server that you start separately. This script does not start uvicorn."
echo
echo "# Admin auth disabled"
echo "curl -i -s http://127.0.0.1:8000/admin/review/items"
echo
echo "# Admin auth enabled without token should reject"
echo "ADMIN_AUTH_ENABLED=true ADMIN_AUTH_TOKEN=local-admin-token \${PRODUCT_READINESS_PYTHON:-python3} -m uvicorn webapi.main:app --host 127.0.0.1 --port 8000"
echo "curl -i -s http://127.0.0.1:8000/admin/review/items"
echo
echo "# Admin auth enabled with token should allow"
echo "curl -i -s http://127.0.0.1:8000/admin/review/items -H 'Authorization: Bearer local-admin-token'"
echo "curl -i -s http://127.0.0.1:8000/admin/review/items -H 'X-Admin-Token: local-admin-token'"
echo
echo "# Product preview with production_safe"
echo "curl -i -s -X POST http://127.0.0.1:8000/chat/product-preview -H 'Content-Type: application/json' -d '{\"query\":\"15問に自由回答は含まれますか？\",\"product_profile\":\"production_safe\",\"apply_feedback_preview\":true,\"apply_feature_rerank\":true}'"
echo
echo "# Product preview with pilot_high_accuracy"
echo "curl -i -s -X POST http://127.0.0.1:8000/chat/product-preview -H 'Content-Type: application/json' -d '{\"query\":\"15問に自由回答は含まれますか？\",\"product_profile\":\"pilot_high_accuracy\",\"apply_feedback_preview\":true,\"apply_feature_rerank\":true}'"
