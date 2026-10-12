#!/usr/bin/env bash
# Все проверки проекта. CI нет: перед каждым пушем в master — этот скрипт,
# пушить только при зелёном итоге (CLAUDE.md).
#
#   bash tools/check.sh
#
# 1. Сервер: pytest. Без ffmpeg на машине проверки архива пропускались бы
#    молча, поэтому тогда весь набор идёт на VPS (tools/remote_tests.sh).
# 2. Прошивка: хост-тесты; без g++ — на VPS (tools/host_tests.sh --remote).
# 3. Прошивка: сборка Arduino, профиль sense.
# 4. Прошивка: сборка ESP-IDF — если idf.py есть в PATH. Плата работает на
#    IDF-сборке; без idf.py шаг пропускается с предупреждением в итоге.
# 5. Сайт: проверка типов, тесты, сборка.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
SKIPPED=()
step() { printf '\n=== %s ===\n' "$1"; }

step "сервер"
if command -v ffmpeg >/dev/null 2>&1; then
  (cd server && python -m pytest -q -p no:cacheprovider)
else
  bash tools/remote_tests.sh -q
fi

step "прошивка: хост-тесты"
if command -v g++ >/dev/null 2>&1; then
  bash tools/host_tests.sh
else
  bash tools/host_tests.sh --remote
fi

step "прошивка: сборка Arduino (sense)"
bash tools/build.sh --board sense

step "прошивка: сборка ESP-IDF"
if command -v idf.py >/dev/null 2>&1; then
  (cd firmware && idf.py build)
else
  echo "idf.py не найден — пропущено."
  SKIPPED+=("сборка ESP-IDF (плата работает на ней: прошивку из этой версии не шить)")
fi

step "сайт"
(cd web && { [ -d node_modules ] || npm ci; } && npm run typecheck && npm test && npm run build)

echo
if [ ${#SKIPPED[@]} -eq 0 ]; then
  echo "Все проверки прошли."
else
  echo "Проверки прошли, кроме пропущенных:"
  printf '  - %s\n' "${SKIPPED[@]}"
fi
