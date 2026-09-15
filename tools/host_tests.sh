#!/usr/bin/env bash
# Хост-тесты прошивки: чистая логика (кольцевой буфер, кнопка, арифметика
# частей, правила отправки) и жизненный цикл записи поверх хранилища в памяти
# (START с предзаписью, ротация, метки, STOP с .END, восстановление, очистка).
# Платы не требуют, Arduino не требуют.
#
#   bash tools/host_tests.sh            локально, нужен g++
#   bash tools/host_tests.sh --remote   на VPS (ssh config1), если g++ нет
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="XiaoVoiceRecorder"
FLAGS="-std=c++17 -O2 -Wall -Wextra -Werror -pthread"
TESTS="test_core test_session"

if [ "${1:-}" = "--remote" ]; then
  HOST="${RECORDER_HOST:-config1}"
  cd "$ROOT"
  tar czf - "$SRC"/*.h "$SRC"/host_tests \
    | ssh "$HOST" "rm -rf /tmp/recorder-host-tests && mkdir -p /tmp/recorder-host-tests \
        && tar xzf - -C /tmp/recorder-host-tests && cd /tmp/recorder-host-tests \
        && for t in $TESTS; do g++ $FLAGS -o \$t $SRC/host_tests/\$t.cpp && ./\$t || exit 1; done"
  exit $?
fi

if ! command -v g++ >/dev/null; then
  echo "host_tests.sh: g++ не найден. Запустите с --remote (сборка и прогон на VPS)." >&2
  exit 2
fi

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
for t in $TESTS; do
  g++ $FLAGS -o "$OUT/$t" "$ROOT/$SRC/host_tests/$t.cpp"
  "$OUT/$t"
done
