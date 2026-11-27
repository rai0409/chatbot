#!/usr/bin/env bash
# Restore a backup archive created by scripts/backup.sh (Prompt023; 2026-11 ops hardening).
#
# Default is SAFE: extracts into a staging directory and verifies every file
# against the embedded sha256 manifest.
#
# --in-place --source-dir DIR replaces the live data, in this order:
#   1. refuses while the API is running (health URL answers or a uvicorn process
#      of webapi.main is found) unless --force
#   2. extracts into DIR/.restore_tmp_<ts>/ (same file system, never /tmp) and
#      verifies every hash there; nothing live is touched until this passes
#   3. moves every archived root (vectorstore/, index/, runs/audit/, the
#      registry file and its -wal/-shm, ...) that exists now aside into
#      DIR/.restore_previous_<ts>/ (kept, never deleted: the operator removes
#      it after checking the restored service)
#   4. moves the restored roots into place, removes its work directory
#   5. post-restore steps when DIR is this installation: the index consistency
#      check of the served collection and the answer cache flush; otherwise the
#      commands are printed
#
#   RESTORE_HEALTH_URL       default http://127.0.0.1:8000/health
#   RESTORE_PROCESS_PATTERN  default "uvicorn.*webapi.main" (pgrep -f)
#
# Usage: scripts/restore.sh ARCHIVE [--target DIR] [--in-place --source-dir DIR [--force]]
set -euo pipefail

ARCHIVE="${1:-}"
[[ -n "$ARCHIVE" ]] || { echo "usage: restore.sh ARCHIVE [--target DIR] [--in-place --source-dir DIR [--force]]" >&2; exit 2; }
shift

TARGET=""
IN_PLACE=0
FORCE=0
SOURCE_DIR="."
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --in-place) IN_PLACE=1; shift ;;
    --force) FORCE=1; shift ;;
    --source-dir) SOURCE_DIR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -f "$ARCHIVE" ]] || { echo "ERROR: archive not found: $ARCHIVE" >&2; exit 1; }
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

verify_in() {
  local dir="$1"
  [[ -f "$dir/backup_manifest.sha256" ]] || { echo "ERROR: backup manifest missing in archive" >&2; return 1; }
  echo "verifying sha256 manifest..."
  (cd "$dir" && sha256sum -c backup_manifest.sha256 --quiet)
}

if [[ "$IN_PLACE" != "1" ]]; then
  [[ -n "$TARGET" ]] || TARGET="restore_staging_$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$TARGET"
  tar -xzf "$ARCHIVE" -C "$TARGET"
  verify_in "$TARGET"
  FILE_COUNT="$(wc -l < "$TARGET/backup_manifest.sha256")"
  echo "restore verified: $FILE_COUNT files OK"
  echo "  archive: $ARCHIVE"
  echo "  target:  $TARGET"
  echo "  staging restore: inspect the target, then move directories into place manually."
  exit 0
fi

LIVE="$(cd "$SOURCE_DIR" && pwd)"
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"

# 1. the API must be stopped
if [[ "$FORCE" != "1" ]]; then
  HEALTH_URL="${RESTORE_HEALTH_URL:-http://127.0.0.1:8000/health}"
  if command -v curl >/dev/null 2>&1 && curl -fsS -m 3 "$HEALTH_URL" -o /dev/null 2>/dev/null; then
    echo "ERROR: the API answers at $HEALTH_URL; stop it before an in-place restore (or --force)" >&2
    exit 1
  fi
  PATTERN="${RESTORE_PROCESS_PATTERN:-uvicorn.*webapi.main}"
  if command -v pgrep >/dev/null 2>&1 && pgrep -f "$PATTERN" >/dev/null 2>&1; then
    echo "ERROR: an API process is running (pgrep -f '$PATTERN'); stop it before an in-place restore (or --force)" >&2
    exit 1
  fi
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$LIVE/.restore_tmp_${TS}_$$"
ASIDE="$LIVE/.restore_previous_${TS}"
mkdir -p "$WORK"
cleanup() { if [[ -d "$WORK" ]]; then rm -rf -- "$WORK"; fi; }
trap cleanup EXIT

# 2. extract and verify away from the live data
tar -xzf "$ARCHIVE" -C "$WORK"
verify_in "$WORK"
FILE_COUNT="$(wc -l < "$WORK/backup_manifest.sha256")"

# The archived roots: backup_targets.txt (newer archives) or derived from the manifest.
if [[ -f "$WORK/backup_targets.txt" ]]; then
  mapfile -t ROOTS < "$WORK/backup_targets.txt"
else
  mapfile -t ROOTS < <(sed -E 's/^[0-9a-f]{64}  //' "$WORK/backup_manifest.sha256" \
    | awk -F/ '{ if (($1=="runs" || $1=="data" || $1=="config") && NF>1) print $1"/"$2; else print $1 }' | sort -u)
fi

# 3. move the current data aside (never deleted here)
mkdir -p "$ASIDE"
for root in "${ROOTS[@]}"; do
  [[ -n "$root" && "$root" != *..* ]] || continue
  [[ -e "$WORK/$root" ]] || continue
  for live in "$LIVE/$root" "$LIVE/$root-wal" "$LIVE/$root-shm" "$LIVE/$root-journal"; do
    if [[ -e "$live" ]]; then
      rel="${live#"$LIVE/"}"
      mkdir -p "$ASIDE/$(dirname "$rel")"
      mv -- "$live" "$ASIDE/$rel"
    fi
  done
done

# 4. swap the restored roots in
for root in "${ROOTS[@]}"; do
  [[ -n "$root" && "$root" != *..* ]] || continue
  [[ -e "$WORK/$root" ]] || continue
  mkdir -p "$LIVE/$(dirname "$root")"
  mv -- "$WORK/$root" "$LIVE/$root"
done
cleanup

echo "restore verified: $FILE_COUNT files OK"
echo "  archive:  $ARCHIVE"
echo "  restored: $LIVE (in place)"
echo "  previous data moved to: $ASIDE (remove it yourself after checking the service)"

# 5. post-restore steps
POST=("from scripts.check_index_consistency import check_collection; import json; r = check_collection(None, full=True); print(json.dumps({k: r[k] for k in ('consistent', 'keyword_rows', 'vector_rows')})); raise SystemExit(0 if r['consistent'] else 1)"
      "from rag_core import answer_cache; answer_cache.clear(); print('answer cache cleared')")
if [[ "$LIVE" == "$ROOT_DIR" && -x "$ROOT_DIR/.venv/bin/python" && "${RESTORE_SKIP_POST:-}" != "1" ]]; then
  for step in "${POST[@]}"; do
    if ! (cd "$ROOT_DIR" && .venv/bin/python -c "$step"); then
      echo "WARNING: post-restore step failed: $step" >&2
    fi
  done
  echo "  next: start the API, check /ready, run a search smoke test (scripts/deploy_smoke.sh)"
else
  echo "  next (in the installation directory, API stopped):"
  for step in "${POST[@]}"; do echo "    .venv/bin/python -c \"$step\""; done
  echo "  then start the API, check /ready, run a search smoke test (scripts/deploy_smoke.sh)"
fi
