// Одна кнопка, два жеста. Короткое нажатие — событие при отпускании, длинное —
// событие в момент, когда удержание достигло порога, без ожидания отпускания:
// STOP должен сработать, пока палец ещё на кнопке, иначе пользователь не
// поймёт, засчитано ли. Отпускание после длинного нажатия ничего не даёт.
//
// Чистая логика без Arduino, проверяется на хосте: host_tests/.
#pragma once

#include <stdint.h>

class ButtonDecoder {
 public:
  enum Event { NONE, SHORT, LONG };

  ButtonDecoder(uint32_t debounceMs, uint32_t longPressMs)
      : m_debounce(debounceMs), m_longPress(longPressMs) {}

  // pressed — уровень кнопки уже в логическом смысле (true = нажата).
  Event feed(bool pressed, uint32_t nowMs) {
    if (pressed != m_lastRaw) {
      m_lastRaw = pressed;
      m_changedAt = nowMs;
    }
    if (nowMs - m_changedAt >= m_debounce && pressed != m_stable) {
      m_stable = pressed;
      if (pressed) {
        // Отсчёт удержания — от самого фронта, а не от конца антидребезга.
        m_pressedAt = m_changedAt;
        m_longFired = false;
        return NONE;
      }
      return m_longFired ? NONE : SHORT;
    }
    if (m_stable && !m_longFired && nowMs - m_pressedAt >= m_longPress) {
      m_longFired = true;
      return LONG;
    }
    return NONE;
  }

  bool held() const { return m_stable; }

 private:
  uint32_t m_debounce;
  uint32_t m_longPress;
  bool m_lastRaw = false;
  bool m_stable = false;
  bool m_longFired = false;
  uint32_t m_changedAt = 0;
  uint32_t m_pressedAt = 0;
};
