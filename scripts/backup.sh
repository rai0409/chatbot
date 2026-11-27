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
#   models_cache/  config/embedding_assets/   local model files and embedding assets, when present
#   SOURCES_DIR / ACTIVE_GENERATION_FILE      when set to a path under --source-dir (outside it: refused unless
#                                             BACKUP_SKIP_OUTSIDE=1; back those up separately)
#
# 2026-11 ops hardening:
#   - one backup at a time (<output>/.backup.lock) and the maintenance lock held exclusively while copying
#     (MAINTENANCE_LOCK_FILE, default <source>/runs/locks/maintenance.lock): ingestion jobs, promotion and
#     deletion hold it shared, so no writer changes Chroma's files mid-copy (waits BACKUP_LOCK_WAIT_SECONDS,
#     default 600, then fails)
#   - the archive is verified after it is written (every member re-hashed against the manifest, streamed)
#   - the work directory is inside the output directory (never /tmp)
#   - <output>/backup_status.json records the last result (monitoring: backup failure / age)
#   - --prune lists archives beyond BACKUP_KEEP (default 14, newest kept); they are deleted only with
#     BACKUP_PRUNE_CONFIRM=1 (an operator decision)
#
# Usage: scripts/backup.sh [--source-dir DIR] [--output-dir DIR] [--extra PATH ...]
#        scripts/backup.sh --prune [--output-dir DIR]
set -euo pipefail

