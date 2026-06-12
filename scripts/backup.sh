#!/usr/bin/env bash
# Backup the stateful runtime data (Prompt023): vectorstore/, data/approved_qa/,
# runs/audit/ — whichever exist under --source-dir — into a timestamped
# tar.gz with a sha256 manifest inside the archive. Read-only on the source.
#
# Usage: scripts/backup.sh [--source-dir DIR] [--output-dir DIR]
set -euo pipefail

SOURCE_DIR="."
OUTPUT_DIR="backups"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir) SOURCE_DIR="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_NAME="chatbot_backup_${TS}.tar.gz"
MANIFEST_NAME="backup_manifest.sha256"

TARGETS=()
for candidate in vectorstore data/approved_qa runs/audit; do
  if [[ -d "$SOURCE_DIR/$candidate" ]]; then
    TARGETS+=("$candidate")
  fi
done
if [[ "${#TARGETS[@]}" == "0" ]]; then
  echo "ERROR: nothing to back up under $SOURCE_DIR (expected vectorstore/, data/approved_qa/, runs/audit/)" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

# Manifest: sha256 of every file, relative paths, sha256sum -c compatible.
(
  cd "$SOURCE_DIR"
  find "${TARGETS[@]}" -type f -print0 | sort -z | xargs -0 -r sha256sum
) > "$STAGING/$MANIFEST_NAME"

tar -czf "$OUTPUT_DIR/$ARCHIVE_NAME" \
  -C "$STAGING" "$MANIFEST_NAME" \
  -C "$SOURCE_DIR" "${TARGETS[@]}"

FILE_COUNT="$(wc -l < "$STAGING/$MANIFEST_NAME")"
echo "backup written: $OUTPUT_DIR/$ARCHIVE_NAME"
echo "  source:  $SOURCE_DIR"
echo "  targets: ${TARGETS[*]}"
echo "  files:   $FILE_COUNT (sha256 manifest included: $MANIFEST_NAME)"
