#!/usr/bin/env bash
# Persistence isolation check (Prompt030).
#
# Runs ONLY the synthetic durable-persistence verification tests, which prove
# tenant isolation and stored data survive a Chroma client reload and a
# hash-verified backup/restore. Those tests build their store in a pytest
# tmp_path under an explicit NON-PRODUCTION collection (pilot_persist_check_v1)
# and assert they never use the production/default collection.
#
# Hard rules:
# - never reads or copies the repo .env, and never prints secrets
# - never touches the production/default vectorstore or the repo VECTORSTORE_DIR
#   (the tests use temp dirs only and assert this)
# - exits non-zero on failure
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python"
fi

# Defensive: do not inherit any ambient pointer at the production collection or
# the repo vectorstore for this synthetic-only check.
unset CHROMA_COLLECTION VECTORSTORE_COLLECTION_NAME VECTORSTORE_DIR 2>/dev/null || true

echo "== Persistence isolation check (synthetic, non-production) =="
"$PYTHON" -m pytest tests/test_durable_multitenant_persistence.py -q
echo "PERSISTENCE ISOLATION CHECK OK"
