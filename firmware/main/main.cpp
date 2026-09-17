// Переходная сборка через ESP-IDF + Arduino как компонент.
//
// Здесь нет своего кода: три .ino включаются подряд в один translation unit —
// ровно так их склеивает сборщик Arduino. setup() и loop() запускает сам
// компонент Arduino (CONFIG_AUTOSTART_ARDUINO=y в sdkconfig.defaults), поэтому
// app_main() не нужен. Поведение устройства обязано совпадать с Arduino-сборкой
// байт в байт по смыслу: это критерий первого этапа миграции.
//
// Что дальше, по одной подсистеме за раз (firmware/README_RU.md):
//   ESP_I2S -> i2s_new_channel / i2s_channel_init_pdm_rx_mode / i2s_channel_read
//   SD      -> FATFS + SDSPI
//   Preferences -> NVS, WiFi -> esp_wifi, WiFiClientSecure -> esp_http_client + esp-tls
// Логика записи (Session.h, RingBuffer.h, AudioFormat.h, Button.h, UploadPolicy.h)
// от этой замены не меняется: она Arduino не видит и сейчас.
#include <Arduino.h>

#include "XiaoVoiceRecorder.ino"
#include "Uploader.ino"
#include "Feedback.ino"
