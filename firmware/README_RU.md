# Сборка через ESP-IDF

Тот же исходный код, что в `XiaoVoiceRecorder/`, собранный `idf.py` с Arduino
ESP32 как компонентом ESP-IDF 5.5.5. Версия ядра закреплена в
`main/idf_component.yml` и `dependencies.lock`: 3.3.12 — та, что работает на
плате с 12 окт 2026. Arduino-сборка (`tools/build.sh`, ядро 3.3.11) остаётся
рядом: тот же код, быстрая проверка, откат.

Чем отличается от Arduino-сборки: только настройками ядра, которые Arduino
зашивает в готовые библиотеки. `sdkconfig.defaults` повторяет sdkconfig
Arduino (`esp32s3-libs/3.3.11/qio_opi`, сверка 12 окт 2026) с одним намеренным
отличием — буфер отправки TCP 32 КиБ вместо 5744 байт. На плате 12 окт:
отправка 436 КиБ/с против 81 КиБ/с, потерь звука нет. Флаги, которые Arduino
берёт из `boards.txt` (USB-журнал, PSRAM), задаёт `CMakeLists.txt`: без них
журнал уходил мимо USB.

## Собрать и прошить

```bash
bash tools/idf.sh build              # firmware/build/recorder.bin
bash tools/idf.sh -p COM5 flash      # собрать и прошить плату
```

`tools/idf.sh` сам находит ESP-IDF и ставит окружение (в Git Bash — через
`export.bat` в cmd: `idf.py` не поддерживает MSYS). Первая сборка скачивает
компоненты и идёт около 6 минут, следующие — около минуты. `tools/check.sh`
собирает IDF перед каждым пушем в master.

Раздел настроек (NVS, 0x9000) прошивкой не трогается: Wi-Fi, сервер и токен
переживают переход в обе стороны. Откат на Arduino — `bash tools/build.sh
--upload -p COM5`.

## ESP-IDF на машине владельца

Установлен 15 окт 2026 в `E:/esp-idf/`:

| Что | Где |
| --- | --- |
| ESP-IDF v5.5.5 (архив с dl.espressif.com, SHA-256 сверен с GitHub) | `E:/esp-idf/esp-idf-v5.5.5` |
| Компилятор, cmake, ninja, Python-окружение (`install.bat esp32s3`) | `E:/esp-idf/tools` |
| Кэш скачанных компонентов | `E:/esp-idf/component-cache` |

На другой машине: задать `IDF_PATH` на каталог ESP-IDF 5.5.5, остальное
`tools/idf.sh` выведет рядом с ним. Инструменты ставятся один раз:
`install.bat esp32s3` (Windows, в cmd без `MSYSTEM`) или `./install.sh esp32s3`.
GitHub с машины владельца отдаёт ESP-IDF очень медленно (около 0,1 МБ/с), а
`dl.espressif.com` — около 4 МБ/с: архив релиза и `IDF_GITHUB_ASSETS=dl.espressif.com/github_assets`
для инструментов.

## Зачем

Arduino ESP32 3.3 уже построен поверх ESP-IDF 5.5, поэтому переход — не
переписывание, а снятие слоёв. Что даёт native IDF, чего нет у Arduino:

- PDM RX драйвер с двумя слотами на одной линии DATA — ровно под два
  IM72D128V на Recorder PCB RevA (`i2s_channel_init_pdm_rx_mode`);
- Task Watchdog на каждую задачу отдельно: захват, запись, сеть;
- A/B OTA с откатом: новая прошивка помечается рабочей только после
  самопроверки PSRAM, карты и первых кадров звука.

## Как собрать

```bash
cd firmware
idf.py set-target esp32s3
idf.py build                          # профиль XIAO Sense
idf.py -DRECORDER_BOARD=reva build    # профиль Recorder RevA
idf.py -p COMx flash monitor
```

Компонент Arduino подтягивается менеджером компонентов из
`main/idf_component.yml` при первой сборке; сеть нужна один раз.

## Что где

| Файл | Что |
| --- | --- |
| `CMakeLists.txt` | проект IDF, имя `recorder`; флаги USB-журнала и PSRAM из `boards.txt` |
| `sdkconfig.defaults` | настройки Arduino-сборки (FAT, lwIP, Wi-Fi, стеки, дамп при сбое) + буфер TCP 32 КиБ |
| `partitions.csv` | копия схемы Arduino `default_8MB`: NVS на том же месте, два раздела под OTA |
| `main/main.cpp` | включает три `.ino` в один translation unit — так же, как их склеивает Arduino |
| `main/idf_component.yml`, `dependencies.lock` | `espressif/arduino-esp32 ==3.3.12`, IDF `>=5.5,<6`; точные версии всех компонентов |

Своих исходников здесь нет и не должно появляться, пока не начнётся замена
подсистем: логика живёт в `XiaoVoiceRecorder/*.h` и проверяется хост-тестами.

## Порядок замены подсистем

По одной, с проверкой поведения после каждой. Порядок — по пользе для
Recorder RevA и по риску для записи:

1. **Звук.** `ESP_I2S` → `i2s_new_channel` + `i2s_channel_init_pdm_rx_mode` +
   `i2s_channel_read` в задаче захвата. `session::Writer` замены не заметит:
   он читает кольцевой буфер. Делать только после того, как Arduino-вариант
   реально записал звук на XIAO Sense — иначе нечего сравнивать.
2. **Карта.** Arduino `SD` → FATFS + SDSPI. Меняется только `SdStorage.h`
   (реализация `Storage`), `Session.h` не трогается.
3. **Настройки.** `Preferences` → NVS напрямую. Ключи и пространство
   `recorder` те же.
4. **Wi-Fi.** `WiFi` → `esp_wifi`.
5. **HTTPS.** `WiFiClientSecure` → `esp_http_client` / `esp-tls`, машина
   состояний `Uploader.ino` остаётся кооперативной.

Когда последняя зависимость от Arduino уйдёт, IDF обновляется до 6.x отдельным
шагом. До этого — 5.5.x, потому что Arduino 3.3 совместим именно с ним.

## Чего здесь нет намеренно

- Отдельного `app_main()`: автозапуск `setup()/loop()` даёт то же поведение
  без нового кода.
- Разбиения на компоненты `recorder_core` / `recorder_hal`: пока файлы
  включаются как есть. Разбиение имеет смысл на шаге 1, когда появится первый
  HAL-файл без Arduino.
- Watchdog и OTA: после того, как эта сборка хотя бы раз загрузилась на плату.
