#!/usr/bin/env bash
# Сборка прошивки без платы. Проверяет, что код компилируется и влезает во флеш.
# Использование:  bash tools/build.sh [--clean]
set -euo pipefail

ACLI="E:/arduino-toolchain/bin/arduino-cli.exe"
SKETCH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/XiaoVoiceRecorder"
FQBN="esp32:esp32:XIAO_ESP32S3:PSRAM=opi,FlashSize=8M"

# arduino-cli держит ядро и загрузки на E:, чтобы не занимать C: и D:.
export ARDUINO_DIRECTORIES_DATA="E:/arduino-toolchain/arduino-toolchaindata"
export ARDUINO_DIRECTORIES_DOWNLOADS="E:/arduino-toolchain/arduino-toolchaindownloads"

"$ACLI" compile --fqbn "$FQBN" --warnings all "$@" "$SKETCH"
