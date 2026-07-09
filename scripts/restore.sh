#!/usr/bin/env bash
# Restore a backup archive created by scripts/backup.sh (Prompt023).
#
# Default is SAFE: extracts into a staging directory and verifies every file
# against the embedded sha256 manifest. Restoring over live data requires the
# explicit --in-place flag (which still verifies hashes after extraction).
#
# Usage: scripts/restore.sh ARCHIVE [--target DIR] [--in-place --source-dir DIR]
set -euo pipefail

ARCHIVE="${1:-}"
[[ -n "$ARCHIVE" ]] || { echo "usage: restore.sh ARCHIVE [--target DIR] [--in-place --source-dir DIR]" >&2; exit 2; }
shift

TARGET=""
IN_PLACE=0
SOURCE_DIR="."
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --in-place) IN_PLACE=1; shift ;;
    --source-dir) SOURCE_DIR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -f "$ARCHIVE" ]] || { echo "ERROR: archive not found: $ARCHIVE" >&2; exit 1; }

if [[ "$IN_PLACE" == "1" ]]; then
  TARGET="$(cd "$SOURCE_DIR" && pwd)"
  echo "WARNING: --in-place restore into $TARGET (existing files will be overwritten)"
else
  [[ -n "$TARGET" ]] || TARGET="restore_staging_$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$TARGET"
fi

tar -xzf "$ARCHIVE" -C "$TARGET"

MANIFEST="$TARGET/backup_manifest.sha256"
[[ -f "$MANIFEST" ]] || { echo "ERROR: backup manifest missing in archive" >&2; exit 1; }

echo "verifying sha256 manifest..."
(
  cd "$TARGET"
  sha256sum -c backup_manifest.sha256 --quiet
)
FILE_COUNT="$(wc -l < "$MANIFEST")"
echo "restore verified: $FILE_COUNT files OK"
echo "  archive: $ARCHIVE"
echo "  target:  $TARGET"
if [[ "$IN_PLACE" != "1" ]]; then
  echo "  staging restore: inspect the target, then move directories into place manually."
fi
