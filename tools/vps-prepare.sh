#!/usr/bin/env bash
# Первичная подготовка VPS под диктофон: каталоги, файл настроек с пустыми
# секретами, юниты systemd. Секреты владелец вводит отдельно, здесь их нет.
set -euo pipefail

sudo mkdir -p /opt/recorder /var/lib/recorder /etc/recorder
sudo chown zubr:zubr /opt/recorder /var/lib/recorder

if [ ! -f /etc/recorder/recorder.env ]; then
  TOKEN="$(openssl rand -hex 24)"
  sudo tee /etc/recorder/recorder.env >/dev/null <<EOF
# Диктофон: настройки служб. Права 0600, владелец zubr. В git не попадает.
RECORDER_DATA=/var/lib/recorder
RECORDER_WEB_DIR=/opt/recorder/web/out
# Токен диктофона: тот же вводится в прошивку командой device_token=...
RECORDER_DEVICE_TOKEN=$TOKEN
# Пароль владельца для веб-интерфейса. Пусто = интерфейс закрыт (503).
RECORDER_OWNER_PASSWORD=
RECORDER_OWNER_USER=owner
# Ключ Deepgram. Пусто = расшифровка не запускается.
DEEPGRAM_API_KEY=
RECORDER_MAX_BYTES=67108864
RECORDER_QUIET_MINUTES=12
RECORDER_MIN_WORD_CONFIDENCE=0.7
RECORDER_SUMMARY_MODEL=claude-opus-5
RECORDER_SUMMARY_TIMEOUT=900
RECORDER_CLAUDE_BIN=/usr/bin/claude
EOF
  sudo chown zubr:zubr /etc/recorder/recorder.env
  sudo chmod 0600 /etc/recorder/recorder.env
  echo "env: создан, токен диктофона сгенерирован"
else
  echo "env: уже есть, не трогаю"
fi

sudo tee /etc/systemd/system/recorder-api.service >/dev/null <<'EOF'
[Unit]
Description=Recorder intake API
After=network.target

[Service]
User=zubr
WorkingDirectory=/opt/recorder/server
EnvironmentFile=/etc/recorder/recorder.env
ExecStart=/usr/bin/python3 -m uvicorn app:app --host 127.0.0.1 --port 8081
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/recorder-worker.service >/dev/null <<'EOF'
[Unit]
Description=Recorder transcription and summary worker
After=network.target recorder-api.service

[Service]
User=zubr
WorkingDirectory=/opt/recorder/server
EnvironmentFile=/etc/recorder/recorder.env
ExecStart=/usr/bin/python3 worker.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
echo "systemd: юниты записаны"
ls -la /opt/recorder /etc/recorder
