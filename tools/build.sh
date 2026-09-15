#!/usr/bin/env bash
# Сборка прошивки без платы. Проверяет, что код компилируется и влезает во флеш.
# Использование:  bash tools/build.sh [--clean]
#
# Ищет arduino-cli так: $ARDUINO_CLI, затем в PATH, затем на E: (тулчейн
# владельца). Каталоги ядра можно переопределить через ARDUINO_DIRECTORIES_*:
# в CI они лежат в рабочем каталоге, у владельца — на E:, чтобы не занимать C:.
set -euo pipefail

SKETCH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/XiaoVoiceRecorder"
FQBN="esp32:esp32:XIAO_ESP32S3:PSRAM=opi,FlashSize=8M"
OWNER_TOOLCHAIN="E:/arduino-toolchain"

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

"$ACLI" compile --fqbn "$FQBN" --warnings all "$@" "$SKETCH"
