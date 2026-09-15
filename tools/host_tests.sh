#!/usr/bin/env bash
# Хост-тесты чистой логики прошивки (кольцевой буфер, кнопка, арифметика
# частей, правила отправки). Платы не требуют, Arduino не требуют.
#
#   bash tools/host_tests.sh            локально, нужен g++
#   bash tools/host_tests.sh --remote   на VPS (ssh config1), если g++ нет
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="XiaoVoiceRecorder"
FLAGS="-std=c++17 -O2 -Wall -Wextra -Werror -pthread"

if [ "${1:-}" = "--remote" ]; then
  HOST="${RECORDER_HOST:-config1}"
  cd "$ROOT"
  tar czf - "$SRC"/*.h "$SRC"/host_tests \
    | ssh "$HOST" "rm -rf /tmp/recorder-host-tests && mkdir -p /tmp/recorder-host-tests \
        && tar xzf - -C /tmp/recorder-host-tests \
        && cd /tmp/recorder-host-tests && g++ $FLAGS -o test_core $SRC/host_tests/test_core.cpp \
        && ./test_core"
  exit $?
fi

if ! command -v g++ >/dev/null; then
  echo "host_tests.sh: g++ не найден. Запустите с --remote (сборка и прогон на VPS)." >&2
  exit 2
fi

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
g++ $FLAGS -o "$OUT/test_core" "$ROOT/$SRC/host_tests/test_core.cpp"
"$OUT/test_core"
