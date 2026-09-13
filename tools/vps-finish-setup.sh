#!/usr/bin/env bash
# Завершение установки на VPS, запускает владелец один раз:
#   ssh -t config1 bash /opt/recorder/tools/vps-finish-setup.sh
# Спрашивает ключ Deepgram и пароль владельца (ввод скрыт), пишет их в
# /etc/recorder/recorder.env, запускает службы, выпускает сертификат и
# включает nginx. Повторный запуск безопасен: заполненное не спрашивает.
set -euo pipefail

ENV=/etc/recorder/recorder.env
NAME=recorder.rocketlunch.dpdns.org

value() { sudo grep -E "^$1=" "$ENV" | cut -d= -f2-; }
put() { sudo sed -i "s|^$1=.*|$1=$2|" "$ENV"; }

if [ -z "$(value DEEPGRAM_API_KEY)" ]; then
  read -r -s -p "Ключ Deepgram (ввод не виден, Enter в конце): " KEY; echo
  [ -n "$KEY" ] || { echo "Пусто. Ничего не записано."; exit 1; }
  put DEEPGRAM_API_KEY "$KEY"
  echo "Ключ Deepgram записан."
else
  echo "Ключ Deepgram уже есть, не спрашиваю."
fi

if [ -z "$(value RECORDER_OWNER_PASSWORD)" ]; then
  read -r -s -p "Пароль владельца для сайта (ввод не виден, Enter в конце): " PASS; echo
  [ -n "$PASS" ] || { echo "Пусто. Ничего не записано."; exit 1; }
  put RECORDER_OWNER_PASSWORD "$PASS"
  echo "Пароль владельца записан. Логин: owner."
else
  echo "Пароль владельца уже есть, не спрашиваю."
fi

sudo systemctl enable --now recorder-api recorder-worker >/dev/null
sudo systemctl restart recorder-api recorder-worker
sleep 2
systemctl is-active recorder-api recorder-worker | paste -sd' ' | sed 's/^/Службы: /'

if ! getent hosts "$NAME" >/dev/null; then
  echo
  echo "Имя $NAME пока никуда не указывает."
  echo "Добавьте в dpdns запись A: $NAME -> $(curl -s -4 ifconfig.me), затем запустите эту команду ещё раз."
  echo "Службы уже работают, секреты записаны."
  exit 0
fi

if [ ! -d "/etc/letsencrypt/live/$NAME" ]; then
  sudo certbot certonly --nginx -d "$NAME" --non-interactive --agree-tos --keep-until-expiring \
    --register-unsafely-without-email 2>&1 | tail -3
fi

sudo tee /etc/nginx/sites-available/recorder >/dev/null <<EOF
server {
    listen 80;
    server_name $NAME;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    server_name $NAME;

    ssl_certificate     /etc/letsencrypt/live/$NAME/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$NAME/privkey.pem;

    # Часть записи весит около 19 МБ. Значение по умолчанию 1 МБ обрежет загрузку.
    client_max_body_size 64m;
    # Отдача 19 МБ с диктофона по домашнему Wi-Fi занимает десятки секунд.
    proxy_read_timeout 300s;
    proxy_request_buffering off;

    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
    }
}
EOF
sudo ln -sf /etc/nginx/sites-available/recorder /etc/nginx/sites-enabled/recorder
sudo nginx -t 2>&1 | tail -1
sudo systemctl reload nginx

echo
echo "Готово. Проверка снаружи:"
curl -s -o /dev/null -w "  сайт без пароля: HTTP %{http_code} (ожидается 401)\n" "https://$NAME/"
echo "  открыть на телефоне: https://$NAME/  логин owner"
echo "  токен диктофона для прошивки: sudo grep DEVICE_TOKEN $ENV"
