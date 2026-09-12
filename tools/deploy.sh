#!/usr/bin/env bash
# Сборка фронтенда и выкладка на VPS. Node нужен только здесь.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${RECORDER_HOST:-config1}"
TARGET="${RECORDER_TARGET:-/opt/recorder}"

# Страховка от случайного запуска не в том каталоге и не на той машине:
# без этой проверки rsync --delete применился бы к TARGET по умолчанию,
# даже если скрипт запущен из чужого или неполного клона репозитория.
for rel in web/package.json server/app.py shared; do
  if [ ! -e "$ROOT/$rel" ]; then
    echo "deploy.sh: не найдено $ROOT/$rel — это не похоже на репозиторий Recorder_v0_1. Останов." >&2
    exit 1
  fi
done

cd "$ROOT/web"
npm run typecheck
npm run build

cd "$ROOT"
rsync -av --delete \
  --exclude='__pycache__' \
  server/ "$HOST:$TARGET/server/"
rsync -av --delete shared/ "$HOST:$TARGET/shared/"
rsync -av --delete web/out/ "$HOST:$TARGET/web/out/"

echo "Выложено. Перезапустить службы:"
echo "  ssh $HOST 'sudo systemctl restart recorder-api recorder-worker'"
