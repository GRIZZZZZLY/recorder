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
# rsync есть на VPS, но не в Git Bash на Windows: там та же выкладка идёт
# tar-ом по ssh. Целевые каталоги перед распаковкой очищаются, как --delete.
if command -v rsync >/dev/null; then
  rsync -a --delete --exclude='__pycache__' --exclude='.pytest_cache' server/ "$HOST:$TARGET/server/"
  rsync -a --delete shared/ "$HOST:$TARGET/shared/"
  rsync -a --delete tools/ "$HOST:$TARGET/tools/"
  rsync -a --delete web/out/ "$HOST:$TARGET/web/out/"
else
  ssh "$HOST" "rm -rf '$TARGET/server' '$TARGET/shared' '$TARGET/tools' '$TARGET/web/out' && mkdir -p '$TARGET/web'"
  tar czf - --exclude='__pycache__' --exclude='.pytest_cache' server shared tools web/out \
    | ssh "$HOST" "tar xzf - -C '$TARGET' && chmod +x '$TARGET'/tools/*.sh"
fi

echo "Выложено. Перезапустить службы:"
echo "  ssh $HOST 'sudo systemctl restart recorder-api recorder-worker'"
