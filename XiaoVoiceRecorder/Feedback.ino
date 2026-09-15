/*
 * Индикация: отдельный LED и вибромотор. Железа нет, пины не назначены:
 * задаются через Serial (led_pin=1, vibro_pin=2) и хранятся в NVS, -1 —
 * выключено. Встроенный LED на GPIO21 занят CS карты и здесь не используется.
 *
 * Вибромотор ERM нельзя вешать прямо на GPIO: нужен транзистор/MOSFET,
 * резистор в базу/затвор и диод параллельно мотору. Пин здесь управляет
 * ключом, не мотором.
 *
 * Все паттерны неблокирующие: feedbackService() двигает их по millis().
 *
 *   ARMED      LED: вспышка 10 мс раз в 10 с (почти выключен)
 *   RECORDING  LED: вспышка 30 мс раз в 3 с
 *   FAULT      LED: 100 мс раз в 1 с
 *   start      вибро 1 x 150 мс
 *   mark       вибро 2 x 60 мс
 *   stop       вибро 1 x 400 мс
 *   error      вибро 3 x 80 мс и LED 6 быстрых вспышек
 */

#include "Types.h"

namespace fb {
constexpr const char *NVS_NAMESPACE = "recorder";

Channel led;
Channel vibro;
uint32_t lastIdleBlinkMs = 0;

const uint16_t START_V[] = {150, 0};
const uint16_t MARK_V[] = {60, 80, 60, 0};
const uint16_t STOP_V[] = {400, 0};
const uint16_t ERROR_V[] = {80, 80, 80, 80, 80, 0};
const uint16_t ERROR_L[] = {50, 50, 50, 50, 50, 50, 50, 0};
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

void fbApplyPin(fb::Channel &ch, int pin) {
  if (ch.pin >= 0) {
    digitalWrite(ch.pin, LOW);
  }
  ch.pin = pin;
  if (pin >= 0) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  }
}

void feedbackSetup() {
  Preferences prefs;
  prefs.begin(fb::NVS_NAMESPACE, true);
  const int ledPin = prefs.getInt("led_pin", -1);
  const int vibroPin = prefs.getInt("vibro_pin", -1);
  prefs.end();
  fbApplyPin(fb::led, ledPin);
  fbApplyPin(fb::vibro, vibroPin);
  Serial.printf("[ui] LED pin %d, vibro pin %d (-1 = не подключено)\n", ledPin, vibroPin);
}

// led_pin=N / vibro_pin=N через Serial. -1 выключает. Пин занятый картой,
// микрофоном или кнопкой не принимается.
bool setFeedbackPin(const String &key, const String &value) {
  if (key != "led_pin" && key != "vibro_pin") {
    return false;
  }
  const int pin = value.toInt();
  static const int reserved[] = {cfg::REC_BUTTON_PIN, cfg::SD_SCK_PIN, cfg::SD_MISO_PIN,
                                 cfg::SD_MOSI_PIN, cfg::SD_CS_PIN, cfg::PDM_CLK_PIN,
                                 cfg::PDM_DATA_PIN};
  for (int r : reserved) {
    if (pin == r) {
      Serial.printf("[ui] GPIO%d занят картой, микрофоном или кнопкой.\n", pin);
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
  fbApplyPin(key == "led_pin" ? fb::led : fb::vibro, pin);
  Serial.printf("[ui] %s = %d сохранено\n", key.c_str(), pin);
  return true;
}

void feedbackEvent(const char *name) {
  Serial.printf("[ui] %s\n", name);
  if (strcmp(name, "start") == 0) {
    fbPlay(fb::vibro, fb::START_V);
  } else if (strcmp(name, "mark") == 0) {
    fbPlay(fb::vibro, fb::MARK_V);
  } else if (strcmp(name, "stop") == 0) {
    fbPlay(fb::vibro, fb::STOP_V);
  } else if (strcmp(name, "error") == 0) {
    fbPlay(fb::vibro, fb::ERROR_V);
    fbPlay(fb::led, fb::ERROR_L);
  }
}

void feedbackService(DeviceState state) {
  const uint32_t now = millis();
  fbAdvance(fb::vibro, now);
  fbAdvance(fb::led, now);
  if (fbPlaying(fb::led)) {
    return;
  }
  uint32_t period = 10000, flash = 10;
  if (state == ST_RECORDING) {
    period = 3000;
    flash = 30;
  } else if (state == ST_FAULT) {
    period = 1000;
    flash = 100;
  }
  if (now - fb::lastIdleBlinkMs >= period) {
    fb::lastIdleBlinkMs = now;
    fbSet(fb::led, true);
  } else if (fb::led.level && now - fb::lastIdleBlinkMs >= flash) {
    fbSet(fb::led, false);
  }
}
