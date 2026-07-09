#!/usr/bin/env bash
# Limited beta preflight (Prompt026).
#
# Safe-by-default, repo-local verification that the limited-beta launch pack is
# present and the supporting checks are green. Run this before working through
# docs/reports/limited_beta_launch_checklist.md.
#
# Hard rules:
# - never reads or copies the repo .env, and never prints secrets
# - never touches the production/default vectorstore (runs targeted tests and
#   the synthetic-data evals only)
# - Docker is NOT required by default; pass --with-docker-smoke to also run
#   scripts/deploy_smoke.sh (which itself uses only synthetic data)
# - exits non-zero on the first failure
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

WITH_DOCKER_SMOKE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-docker-smoke) WITH_DOCKER_SMOKE=1; shift ;;
    -h|--help)
      echo "usage: scripts/limited_beta_preflight.sh [--with-docker-smoke]"
      exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python"
fi

fail() { echo "PREFLIGHT FAIL: $*" >&2; exit 1; }

echo "== Limited beta preflight =="
echo "repo: $ROOT_DIR"
echo "python: $PYTHON"

# --- 1. Required repo-local files ------------------------------------------
echo
echo "== Required files =="
REQUIRED_FILES=(
  "docs/security_operations.md"
  "docs/operations.md"
  "docs/production_readiness_checklist.md"
  "scripts/deploy_smoke.sh"
  "scripts/backup.sh"
  "scripts/restore.sh"
  "scripts/onboard_documents_dry_run.py"
  "scripts/import_manifest.py"
  "eval/production_readiness_report.py"
  "webapi/rate_limit.py"
  "webapi/metrics_registry.py"
  "docs/reports/beta_go_no_go_assessment.md"
  "docs/reports/limited_beta_launch_checklist.md"
  "docs/reports/limited_beta_rollback_runbook.md"
  "docs/reports/pilot_tenant_onboarding_runbook.md"
)
for f in "${REQUIRED_FILES[@]}"; do
  [[ -f "$f" ]] || fail "missing required file: $f"
  echo "  ok: $f"
done

# --- 2. Required git tags ---------------------------------------------------
echo
echo "== Required tags =="
REQUIRED_TAGS=(
  "prompt023-deploy-ops"
  "prompt024-security-ops"
  "prompt025-observability-beta-gate"
)
for t in "${REQUIRED_TAGS[@]}"; do
  git rev-parse -q --verify "refs/tags/${t}" >/dev/null 2>&1 || fail "missing required tag: $t"
  echo "  ok: $t"
done

# --- 3. Targeted tests (safe, no network/model/docker) ----------------------
echo
echo "== Targeted tests =="
"$PYTHON" -m pytest \
  tests/test_embedding_fingerprint.py \
  tests/test_guard_distance_calibration.py \
  tests/test_rate_limit.py \
  tests/test_metrics_observability.py \
  tests/test_observability_export.py \
  tests/test_production_readiness_report.py \
  -q || fail "targeted tests failed"

# --- 4. Product readiness smoke --------------------------------------------
echo
echo "== Product readiness smoke =="
bash scripts/product_readiness_smoke.sh >/dev/null || fail "product readiness smoke failed"
echo "  ok: product readiness smoke"

# --- 5. Synthetic-data evals -----------------------------------------------
echo
echo "== Evals (synthetic data only) =="
PYTHONPATH=. "$PYTHON" -m eval.runner \
  --cases eval/cases/smoke_cases.jsonl \
  --chunks-jsonl eval/cases/smoke_chunks.jsonl \
  --output runs/eval/limited_beta_preflight_smoke.json >/dev/null \
  || fail "smoke eval failed"
echo "  ok: smoke eval"
PYTHONPATH=. "$PYTHON" -m eval.runner \
  --cases eval/cases/qa_pair_cases.jsonl \
  --chunks-jsonl eval/cases/qa_pair_chunks.jsonl \
  --output runs/eval/limited_beta_preflight_qa_pair.json >/dev/null \
  || fail "qa_pair eval failed"
echo "  ok: qa_pair eval"

# --- 6. Generated readiness artifacts --------------------------------------
echo
echo "== Readiness artifacts =="
for a in \
  "artifacts/readiness/production_readiness_report.json" \
  "artifacts/readiness/production_readiness_report.md"; do
  [[ -f "$a" ]] || fail "missing readiness artifact: $a (run: $PYTHON eval/production_readiness_report.py)"
  echo "  ok: $a"
done

# --- 7. Optional docker deploy smoke ---------------------------------------
if [[ "$WITH_DOCKER_SMOKE" -eq 1 ]]; then
  echo
  echo "== Docker deploy smoke (--with-docker-smoke) =="
  command -v docker >/dev/null 2>&1 || fail "--with-docker-smoke requested but docker is not available"
  bash scripts/deploy_smoke.sh || fail "docker deploy smoke failed"
  echo "  ok: docker deploy smoke"
else
  echo
  echo "== Docker deploy smoke: SKIPPED (pass --with-docker-smoke to run) =="
fi

echo
echo "PREFLIGHT OK"
