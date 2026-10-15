#!/usr/bin/env bash
# idf.py для firmware/ без ручной настройки окружения.
#
#   bash tools/idf.sh build              собрать (firmware/build/recorder.bin)
#   bash tools/idf.sh -p COM5 flash      собрать и прошить плату
#   bash tools/idf.sh --check            есть ли ESP-IDF (для tools/check.sh)
#
# ESP-IDF ищется так: $IDF_PATH, затем E:/esp-idf/esp-idf-v5.5.5 (у владельца).
# Инструменты и кэш компонентов лежат рядом с ним, а не в профиле на C:.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IDF_DIR="${IDF_PATH:-E:/esp-idf/esp-idf-v5.5.5}"
if [ ! -f "$IDF_DIR/tools/idf.py" ]; then
  echo "idf.sh: ESP-IDF не найден в $IDF_DIR (задайте IDF_PATH)." >&2
  exit 1
fi
[ "${1:-}" = "--check" ] && exit 0

BASE="$(dirname "$IDF_DIR")"
export IDF_TOOLS_PATH="${IDF_TOOLS_PATH:-$BASE/tools}"
export IDF_COMPONENT_CACHE_PATH="${IDF_COMPONENT_CACHE_PATH:-$BASE/component-cache}"
cd "$ROOT/firmware"

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*)
    # Git Bash: idf.py не поддерживает MSYS, поэтому окружение ставит родной
    # export.bat в cmd. MSYSTEM снимается внутри cmd («set X=&&» без пробела):
    # env -u не помогает, Git Bash возвращает её дочернему процессу, а
    # export.bat и install.bat с ней отказываются работать.
    exec cmd //c "set MSYSTEM=&& call $(cygpath -w "$IDF_DIR")\\export.bat >nul && idf.py $*"
    ;;
  *)
    # shellcheck disable=SC1091
    . "$IDF_DIR/export.sh" >/dev/null
    exec idf.py "$@"
    ;;
esac
