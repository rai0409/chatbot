#!/usr/bin/env bash
# Purpose : run every verification of this repository on a fresh machine, step by step, and say what was skipped.
# Usage   : bash tools/verify_all.sh [--quick] [--with-eval]
#             --quick      only the unit/integration tests (no browser, no Postgres, no vitest)
#             --with-eval  also run the real-model retrieval evaluation (downloads ruri models, needs torch; slow)
# Input   : none (everything is created inside the repository: .venv/, data/external/, webui/node_modules/;
#           all git-ignored). Needs python3.12, and node/npm for the web UI tests. No sudo is used.
# Output  : a summary table at the end; per-step logs in data/external/verify_logs/ (git-ignored).
# Cleanup : the folders above can be moved to 即削除/ when no longer needed. Nothing outside the repository is written
#           except the Playwright browser cache (~/.cache/ms-playwright). The Hugging Face cache is kept inside the
#           repository (HF_HOME is set by this script when --with-eval is used).
#           This script never deletes anything: an old test database folder is moved aside, not removed.
set -u
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LOGS="$ROOT/data/external/verify_logs"; mkdir -p "$LOGS"
QUICK=0; EVAL=0
for a in "$@"; do case "$a" in --quick) QUICK=1;; --with-eval) EVAL=1;; esac; done
declare -a RESULTS=()
note() { RESULTS+=("$1|$2|$3"); }
run() {  # run <name> <command...>
  local name="$1"; shift
  echo "== $name"
  if "$@" >"$LOGS/$name.log" 2>&1; then note "$name" PASS ""; else note "$name" FAIL "see $LOGS/$name.log"; fi
}

# 1. environment
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv || { echo "cannot create .venv"; exit 2; }; fi
PY=.venv/bin/python
run install-core "$PY" -m pip install -q -r requirements.txt -r requirements-pdf.txt -r requirements-ja.txt \
  -r requirements-state.txt -r requirements-qdrant.txt -r requirements-anthropic.txt pytest -c constraints/linux-py312.txt
export REQUIRE_OPTIONAL_DEPS=1   # a missing optional package is a failure here, never a silent skip

# 2. tests that need nothing else
run unit-and-integration "$PY" -m pytest -q --ignore=tests/e2e --ignore=tests/test_llm_quota_store_pg.py -p no:cacheprovider
run docextract-tests "$PY" -m pytest -q libs/docextract/tests -p no:cacheprovider
run retrieval-regression-gate "$PY" scripts/eval_regression_gate.py --margin 0.02
run static-pyflakes "$PY" -m pyflakes rag_core webapi scripts eval tools tests libs config.py rag_grounded.py

if [ "$QUICK" = 1 ]; then
  note e2e SKIP "--quick"; note postgres SKIP "--quick"; note webui SKIP "--quick"
else
  # 3. browser tests (Playwright). Linux needs system libraries: if the browser cannot start, run once
  #    `sudo .venv/bin/python -m playwright install-deps chromium-headless-shell` and repeat.
  run install-playwright bash -c "$PY -m pip install -q playwright && $PY -m playwright install chromium-headless-shell"
  KURADEN_E2E_REQUIRED=1 run e2e "$PY" -m pytest -q tests/e2e -p no:cacheprovider

  # 4. PostgreSQL quota store tests: an isolated server from the bundled `pgserver` binaries (no Docker, no sudo)
  run install-pgserver "$PY" -m pip install -q pgserver
  BIN="$($PY -c 'import pgserver,pathlib;print(pathlib.Path(pgserver.__file__).parent/"pginstall"/"bin")' 2>/dev/null)"
  PGD="$ROOT/data/external/pg"; SOCK="$ROOT/data/external/pgsock"
  if [ -n "$BIN" ] && [ -x "$BIN/initdb" ]; then
    mkdir -p "$SOCK"
    if [ -e "$PGD" ]; then mv "$PGD" "$PGD.old.$(date +%s)"; fi   # never delete: move an old cluster aside
    "$BIN/initdb" -D "$PGD" -U postgres --auth=trust -E UTF8 >"$LOGS/pg-init.log" 2>&1
    "$BIN/pg_ctl" -D "$PGD" -o "-p 5432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCK" -l "$LOGS/pg-server.log" -w start >/dev/null 2>&1
    "$BIN/psql" -h 127.0.0.1 -p 5432 -U postgres -c "CREATE ROLE quota_test LOGIN PASSWORD 'quota_test' CREATEDB;" -c "CREATE DATABASE quota_test OWNER quota_test;" >/dev/null 2>&1
    CHATBOT_QUOTA_TEST_ISOLATED=1 LLM_QUOTA_TEST_DSN="host=127.0.0.1 port=5432 dbname=quota_test user=quota_test password=quota_test" \
      run postgres "$PY" -m pytest -q tests/test_llm_quota_store_pg.py -p no:cacheprovider
    "$BIN/pg_ctl" -D "$PGD" -m fast stop >/dev/null 2>&1
  else
    note postgres SKIP "pgserver binaries not available on this platform (use Docker: postgres:16, see .github/workflows/ci.yml)"
  fi

  # 5. web UI
  if command -v npm >/dev/null 2>&1; then
    run webui-install bash -c "cd webui && npm ci --no-audit --no-fund"
    run webui-typecheck bash -c "cd webui && npx tsc --noEmit"
    run webui-vitest bash -c "cd webui && npx vitest run"
  else
    note webui SKIP "npm not found"
  fi
fi

# 6. real-model retrieval evaluation (optional; downloads the JaGovFaqs file, see tools/eval_real_vector_jagov.py)
if [ "$EVAL" = 1 ]; then
  export HF_HOME="$ROOT/data/external/hf_cache"; mkdir -p "$ROOT/data/external/jagovfaqs" "$ROOT/data/external/eval_out"
  [ -f data/external/jagovfaqs/data.jsonl ] || curl -sL -o data/external/jagovfaqs/data.jsonl \
      https://huggingface.co/datasets/matsuxr/JaGovFaqs-22k/resolve/main/data.jsonl   # CC-BY-4.0
  run install-torch bash -c "$PY -m pip install -q torch --index-url https://download.pytorch.org/whl/cpu && $PY -m pip install -q 'sentence-transformers==5.7.0' -c constraints/linux-py312.txt"
  run eval-real-vector "$PY" tools/eval_real_vector_jagov.py
else
  note eval-real-vector SKIP "add --with-eval"
fi

echo; echo "==== summary ===="
fail=0
for r in "${RESULTS[@]}"; do IFS='|' read -r n s d <<<"$r"; printf '%-28s %-5s %s\n' "$n" "$s" "$d"; [ "$s" = FAIL ] && fail=1; done
exit $fail
