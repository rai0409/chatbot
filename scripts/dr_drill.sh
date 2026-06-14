#!/usr/bin/env bash
# Disaster-recovery drill (Prompt063): SYNTHETIC data only.
#
# Creates a throwaway source with a synthetic vectorstore/, backs it up
# (scripts/backup.sh), restores to a staging target (scripts/restore.sh,
# hash-verified), and asserts the restored content matches. Never touches the
# repo's real vectorstore/data/runs. No .env, no Docker, no network, no secrets.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
SRC="$WORK/src"; OUT="$WORK/backups"; TARGET="$WORK/restore"
mkdir -p "$SRC/vectorstore" "$OUT"
# synthetic, clearly non-customer content
echo "synthetic-dr-content-$(date -u +%s)" > "$SRC/vectorstore/chroma.sqlite3"
echo "synthetic-segment" > "$SRC/vectorstore/segment.bin"

echo "== DR drill: backup =="
bash "$ROOT_DIR/scripts/backup.sh" --source-dir "$SRC" --output-dir "$OUT" >/dev/null
ARCHIVE="$(ls -1 "$OUT"/chatbot_backup_*.tar.gz | head -1)"
[ -n "$ARCHIVE" ] || { echo "DR DRILL FAIL: no archive"; exit 1; }

echo "== DR drill: restore (staging, hash-verified) =="
bash "$ROOT_DIR/scripts/restore.sh" "$ARCHIVE" --target "$TARGET" >/dev/null

echo "== DR drill: verify restored content matches source =="
if diff -r "$SRC/vectorstore" "$TARGET/vectorstore" >/dev/null; then
  echo "DR DRILL OK: restored content matches source (hash-verified archive)"
  exit 0
else
  echo "DR DRILL FAIL: restored content differs"
  exit 1
fi
