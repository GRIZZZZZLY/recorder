#!/usr/bin/env bash
# Снимок исходников вместо коммита: git в проекте не заведён, а откатывать
# неудачный шаг чем-то надо. Собранный фронтенд и зависимости не копируются.
#
#     tools/snapshot.sh web-shell
set -euo pipefail

NAME="${1:?нужно имя снимка: tools/snapshot.sh <имя>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/.snapshots/$(date +%Y%m%d-%H%M%S)-$NAME"

mkdir -p "$DEST"
for part in server shared tools docs XiaoVoiceRecorder; do
    [ -e "$ROOT/$part" ] && cp -r "$ROOT/$part" "$DEST/"
done
if [ -d "$ROOT/web" ]; then
    mkdir -p "$DEST/web"
    (cd "$ROOT/web" && tar -cf - \
        --exclude=node_modules --exclude=.next --exclude=out .) | tar -xf - -C "$DEST/web"
fi
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} +

echo "Снимок: $DEST"
