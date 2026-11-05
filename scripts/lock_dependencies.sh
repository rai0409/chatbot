#!/usr/bin/env bash
# Hash-locked dependency files (6-13; stage 13: one lock per installed set).
# Run on a connected machine and commit the *.lock.txt files. CI and the
# Docker image install with --require-hashes when requirements.lock.txt
# exists, so a changed or substituted package fails the build instead of
# being installed.
#
#   requirements.lock.txt            requirements.txt + -ja + -pdf + -state + -qdrant
#                                    (the Docker image and CI set), versions from
#                                    constraints/linux-py312.txt
#   requirements-embedding.lock.txt  the same + requirements-embedding.txt and
#                                    CPU-only torch, versions from
#                                    constraints/linux-py312-embedding.txt
#
#   bash scripts/lock_dependencies.sh
# Requires: pip install uv. Update constraints/ first (clean venv + full
# pytest), then re-run this script: the locks only add hashes to those versions.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON_VERSION:-3.12}"
PLATFORM="${LOCK_PLATFORM:-x86_64-manylinux_2_28}"
BASE=(requirements.txt requirements-ja.txt requirements-pdf.txt requirements-state.txt requirements-qdrant.txt)
COMMON=(--python-version "$PY" --python-platform "$PLATFORM" --generate-hashes --no-annotate --no-header)

uv pip compile "${BASE[@]}" "${COMMON[@]}" \
  --constraint constraints/linux-py312.txt -o requirements.lock.txt

# torch==X+cpu exists only on the PyTorch CPU index; the requirements are
# passed on stdin so the torch pin needs no extra file.
{ cat "${BASE[@]}" requirements-embedding.txt; grep -E '^torch==' constraints/linux-py312-embedding.txt; } |
uv pip compile - "${COMMON[@]}" \
  --constraint constraints/linux-py312-embedding.txt \
  --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
  -o requirements-embedding.lock.txt
echo "lock files written; review the diff and commit them"
