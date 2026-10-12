/*
 * Индикация: RGB-светодиод и вибромотор. Пины по умолчанию — из профиля платы
 * (BoardConfig.h): красный на GPIO4, зелёный и синий на GPIO5/6 (только
 * прототип XIAO Sense; на RevA светодиод один), затвор вибро на GPIO3.
 * Значение из NVS (led_pin, led_g_pin, led_b_pin, vibro_pin через Serial)
 * перекрывает профиль, -1 выключает канал. Встроенный LED Sense на GPIO21
 * занят CS карты и здесь не используется.
 *
 * Цвета — в Indicator.h: запись синим, метка оранжевым, остановка красным,
 * в ожидании выключен. Без зелёного и синего канала любой цвет зажигает
 * единственный светодиод. Яркость каждого канала (led_r_lvl, led_g_lvl,
 * led_b_lvl, 0..255) и доля зелёного в оранжевом (led_o_lvl) подстраиваются
 * через Serial под реальные резисторы; команда t показывает цвета по очереди.
 *
 * Вибромотор ERM нельзя вешать прямо на GPIO: нужен транзистор/MOSFET,
 * резистор в базу/затвор и диод параллельно мотору. Пин здесь управляет
 * ключом, не мотором.
 *
 * Все паттерны неблокирующие: feedbackService() двигает их по millis().
 *
 *   start      вибро 1 x 150 мс
 *   mark       вибро 2 x 60 мс
 *   stop       вибро 1 x 400 мс
 *   error      вибро 3 x 80 мс
 */

#include "Indicator.h"
#include "Types.h"

namespace fb {
constexpr const char *NVS_NAMESPACE = "recorder";

Channel vibro;
// Пины светодиода: красный, зелёный, синий.
int ledPins[3] = {-1, -1, -1};
// Яркость каналов 0..255. Красному через 330 Ом достаётся меньше тока, чем
// зелёному и синему через 47 Ом: без подстройки синий и зелёный забивают его.
// Четвёртый — зелёный внутри оранжевого: меньше, чем в одиночном зелёном,
// иначе смесь с красным выглядит жёлтой. Значения подобраны на глаз на
// прототипе 10 окт (330 / 47 / 47 Ом); NVS их перекрывает. Оранжевый 12 окт
// на IDF-сборке при 8 выглядел красным, владелец подтвердил 16 — почему при
// тех же числах цвет сдвинулся, не выяснено.
uint8_t ledLevels[4] = {255, 50, 70, 16};
// Частота ШИМ светодиода выше слышимой: ток светодиода не должен давать
// тон в микрофон.
constexpr uint32_t LED_PWM_HZ = 20000;
led::Flash ledFlash;
uint8_t ledShown = 0xFF;  // 0xFF — ещё ничего не выводилось

const uint16_t START_V[] = {150, 0};
const uint16_t MARK_V[] = {60, 80, 60, 0};
const uint16_t STOP_V[] = {400, 0};
const uint16_t ERROR_V[] = {80, 80, 80, 80, 80, 0};
}  // namespace fb

void fbSet(fb::Channel &ch, bool on) {
  ch.level = on;
  if (ch.pin >= 0) {
    digitalWrite(ch.pin, on ? HIGH : LOW);
  }
}

void fbPlay(fb::Channel &ch, const uint16_t *pattern) {
  uint8_t n = 0;
  while (n < fb::MAX_STEPS && pattern[n] != 0) {
    ch.steps[n] = pattern[n];
    ++n;
  }
  if (n < fb::MAX_STEPS) {
    ch.steps[n] = 0;
  }
  ch.index = 0;
  ch.stepStartedMs = millis();
  fbSet(ch, true);
}

bool fbPlaying(const fb::Channel &ch) {
  return ch.index < fb::MAX_STEPS && ch.steps[ch.index] != 0;
}

void fbAdvance(fb::Channel &ch, uint32_t now) {
  if (!fbPlaying(ch)) {
    return;
  }
  if (now - ch.stepStartedMs < ch.steps[ch.index]) {
    return;
  }
  ++ch.index;
  ch.stepStartedMs = now;
  if (fbPlaying(ch)) {
    fbSet(ch, (ch.index % 2) == 0);  // чётные шаги — включено
  } else {
    fbSet(ch, false);
  }
}

