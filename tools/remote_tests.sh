#!/usr/bin/env bash
# Серверные тесты на VPS. Нужны там, где на машине разработки нет ffmpeg:
# проверки архива WAV -> FLAC без него пропускаются молча.
#
#   bash tools/remote_tests.sh              все тесты сервера
#   bash tools/remote_tests.sh test_archive.py -q   произвольные аргументы pytest
#
# Код копируется во временный каталог и там же исполняется: рабочие /opt/recorder
# и /var/lib/recorder не затрагиваются.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${RECORDER_HOST:-config1}"
REMOTE_DIR="/tmp/recorder-server-tests"

cd "$ROOT"
tar czf - --exclude='__pycache__' --exclude='.pytest_cache' server shared \
  | ssh "$HOST" "rm -rf '$REMOTE_DIR' && mkdir -p '$REMOTE_DIR' \
      && tar xzf - -C '$REMOTE_DIR' \
      && cd '$REMOTE_DIR/server' && timeout --kill-after=30 1200 python3 -m pytest ${*:--q}"
# timeout: 7 окт 2026 зависшая проверка звала настоящий claude; оборванный
# прогон оставил три процесса claude, которые сутки держали 3,9 ГБ памяти.
# Вызов настоящего claude в проверках закрыт фикстурой, это — страховка.
