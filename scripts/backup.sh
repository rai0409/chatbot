#!/usr/bin/env bash
# Backup the stateful runtime data (Prompt023; stage 14: everything needed to
# serve again) — whichever exist under --source-dir — into a timestamped
# tar.gz with a sha256 manifest inside the archive. Read-only on the source.
#
#   vectorstore/                     Chroma (VECTOR_BACKEND=chroma)
#   index/                           keyword corpora, incl. index/collections/ (not .keyword_cache/)
#   data/approved_qa/                approved answers
#   runs/audit/                      audit logs
#   runs/ingestion_registry.sqlite3  staging collections and their owner tenants, jobs
#   runs/state/  runs/conversations/ session revocations, conversation history (local backends)
#   configs/                         product and RAG profiles
#   runs/settings/                   the console's runtime switches, limits and model assignments (rag_core/runtime_settings.py)
#   runs/records/                    records of questions and answers, the judgements, the pseudonym salt (rag_core/records.py):
#                                    they hold what people asked, so protect the archive like the data itself
#   runs/usage/  runs/llm_batch/     LLM usage files, batch jobs
#   runs/tenant_eval/                proposals and the evaluation ledger
#   data/golden_qa/                  the golden questions kept in the console
#   data/profile_versions/           stored versions of the RAG profiles and which one is active
#   data/sources/  data/contracts/  data/extraction/   registered sources, the contract ledger, OCR lines and their corrections
#   --extra PATH (repeatable)        e.g. the APPROVED_QA_PATH file or the upload directory
#
# SQLite files are copied with SQLite's online backup (a consistent snapshot
# while the API runs). Chroma's other files are copied as they are: run the
# backup while no ingestion job writes. Qdrant, Postgres and Redis keep their
# data outside these folders: snapshot / pg_dump them as in
# docs/operations/backup_scope.md.
#
# Usage: scripts/backup.sh [--source-dir DIR] [--output-dir DIR] [--extra PATH ...]
set -euo pipefail

SOURCE_DIR="."
OUTPUT_DIR="backups"
EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir) SOURCE_DIR="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --extra) EXTRA+=("$2"); shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_NAME="chatbot_backup_${TS}.tar.gz"
MANIFEST_NAME="backup_manifest.sha256"

TARGETS=()
for candidate in vectorstore index data/approved_qa runs/audit runs/ingestion_registry.sqlite3 runs/state \
                 runs/conversations configs \
                 runs/settings runs/records runs/usage runs/llm_batch runs/tenant_eval \
                 data/golden_qa data/profile_versions data/sources data/contracts data/extraction "${EXTRA[@]}"; do
  if [[ -e "$SOURCE_DIR/$candidate" ]]; then
    TARGETS+=("$candidate")
  elif [[ " ${EXTRA[*]} " == *" $candidate "* ]]; then
    echo "ERROR: --extra path not found under $SOURCE_DIR: $candidate" >&2
    exit 1
  fi
done
if [[ "${#TARGETS[@]}" == "0" ]]; then
  echo "ERROR: nothing to back up under $SOURCE_DIR (expected vectorstore/, index/, data/approved_qa/, runs/audit/)" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
SNAP="$STAGING/snapshot"
mkdir -p "$SNAP"

# Files to archive: everything under the targets except the keyword index
# cache (rebuilt automatically; holds its signing key) and SQLite side files.
: > "$STAGING/plain.list"
: > "$STAGING/sqlite.list"
(
  cd "$SOURCE_DIR"
  find "${TARGETS[@]}" -type f -not -path '*/.keyword_cache/*' -not -name '*-wal' -not -name '*-shm' \
    -not -name '*.sqlite3' -not -name '*.sqlite' -print0 | sort -z > "$STAGING/plain.list"
  find "${TARGETS[@]}" -type f \( -name '*.sqlite3' -o -name '*.sqlite' \) -print0 | sort -z > "$STAGING/sqlite.list"
)
while IFS= read -r -d '' rel; do
  mkdir -p "$SNAP/$(dirname "$rel")"
  python3 - "$SOURCE_DIR/$rel" "$SNAP/$rel" <<'PY'
import shutil
import sqlite3
import sys

with open(sys.argv[1], "rb") as f:
    is_sqlite = f.read(16) == b"SQLite format 3\x00"
if not is_sqlite:  # a file that only has the extension: copy it as it is
    shutil.copy2(sys.argv[1], sys.argv[2])
    raise SystemExit(0)
src = sqlite3.connect(sys.argv[1], timeout=30)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close()
src.close()
PY
done < "$STAGING/sqlite.list"

# Manifest: sha256 of every file, relative paths, sha256sum -c compatible.
{
  (cd "$SOURCE_DIR" && xargs -0 -r sha256sum < "$STAGING/plain.list")
  (cd "$SNAP" && xargs -0 -r sha256sum < "$STAGING/sqlite.list")
} | sort -k2 > "$STAGING/$MANIFEST_NAME"

tar -czf "$OUTPUT_DIR/$ARCHIVE_NAME" \
  -C "$STAGING" "$MANIFEST_NAME" \
  -C "$SOURCE_DIR" --null -T "$STAGING/plain.list" \
  -C "$SNAP" --null -T "$STAGING/sqlite.list"

FILE_COUNT="$(wc -l < "$STAGING/$MANIFEST_NAME")"
echo "backup written: $OUTPUT_DIR/$ARCHIVE_NAME"
echo "  source:  $SOURCE_DIR"
echo "  targets: ${TARGETS[*]}"
echo "  files:   $FILE_COUNT (sha256 manifest included: $MANIFEST_NAME)"
