// Профиль платы: пины и формат звука. Единственное место, где прошивка знает,
// на какой плате работает. Профиль выбирается при сборке:
//
//   (по умолчанию)          XIAO ESP32-S3 Sense: встроенный PDM-микрофон, моно
//   -DRECORDER_BOARD_REVA   Recorder PCB V1 RevA: 2x IM72D128V, стерео
//
//   bash tools/build.sh                      # Sense
//   RECORDER_BOARD=reva bash tools/build.sh  # RevA
//
// Ни одна плата пока не проверена на железе: XIAO в пути, RevA в производстве.
// Логика записи (Session.h, RingBuffer.h, AudioFormat.h) этот файл не включает:
// она получает формат значением и о пинах не знает.
#pragma once

#include <stdint.h>

#include "AudioFormat.h"

namespace board {

#if defined(RECORDER_BOARD_REVA)

// Recorder PCB V1 RevA — своя плата 62 x 22 мм, XIAO ESP32-S3 без Sense.
// Распиновка по production package RevA; подтвердить по плате в руках.
// Схема и ECO-01: docs/hardware/recorder-pcb-v1-reva.md.
constexpr const char *NAME = "Recorder PCB V1 RevA";
constexpr const char *ID = "reva";

// Два IM72D128V01XTMA1 на общих CLK и DATA. MIC1 SELECT -> GND, MIC2 SELECT -> 3V3:
// каждый отдаёт свой полупериод CLK, драйвер PDM RX раскладывает их по слотам L/R.
constexpr int PDM_CLK_PIN = 5;
constexpr int PDM_DATA_PIN = 6;
constexpr audio::Format AUDIO = audio::STEREO_16K;

constexpr int SD_SCK_PIN = 7;
constexpr int SD_MISO_PIN = 8;
constexpr int SD_MOSI_PIN = 9;
constexpr int SD_CS_PIN = 44;

constexpr int REC_BUTTON_PIN = 2;
constexpr int REC_BUTTON2_PIN = -1;

// BAT+ -> делитель -> GPIO1 (ADC1_CH0, не конфликтует с Wi-Fi).
// Коэффициент делителя взят как 2:1 и НЕ подтверждён по схеме RevA: сверить
// с BOM и поправить здесь до первого чтения заряда.
constexpr int BAT_ADC_PIN = 1;
constexpr float BAT_DIVIDER = 2.0f;

// GPIO3 управляет затвором MOSFET (AO3400A), не мотором. GPIO4 — LED состояния.
constexpr int VIBRO_PIN = 3;
constexpr int LED_PIN = 4;

#else

// Seeed Studio XIAO ESP32-S3 Sense с платой расширения Sense. Прототип до
// приезда RevA. Камера не используется и не инициализируется.
constexpr const char *NAME = "XIAO ESP32-S3 Sense";
constexpr const char *ID = "xiao-sense";

constexpr int PDM_CLK_PIN = 42;
constexpr int PDM_DATA_PIN = 41;
constexpr audio::Format AUDIO = audio::MONO_16K;

constexpr int SD_SCK_PIN = 7;
constexpr int SD_MISO_PIN = 8;
constexpr int SD_MOSI_PIN = 9;
// GPIO21 — документированный CS microSD платы Sense. Тот же пин — встроенный
// LED, поэтому он не мигает независимо, пока карта смонтирована.
constexpr int SD_CS_PIN = 21;

// Встроенная кнопка BOOT с внешней подтяжкой: REC без пайки. Удержание при
// сбросе всё так же уводит в загрузчик. Подтвердить при первом включении.
constexpr int REC_BUTTON_PIN = 0;
// Внешняя кнопка прототипа: D1 (GPIO2) -> кнопка -> GND, подтяжка внутренняя.
// Работает вместе с BOOT; пока не припаяна, пин читается как «отпущено».
// Пины кнопки, вибро и LED — те же, что на RevA.
constexpr int REC_BUTTON2_PIN = 2;

// У штатной XIAO нет ни делителя, ни газометра: заряд прочитать нечем.
constexpr int BAT_ADC_PIN = -1;
constexpr float BAT_DIVIDER = 0.0f;

// Прототип: D2 (GPIO3) -> резистор -> затвор MOSFET вибро, D3 (GPIO4) ->
// резистор -> LED -> GND. Пока не припаяны, пин просто дёргается впустую.
// Перекрывается через Serial: led_pin=N vibro_pin=N, -1 выключает.
constexpr int VIBRO_PIN = 3;
constexpr int LED_PIN = 4;

#endif

constexpr bool HAS_BATTERY_ADC = BAT_ADC_PIN >= 0;

static_assert(AUDIO.bitsPerSample == 16, "Session.h пишет только 16-битный PCM");
static_assert(AUDIO.channels == 1 || AUDIO.channels == 2, "PDM RX даёт один или два слота");
static_assert(!HAS_BATTERY_ADC || BAT_DIVIDER > 1.0f, "делитель обязан понижать BAT+ до диапазона АЦП");

}  // namespace board
