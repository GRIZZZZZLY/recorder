// Типы, которые встречаются в сигнатурах функций .ino. Arduino вставляет
// автоматические прототипы в начало объединённого файла, поэтому такие типы
// должны быть объявлены в заголовке, а не в самом .ino.
#pragma once

#include <stdint.h>

enum DeviceState { ST_ARMED, ST_RECORDING, ST_FAULT };
enum StopReason { STOP_NORMAL, STOP_WRITE_ERROR };

namespace fb {
constexpr uint8_t MAX_STEPS = 8;

// Один канал индикации: LED или ключ вибромотора.
struct Channel {
  int pin = -1;
  // Последовательность длительностей: вкл, выкл, вкл, ... (мс). 0 — конец.
  uint16_t steps[MAX_STEPS] = {0};
  uint8_t index = MAX_STEPS;   // MAX_STEPS = ничего не играет
  uint32_t stepStartedMs = 0;
  bool level = false;
};
}  // namespace fb
