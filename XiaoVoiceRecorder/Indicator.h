// Цвет RGB-светодиода: фон от состояния устройства и разовые вспышки поверх
// него (метка, остановка, ошибка). Чистая логика без Arduino — проверяется
// хост-тестами; Feedback.ino только переводит цвет в уровни на пинах.
//
//   запись         синий, горит всё время
//   метка          оранжевый 300 мс, потом снова синий
//   остановка      красный 1 с, потом выключен
//   ошибка         6 быстрых красных вспышек
//   неисправность  красный 100 мс раз в секунду
//   ожидание       выключен
#pragma once

#include <stdint.h>

#include "Types.h"

namespace led {

// Биты каналов. Оранжевый — отдельный бит: полный красный и немного
// зелёного; сколько именно зелёного, решает Feedback.ino (led_o_lvl).
enum Color : uint8_t {
  COLOR_OFF = 0,
  COLOR_RED = 1,
  COLOR_GREEN = 2,
  COLOR_BLUE = 4,
  COLOR_ORANGE = 8,
};

// Шаг вспышки: цвет и длительность. ms == 0 — конец последовательности.
struct Step {
  uint8_t color;
  uint16_t ms;
};

const Step FLASH_MARK[] = {{COLOR_ORANGE, 300}, {COLOR_OFF, 0}};
const Step FLASH_STOP[] = {{COLOR_RED, 1000}, {COLOR_OFF, 0}};
// Проверка пайки по команде t: каждый цвет отдельно, чтобы сверить провода.
const Step FLASH_TEST[] = {{COLOR_RED, 2000}, {COLOR_OFF, 300}, {COLOR_GREEN, 2000}, {COLOR_OFF, 300},
                           {COLOR_BLUE, 2000}, {COLOR_OFF, 300}, {COLOR_ORANGE, 2000}, {COLOR_OFF, 0}};
const Step FLASH_ERROR[] = {{COLOR_RED, 50}, {COLOR_OFF, 50}, {COLOR_RED, 50}, {COLOR_OFF, 50},
                            {COLOR_RED, 50}, {COLOR_OFF, 50}, {COLOR_RED, 50}, {COLOR_OFF, 50},
                            {COLOR_RED, 50}, {COLOR_OFF, 50}, {COLOR_RED, 50}, {COLOR_OFF, 0}};

// Разовая вспышка. Новая сразу заменяет недоигранную.
class Flash {
 public:
  void play(const Step *steps, uint32_t now) {
    m_steps = steps;
    m_index = 0;
    m_stepStartedMs = now;
  }

  // Цвет вспышки в момент now или -1, если вспышки нет.
  int colorAt(uint32_t now) {
    while (m_steps != nullptr && m_steps[m_index].ms != 0) {
      if (now - m_stepStartedMs < m_steps[m_index].ms) {
        return m_steps[m_index].color;
      }
      m_stepStartedMs += m_steps[m_index].ms;
      ++m_index;
    }
    m_steps = nullptr;
    return -1;
  }

 private:
  const Step *m_steps = nullptr;
  uint8_t m_index = 0;
  uint32_t m_stepStartedMs = 0;
};

inline uint8_t stateColor(DeviceState state, uint32_t now) {
  if (state == ST_RECORDING) {
    return COLOR_BLUE;
  }
  if (state == ST_FAULT) {
    return (now % 1000) < 100 ? COLOR_RED : COLOR_OFF;
  }
  return COLOR_OFF;
}

inline uint8_t colorAt(DeviceState state, Flash &flash, uint32_t now) {
  const int c = flash.colorAt(now);
  return c >= 0 ? static_cast<uint8_t>(c) : stateColor(state, now);
}

}  // namespace led