// Старый пин гасится, новый становится выходом с низким уровнем.
void fbApplyPin(int &slot, int pin) {
  if (slot >= 0) {
    digitalWrite(slot, LOW);
  }
  slot = pin;
  if (pin >= 0) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  }
}

// Каналы светодиода управляются ШИМ (analogWrite), поэтому старый пин сначала
// гасится через ШИМ, а pinMode отцепляет его от ШИМ.
void ledApplyPin(int i, int pin) {
  const int old = fb::ledPins[i];
  if (old >= 0) {
    analogWrite(old, 0);
    pinMode(old, OUTPUT);
    digitalWrite(old, LOW);
  }
  fb::ledPins[i] = pin;
  if (pin >= 0) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  }
  fb::ledShown = 0xFF;  // новый пин получит текущий цвет
}

void ledShow(uint8_t color) {
  if (color == fb::ledShown) {
    return;
  }
  fb::ledShown = color;
  const bool single = fb::ledPins[1] < 0 && fb::ledPins[2] < 0;
  const bool orange = (color & led::COLOR_ORANGE) != 0;
  uint8_t duty[3] = {0, 0, 0};
  if (single) {
    duty[0] = color != led::COLOR_OFF ? fb::ledLevels[0] : 0;
  } else {
    for (int i = 0; i < 3; ++i) {
      duty[i] = (color & (1 << i)) != 0 ? fb::ledLevels[i] : 0;
    }
    if (orange) {
      duty[0] = fb::ledLevels[0];
      duty[1] = fb::ledLevels[3];
    }
  }
  for (int i = 0; i < 3; ++i) {
    if (fb::ledPins[i] >= 0) {
      analogWrite(fb::ledPins[i], duty[i]);
    }
  }
}

namespace fb {
const char *const LED_KEYS[3] = {"led_pin", "led_g_pin", "led_b_pin"};
const char *const LEVEL_KEYS[4] = {"led_r_lvl", "led_g_lvl", "led_b_lvl", "led_o_lvl"};
const int LED_DEFAULTS[3] = {board::LED_PIN, board::LED_G_PIN, board::LED_B_PIN};
}  // namespace fb

void feedbackSetup() {
  Preferences prefs;
  prefs.begin(fb::NVS_NAMESPACE, true);
  int pins[3];
  for (int i = 0; i < 3; ++i) {
    pins[i] = prefs.getInt(fb::LED_KEYS[i], fb::LED_DEFAULTS[i]);
  }
  for (int i = 0; i < 4; ++i) {
    fb::ledLevels[i] = prefs.getUChar(fb::LEVEL_KEYS[i], fb::ledLevels[i]);
  }
  const int vibroPin = prefs.getInt("vibro_pin", board::VIBRO_PIN);
  prefs.end();
  // Пины ещё не на ШИМ, поэтому меняется общая частота для analogWrite.
  analogWriteFrequency(pins[0] >= 0 ? pins[0] : 0, fb::LED_PWM_HZ);
  for (int i = 0; i < 3; ++i) {
    ledApplyPin(i, pins[i]);
  }
  fbApplyPin(fb::vibro.pin, vibroPin);
  Serial.printf("[ui] LED R/G/B pins %d/%d/%d, яркость %u/%u/%u, оранжевый %u, vibro pin %d (-1 = не подключено; профиль %s: %d/%d/%d, %d)\n",
                pins[0], pins[1], pins[2], fb::ledLevels[0], fb::ledLevels[1], fb::ledLevels[2], fb::ledLevels[3], vibroPin,
                board::ID, board::LED_PIN, board::LED_G_PIN, board::LED_B_PIN, board::VIBRO_PIN);
}

// Проверка пайки: каждый цвет по 2 с, порядок пишется в Serial.
void feedbackLedTest() {
  Serial.printf("[led] Тест: красный, зелёный, синий, оранжевый — по 2 с. Яркость %u/%u/%u, оранжевый %u.\n",
                fb::ledLevels[0], fb::ledLevels[1], fb::ledLevels[2], fb::ledLevels[3]);
  fb::ledFlash.play(led::FLASH_TEST, millis());
}

