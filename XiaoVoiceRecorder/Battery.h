// Заряд аккумулятора: чистая арифметика без Arduino. Само чтение АЦП живёт в
// XiaoVoiceRecorder.ino (readBattery), потому что там есть analogReadMilliVolts.
//
// На XIAO ESP32-S3 Sense измерять нечем: у штатной платы нет ни делителя, ни
// газометра, и статус честно говорит supported=false. На Recorder PCB RevA
// BAT+ идёт через делитель на GPIO1 — здесь напряжение восстанавливается по
// коэффициенту делителя и переводится в проценты.
//
// Процент — первая оценка по типовой кривой разряда Li-Po, а не измерение
// именно нашего аккумулятора. Точки таблицы заменяются измеренными на RevA,
// когда будет чем измерять; до этого X-Battery — ориентир, не показание.
#pragma once

#include <stdint.h>

namespace battery {

struct Status {
  bool supported = false;   // на этой плате есть чем измерить
  uint16_t millivolts = 0;  // напряжение на BAT+, мВ; 0 если не поддерживается
  uint8_t percent = 0;      // 0..100 по кривой ниже; смысл только при supported
};

// Напряжение на BAT+ по напряжению на входе АЦП и коэффициенту делителя.
constexpr uint32_t packMillivolts(uint32_t adcMillivolts, float divider) {
  return static_cast<uint32_t>(adcMillivolts * divider + 0.5f);
}

// Кусочно-линейная кривая разряда одноячеечного Li-Po под небольшой нагрузкой.
// Линейная формула 3,3..4,2 В врала бы на плоском участке 3,7..3,9 В, где
// аккумулятор проводит большую часть жизни.
inline uint8_t percentFromMillivolts(uint32_t mv) {
  struct Point { uint16_t mv; uint8_t pct; };
  // ponytail: типовая кривая; заменить точками с амперметра на RevA.
  static const Point curve[] = {
      {4200, 100}, {4100, 90}, {4000, 78}, {3900, 62}, {3800, 45},
      {3750, 33},  {3700, 20}, {3600, 8},  {3500, 3},  {3300, 0},
  };
  const int n = static_cast<int>(sizeof(curve) / sizeof(curve[0]));
  if (mv >= curve[0].mv) return 100;
  if (mv <= curve[n - 1].mv) return 0;
  for (int i = 1; i < n; ++i) {
    if (mv >= curve[i].mv) {
      const Point &hi = curve[i - 1];
      const Point &lo = curve[i];
      const uint32_t span = hi.mv - lo.mv;
      const uint32_t up = mv - lo.mv;
      return static_cast<uint8_t>(lo.pct + ((hi.pct - lo.pct) * up + span / 2) / span);
    }
  }
  return 0;
}

}  // namespace battery
