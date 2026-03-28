#!/usr/bin/env bash
# make_split_zip.sh
# 使い方:
#   bash make_split_zip.sh
#   bash make_split_zip.sh booking_bundle /home/rai/booking_zoom_connect
#
# 出力例:
#   booking_bundle_app.zip
#   booking_bundle_ops.zip
#
# 方針:
# - docs は除外
# - node_modules / dist / .next など重いものは除外
# - 実装本体(app) と 運用・設定(ops) を2分割

set -euo pipefail

BASE_NAME="${1:-output}"
TARGET="${2:-$(pwd)}"

if ! command -v zip >/dev/null 2>&1; then
  echo "zip コマンドが見つかりません。Ubuntuなら: sudo apt-get update && sudo apt-get install -y zip" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。" >&2
  exit 1
fi

if [ ! -d "$TARGET" ]; then
  echo "対象ディレクトリが存在しません: $TARGET" >&2
  exit 1
fi

abs_path() {
  python3 - "$1" <<'PY'
import os, sys
print(os.path.abspath(sys.argv[1]))
PY
}

next_free_path() {
  python3 - "$1" <<'PY'
import os, sys

path = sys.argv[1]
dir_ = os.path.dirname(path)
base = os.path.basename(path)
stem, ext = os.path.splitext(base)

if not os.path.exists(path):
    print(path)
    raise SystemExit

i = 2
while True:
    cand = os.path.join(dir_, f"{stem}{i}{ext}")
    if not os.path.exists(cand):
        print(cand)
        break
    i += 1
PY
}

TARGET="$(abs_path "$TARGET")"

APP_OUT="$(next_free_path "$(abs_path "$BASE_NAME"_app.zip)")"
OPS_OUT="$(next_free_path "$(abs_path "$BASE_NAME"_ops.zip)")"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

APP_LIST="$TMP_DIR/app.lst"
OPS_LIST="$TMP_DIR/ops.lst"
: > "$APP_LIST"
: > "$OPS_LIST"

cd "$TARGET"

add_if_exists() {
  local list_file="$1"
  shift
  for p in "$@"; do
    if [ -e "$p" ]; then
      echo "$p" >> "$list_file"
    fi
  done
}

# -------------------------
# 1) app側: 実装本体
# -------------------------
add_if_exists "$APP_LIST" \
  apps \
  packages \
  prisma \
  src \
  lib \
  public \
  types \
  shared \
  features \
  modules

# app側にも最低限のルート設定ファイルは入れる
add_if_exists "$APP_LIST" \
  package.json \
  pnpm-lock.yaml \
  pnpm-workspace.yaml \
  yarn.lock \
  package-lock.json \
  turbo.json \
  nx.json \
  tsconfig.json \
  tsconfig.base.json \
  tsconfig.build.json \
  nest-cli.json \
  next.config.js \
  next.config.mjs \
  next.config.ts \
  postcss.config.js \
  postcss.config.cjs \
  tailwind.config.js \
  tailwind.config.cjs \
  tailwind.config.ts \
  eslint.config.js \
  eslint.config.mjs \
  .eslintrc \
  .eslintrc.json \
  .prettierrc \
  .prettierrc.json \
  .gitignore \
  .npmrc \
  Dockerfile \
  Dockerfile.api \
  Dockerfile.web \
  docker-compose.yml \
  docker-compose.yaml \
  README.md

# -------------------------
# 2) ops側: scripts / infra / CI / DB補助
# -------------------------
add_if_exists "$OPS_LIST" \
  scripts \
  infra \
  .github \
  prisma \
  migrations \
  sql \
  seed \
  seeds \
  docker \
  deployments \
  configs \
  config \
  Makefile \
  docker-compose.yml \
  docker-compose.yaml \
  .env.example \
  .env.local.example \
  .env.development.example \
  .env.production.example \
  package.json \
  pnpm-lock.yaml \
  pnpm-workspace.yaml \
  yarn.lock \
  package-lock.json \
  turbo.json \
  nx.json \
  tsconfig.json \
  tsconfig.base.json \
  tsconfig.build.json \
  nest-cli.json \
  README.md

# 空なら警告
if [ ! -s "$APP_LIST" ]; then
  echo "app用に対象が見つかりませんでした。対象構成を確認してください。" >&2
  exit 1
fi

if [ ! -s "$OPS_LIST" ]; then
  echo "ops用に対象が見つかりませんでした。対象構成を確認してください。" >&2
  exit 1
fi

# 共通除外
EXCLUDES=(
  "*/.git/*"
  "*/node_modules/*"
  "*/dist/*"
  "*/build/*"
  "*/.next/*"
  "*/out/*"
  "*/coverage/*"
  "*/.turbo/*"
  "*/.cache/*"
  "*/.venv/*"
  "*/venv/*"
  "*/__pycache__/*"
  "*/.pytest_cache/*"
  "*/.mypy_cache/*"
  "*/.idea/*"
  "*/.vscode/*"
  "*/tmp/*"
  "*/temp/*"
  "*/logs/*"
  "*/outputs/*"
  "*/docs/*"
  "docs/*"
  "*/.DS_Store"
  "*/.env"
  "*/.env.*"
  "!*/.env.example"
  "!*/.env.local.example"
  "!*/.env.development.example"
  "!*/.env.production.example"
  "*/**/*.pem"
  "*/**/*.key"
  "*/**/*secret*"
  "*/**/*.log"
  "*/**/*.sqlite"
  "*/**/*.db"
  "*/**/*.tar"
  "*/**/*.tar.gz"
  "*/**/*.zip"
  "*/**/*.pt"
  "*/**/*.pth"
  "*/**/*.onnx"
  "*/**/*.bin"
  "*/**/*.parquet"
  "*/**/*.feather"
  "*/**/*.csv"
  "*/**/*.tsv"
  "*/**/*.jpg"
  "*/**/*.jpeg"
  "*/**/*.png"
  "*/**/*.webp"
  "*/**/*.mp4"
  "*/**/*.mov"
  "*/**/*.pdf"
)

rm -f "$APP_OUT" "$OPS_OUT"

echo "[1/2] app zip を作成中: $APP_OUT"
zip -r -y "$APP_OUT" $(tr '\n' ' ' < "$APP_LIST") -x "${EXCLUDES[@]}"

echo "[2/2] ops zip を作成中: $OPS_OUT"
zip -r -y "$OPS_OUT" $(tr '\n' ' ' < "$OPS_LIST") -x "${EXCLUDES[@]}"

echo
echo "OK:"
echo "  app : $APP_OUT"
echo "  ops : $OPS_OUT"
echo "対象 : $TARGET"