// led_r_lvl=N / led_g_lvl=N / led_b_lvl=N — яркость канала 0..255,
// led_o_lvl=N — сколько зелёного в оранжевом.
bool setLedLevel(const String &key, const String &value) {
  for (int i = 0; i < 4; ++i) {
    if (key == fb::LEVEL_KEYS[i]) {
      const int level = value.toInt();
      if (level < 0 || level > 255) {
        Serial.println("[ui] Яркость вне диапазона 0..255.");
        return true;
      }
      Preferences prefs;
      prefs.begin(fb::NVS_NAMESPACE, false);
      prefs.putUChar(key.c_str(), static_cast<uint8_t>(level));
      prefs.end();
      fb::ledLevels[i] = static_cast<uint8_t>(level);
      fb::ledShown = 0xFF;
      Serial.printf("[ui] %s = %d сохранено\n", key.c_str(), level);
      return true;
    }
  }
  return false;
}

// Настройка индикации через Serial: пины led_pin / led_g_pin / led_b_pin /
// vibro_pin (-1 выключает) и яркость led_*_lvl. Пин занятый картой,
// микрофоном, кнопкой или АЦП батареи не принимается.
bool setFeedbackConfig(const String &key, const String &value) {
  if (setLedLevel(key, value)) {
    return true;
  }
  int ledIndex = -1;
  for (int i = 0; i < 3; ++i) {
    if (key == fb::LED_KEYS[i]) {
      ledIndex = i;
    }
  }
  if (ledIndex < 0 && key != "vibro_pin") {
    return false;
  }
  const int pin = value.toInt();
  static const int reserved[] = {board::REC_BUTTON_PIN, board::REC_BUTTON2_PIN, board::SD_SCK_PIN, board::SD_MISO_PIN,
                                 board::SD_MOSI_PIN, board::SD_CS_PIN, board::PDM_CLK_PIN,
                                 board::PDM_DATA_PIN, board::BAT_ADC_PIN};
  for (int r : reserved) {
    if (pin == r && pin >= 0) {
      Serial.printf("[ui] GPIO%d занят картой, микрофоном, кнопкой или АЦП батареи.\n", pin);
      return true;
    }
  }
  if (pin < -1 || pin > 48) {
    Serial.println("[ui] Пин вне диапазона -1..48.");
    return true;
  }
  Preferences prefs;
  prefs.begin(fb::NVS_NAMESPACE, false);
  prefs.putInt(key.c_str(), pin);
  prefs.end();
  if (ledIndex >= 0) {
    ledApplyPin(ledIndex, pin);
  } else {
    fbApplyPin(fb::vibro.pin, pin);
  }
  Serial.printf("[ui] %s = %d сохранено\n", key.c_str(), pin);
  return true;
}

void feedbackEvent(const char *name) {
  Serial.printf("[ui] %s\n", name);
  const uint32_t now = millis();
  if (strcmp(name, "start") == 0) {
    fbPlay(fb::vibro, fb::START_V);
  } else if (strcmp(name, "mark") == 0) {
    fbPlay(fb::vibro, fb::MARK_V);
    fb::ledFlash.play(led::FLASH_MARK, now);
  } else if (strcmp(name, "stop") == 0) {
    fbPlay(fb::vibro, fb::STOP_V);
    fb::ledFlash.play(led::FLASH_STOP, now);
  } else if (strcmp(name, "error") == 0) {
    fbPlay(fb::vibro, fb::ERROR_V);
    fb::ledFlash.play(led::FLASH_ERROR, now);
  }
  // Цвет вспышки — сразу, не дожидаясь feedbackService(): после остановки цикл
  // на секунды уходит в карту и сеть, и вспышка истекла бы, так и не загоревшись.
  const int flash = fb::ledFlash.colorAt(now);
  if (flash >= 0) {
    ledShow(static_cast<uint8_t>(flash));
  }
}

void feedbackService(DeviceState state) {
  const uint32_t now = millis();
  fbAdvance(fb::vibro, now);
  ledShow(led::colorAt(state, fb::ledFlash, now));
}
