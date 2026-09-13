#!/usr/bin/env bash
# Заменить один секрет в /etc/recorder/recorder.env. Запускает владелец:
#   ssh -t config1 bash /opt/recorder/tools/vps-set-secret.sh DEEPGRAM_API_KEY
#   ssh -t config1 bash /opt/recorder/tools/vps-set-secret.sh RECORDER_OWNER_PASSWORD
# Ввод скрыт, значение в чат и в историю команд не попадает. Ключ Deepgram
# проверяется живым запросом до записи: неверный ключ не сохраняется.
set -euo pipefail

ENV=/etc/recorder/recorder.env
KEY="${1:-}"
case "$KEY" in
  DEEPGRAM_API_KEY) LABEL="ключ Deepgram" ;;
  RECORDER_OWNER_PASSWORD) LABEL="пароль владельца для сайта" ;;
  RECORDER_DEVICE_TOKEN) LABEL="токен диктофона" ;;
  *) echo "Что менять: DEEPGRAM_API_KEY, RECORDER_OWNER_PASSWORD или RECORDER_DEVICE_TOKEN"; exit 1 ;;
esac

read -r -s -p "Новый $LABEL (ввод не виден, Enter в конце): " V; echo
[ -n "$V" ] || { echo "Пусто. Ничего не записано."; exit 1; }

if [ "$KEY" = DEEPGRAM_API_KEY ]; then
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Token $V" https://api.deepgram.com/v1/projects || true)"
  if [ "$CODE" != "200" ]; then
    echo "Deepgram не принял этот ключ (ответ $CODE). Ничего не записано."
    echo "Ключ берётся в console.deepgram.com, раздел API Keys, кнопка Create a New API Key; обычно 40 знаков."
    exit 1
  fi
  echo "Deepgram принял ключ."
fi
if [ "$KEY" = RECORDER_OWNER_PASSWORD ] && [ "${#V}" -lt 12 ]; then
  echo "Пароль короче 12 знаков. Сайт открыт в интернет, короткий пароль подбирается. Ничего не записано."
  exit 1
fi

if sudo grep -qE "^$KEY=" "$ENV"; then
  sudo sed -i "s|^$KEY=.*|$KEY=$V|" "$ENV"
else
  echo "$KEY=$V" | sudo tee -a "$ENV" >/dev/null
fi
unset V
sudo systemctl restart recorder-api recorder-worker
sleep 2
echo "Записано (${LABEL}). Службы: $(systemctl is-active recorder-api recorder-worker | paste -sd' ')"
