#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python"
fi

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
echo "ADMIN_AUTH_ENABLED=true ADMIN_AUTH_TOKEN=local-admin-token $PYTHON -m uvicorn webapi.main:app --host 127.0.0.1 --port 8000"
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