SOURCE_DIR="."
OUTPUT_DIR="backups"
EXTRA=()
PRUNE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir) SOURCE_DIR="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --extra) EXTRA+=("$2"); shift 2 ;;
    --prune) PRUNE=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ "$PRUNE" == "1" ]]; then
  KEEP="${BACKUP_KEEP:-14}"
  [[ "$KEEP" =~ ^[0-9]+$ ]] && (( KEEP >= 1 )) || { echo "ERROR: BACKUP_KEEP must be a positive integer" >&2; exit 2; }
  mapfile -t ALL < <(ls -1 "$OUTPUT_DIR"/chatbot_backup_*.tar.gz 2>/dev/null | sort -r || true)
  if (( ${#ALL[@]} <= KEEP )); then
    echo "prune: ${#ALL[@]} archive(s), keeping ${KEEP}: nothing to prune"
    exit 0
  fi
  echo "prune: ${#ALL[@]} archive(s), keeping the newest ${KEEP}; candidates:"
  for old in "${ALL[@]:$KEEP}"; do echo "  $old"; done
  if [[ "${BACKUP_PRUNE_CONFIRM:-}" == "1" ]]; then
    for old in "${ALL[@]:$KEEP}"; do rm -f -- "$old"; echo "deleted: $old"; done
  else
    echo "dry run: set BACKUP_PRUNE_CONFIRM=1 to delete the candidates above"
  fi
  exit 0
fi

SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_NAME="chatbot_backup_${TS}.tar.gz"
MANIFEST_NAME="backup_manifest.sha256"
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
STATUS_FILE="$OUTPUT_DIR/backup_status.json"

write_status() {
  # $1 = ok|error ; no secrets, no paths beyond the archive name
  local tmp="$STATUS_FILE.tmp.$$"
  printf '{"status": "%s", "finished_at": %s, "archive": "%s"}\n' "$1" "$(date -u +%s)" "${2:-}" > "$tmp"
  mv -f "$tmp" "$STATUS_FILE"
}

# One backup at a time.
exec 8>>"$OUTPUT_DIR/.backup.lock"
if command -v flock >/dev/null 2>&1; then
  if ! flock -n 8; then
    echo "ERROR: another backup is running (lock $OUTPUT_DIR/.backup.lock)" >&2
    exit 75
  fi
fi

DONE=0
on_exit() {
  local code=$?
  if [[ -n "${STAGING:-}" && -d "$STAGING" ]]; then rm -rf -- "$STAGING"; fi
  if [[ "$DONE" != "1" ]]; then write_status error ""; fi
  exit "$code"
}
trap on_exit EXIT

# Writers (ingestion jobs, promotion, deletion) hold the maintenance lock shared; the backup holds it exclusively.
MAINT_LOCK="${MAINTENANCE_LOCK_FILE:-runs/locks/maintenance.lock}"
[[ "$MAINT_LOCK" == /* ]] || MAINT_LOCK="$SOURCE_DIR/$MAINT_LOCK"
mkdir -p "$(dirname "$MAINT_LOCK")"
exec 9>>"$MAINT_LOCK"
if command -v flock >/dev/null 2>&1; then
  if ! flock -w "${BACKUP_LOCK_WAIT_SECONDS:-600}" 9; then
    echo "ERROR: a writer holds the maintenance lock ($MAINT_LOCK); retry later" >&2
    exit 1
  fi
else
  echo "WARNING: flock(1) is not installed: run the backup while no ingestion job, promotion or deletion writes" >&2
fi

# Paths configured outside the default layout.
relative_or_fail() {
  local raw="$1" abs
  [[ -n "$raw" ]] || return 0
  if [[ "$raw" == /* ]]; then abs="$raw"; else abs="$SOURCE_DIR/$raw"; fi
  if [[ "$abs" == "$SOURCE_DIR/"* ]]; then
    echo "${abs#"$SOURCE_DIR/"}"
  elif [[ "${BACKUP_SKIP_OUTSIDE:-}" == "1" ]]; then
    echo "WARNING: $raw is outside $SOURCE_DIR and is NOT in this backup (BACKUP_SKIP_OUTSIDE=1)" >&2
  else
    echo "ERROR: $raw is outside $SOURCE_DIR; back it up separately and set BACKUP_SKIP_OUTSIDE=1" >&2
    return 1
  fi
}
CONFIGURED=()
for raw in "${SOURCES_DIR:-}" "${ACTIVE_GENERATION_FILE:-}"; do
  rel="$(relative_or_fail "$raw")" || exit 1
  [[ -n "$rel" ]] && CONFIGURED+=("$rel")
done

TARGETS=()
for candidate in vectorstore index data/approved_qa runs/audit runs/ingestion_registry.sqlite3 runs/state \
                 runs/conversations configs config/rag_profiles config/embedding_assets models_cache \
                 runs/settings runs/records runs/usage runs/llm_batch runs/tenant_eval \
                 data/golden_qa data/profile_versions data/sources data/contracts data/extraction \
                 "${CONFIGURED[@]}" "${EXTRA[@]}"; do
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
# A target listed twice (index/ and a configured file inside it) is archived once.
mapfile -t TARGETS < <(printf '%s\n' "${TARGETS[@]}" | awk '!seen[$0]++')

STAGING="$(mktemp -d "$OUTPUT_DIR/.staging.XXXXXX")"
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

# The archived roots (restore.sh --in-place moves exactly these aside and swaps the restored ones in).
printf '%s\n' "${TARGETS[@]}" > "$STAGING/backup_targets.txt"

tar -czf "$OUTPUT_DIR/$ARCHIVE_NAME" \
  -C "$STAGING" "$MANIFEST_NAME" backup_targets.txt \
  -C "$SOURCE_DIR" --null -T "$STAGING/plain.list" \
  -C "$SNAP" --null -T "$STAGING/sqlite.list"

# Verify what was written: every member re-hashed (streamed, nothing extracted) against the manifest.
if ! python3 - "$OUTPUT_DIR/$ARCHIVE_NAME" "$MANIFEST_NAME" <<'PY'
import hashlib
import sys
import tarfile

archive, manifest_name = sys.argv[1], sys.argv[2]
expected, actual = {}, {}
with tarfile.open(archive, "r:gz") as tar:
    for member in tar:
        if not member.isfile():
            continue
        handle = tar.extractfile(member)
        if member.name == manifest_name:
            for line in handle.read().decode("utf-8").splitlines():
                digest, _, name = line.partition("  ")
                expected[name] = digest
            continue
        digest = hashlib.sha256()
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
        actual[member.name] = digest.hexdigest()
missing = sorted(set(expected) - set(actual))
wrong = sorted(n for n in expected if n in actual and actual[n] != expected[n])
if not expected or missing or wrong:
    print(f"ERROR: archive verification failed (missing={len(missing)} mismatched={len(wrong)})", file=sys.stderr)
    sys.exit(1)
print(f"archive verified: {len(expected)} files")
PY
then
  # Never leave a bad archive under a name that looks good.
  mv -f -- "$OUTPUT_DIR/$ARCHIVE_NAME" "$OUTPUT_DIR/$ARCHIVE_NAME.failed"
  echo "ERROR: the archive did not verify; kept as $ARCHIVE_NAME.failed" >&2
  exit 1
fi

FILE_COUNT="$(wc -l < "$STAGING/$MANIFEST_NAME")"
DONE=1
write_status ok "$ARCHIVE_NAME"
echo "backup written: $OUTPUT_DIR/$ARCHIVE_NAME"
echo "  source:  $SOURCE_DIR"
echo "  targets: ${TARGETS[*]}"
echo "  files:   $FILE_COUNT (sha256 manifest included: $MANIFEST_NAME)"
