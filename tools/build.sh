#!/usr/bin/env bash
# Сборка прошивки без платы. Проверяет, что код компилируется и влезает во флеш.
# Использование:  bash tools/build.sh [--board sense|reva] [--clean]
#                 RECORDER_BOARD=reva bash tools/build.sh
#
# Профиль платы (BoardConfig.h):
#   sense  XIAO ESP32-S3 Sense, встроенный микрофон, моно      — по умолчанию
#   reva   Recorder PCB V1 RevA, 2x IM72D128V, стерео           — платы ещё нет,
#          собирается статически, на железе не проверено
#
# Ищет arduino-cli так: $ARDUINO_CLI, затем в PATH, затем на E: (тулчейн
# владельца). Каталоги ядра можно переопределить через ARDUINO_DIRECTORIES_*:
# в CI они лежат в рабочем каталоге, у владельца — на E:, чтобы не занимать C:.
set -euo pipefail

SKETCH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/XiaoVoiceRecorder"
FQBN="esp32:esp32:XIAO_ESP32S3:PSRAM=opi,FlashSize=8M"
OWNER_TOOLCHAIN="E:/arduino-toolchain"

BOARD="${RECORDER_BOARD:-sense}"
if [ "${1:-}" = "--board" ]; then
  BOARD="${2:?build.sh: --board требует sense или reva}"
  shift 2
fi
# Профиль передаётся флагом препроцессора, а не правкой исходников: один и тот
# же код собирается под обе платы. Оба варианта пишутся в разные каталоги,
# чтобы объекты одного профиля не подменяли другой.
case "$BOARD" in
  sense) BOARD_FLAGS=() ;;
  reva)  BOARD_FLAGS=(--build-property "compiler.cpp.extra_flags=-DRECORDER_BOARD_REVA") ;;
  *) echo "build.sh: неизвестный профиль '$BOARD' (sense | reva)" >&2; exit 2 ;;
esac

if [ -n "${ARDUINO_CLI:-}" ]; then
  ACLI="$ARDUINO_CLI"
elif command -v arduino-cli >/dev/null 2>&1; then
  ACLI="arduino-cli"
elif [ -x "$OWNER_TOOLCHAIN/bin/arduino-cli.exe" ]; then
  ACLI="$OWNER_TOOLCHAIN/bin/arduino-cli.exe"
  export ARDUINO_DIRECTORIES_DATA="${ARDUINO_DIRECTORIES_DATA:-$OWNER_TOOLCHAIN/arduino-toolchaindata}"
  export ARDUINO_DIRECTORIES_DOWNLOADS="${ARDUINO_DIRECTORIES_DOWNLOADS:-$OWNER_TOOLCHAIN/arduino-toolchaindownloads}"
else
  echo "build.sh: arduino-cli не найден. Задайте ARDUINO_CLI или положите его в PATH." >&2
  exit 2
fi

echo "build.sh: профиль $BOARD"
"$ACLI" compile --fqbn "$FQBN" --warnings all \
  --build-path "${RECORDER_BUILD_DIR:-${TMPDIR:-/tmp}/recorder-build-$BOARD}" \
  "${BOARD_FLAGS[@]}" "$@" "$SKETCH"
