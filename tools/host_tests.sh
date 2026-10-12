#!/usr/bin/env bash
# Хост-тесты прошивки. Платы не требуют, Arduino не требуют.
#
#   test_core         кольцевой буфер, кнопка, правила отправки — только то,
#                     ошибка в чём теряет звук или части
#   test_session      жизненный цикл записи поверх хранилища в памяти:
#                     предзапись, ротация, метки, STOP с .END, отказы карты,
#                     восстановление, очистка, CRC при записи
#   test_faults       пропадание питания в каждый опасный момент, долгая
#                     эксплуатация, номера сессий, точность метки
#   test_upload_pump  тело отправки байт в байт: докачка, короткие чтения,
#                     оборванная запись в сокет, кнопка важнее отправки
#
#   bash tools/host_tests.sh              локально, нужен g++
#   bash tools/host_tests.sh --sanitize   то же плюс ASan и UBSan
#   bash tools/host_tests.sh --remote     на VPS (ssh config1), если g++ нет
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="XiaoVoiceRecorder"
BASE_FLAGS="-std=c++17 -Wall -Wextra -Werror -pthread"
TESTS="test_core test_session test_faults test_upload_pump"

MODE="${1:-}"
SANITIZE=""
if [ "$MODE" = "--sanitize" ]; then
  # Выход за границы массива, использование освобождённой памяти, утечки и
  # неопределённое поведение — то, чего в C++ не видно ни глазами, ни тестами.
  SANITIZE="-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all -g -O1"
  shift || true
fi
FLAGS="$BASE_FLAGS ${SANITIZE:--O2}"

if [ "$MODE" = "--remote" ]; then
  HOST="${RECORDER_HOST:-config1}"
  cd "$ROOT"
  tar czf - "$SRC"/*.h "$SRC"/host_tests \
    | ssh "$HOST" "rm -rf /tmp/recorder-host-tests && mkdir -p /tmp/recorder-host-tests \
        && tar xzf - -C /tmp/recorder-host-tests && cd /tmp/recorder-host-tests \
        && for t in $TESTS; do g++ $BASE_FLAGS -O2 -o \$t $SRC/host_tests/\$t.cpp && ./\$t || exit 1; done"
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
