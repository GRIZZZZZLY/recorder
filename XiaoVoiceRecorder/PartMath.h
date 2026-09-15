// Арифметика частей: границы по байтам, а не по часам. Часть заканчивается
// ровно на PART_MAX_BYTES, поэтому стык двух частей не теряет ни одного
// сэмпла, а смещение метки считается точно, включая предзапись.
//
// Позиция (pos) — байты PCM с начала сессии, где ноль — самый старый байт
// предзаписи, попавший в первую часть. Без Arduino, проверяется на хосте.
#pragma once

#include <stdint.h>
#include <stdio.h>

namespace partmath {

constexpr uint32_t SAMPLE_RATE = 16000;
constexpr uint32_t FRAME_BYTES = 2;  // 16 бит, моно
constexpr uint32_t BYTES_PER_SECOND = SAMPLE_RATE * FRAME_BYTES;
constexpr uint32_t PART_SECONDS = 10 * 60;
constexpr uint32_t PART_MAX_BYTES = BYTES_PER_SECOND * PART_SECONDS;  // 19 200 000
constexpr uint16_t MAX_PARTS = 99;

// Номер части (с единицы), в которую попадает байт с этой позицией.
inline uint16_t partForPosition(uint32_t pos) {
  return static_cast<uint16_t>(pos / PART_MAX_BYTES) + 1;
}

// Смещение внутри своей части, байт.
inline uint32_t offsetInPart(uint32_t pos) { return pos % PART_MAX_BYTES; }

// Сколько ещё влезет в часть, если в ней уже written байт.
inline uint32_t roomInPart(uint32_t written) {
  return written >= PART_MAX_BYTES ? 0 : PART_MAX_BYTES - written;
}

inline uint32_t wholeFrames(uint32_t bytes) { return bytes - bytes % FRAME_BYTES; }

inline double seconds(uint32_t bytes) {
  return static_cast<double>(bytes) / static_cast<double>(BYTES_PER_SECOND);
}

// Строка метки для .MRK и X-Marks: секунды с двумя знаками, точка.
inline int formatMark(char *out, size_t size, uint32_t offsetBytes) {
  const uint32_t whole = offsetBytes / BYTES_PER_SECOND;
  const uint32_t rem = offsetBytes % BYTES_PER_SECOND;
  const uint32_t hundredths = (rem * 100 + BYTES_PER_SECOND / 2) / BYTES_PER_SECOND;
  // Округление может дать 100 сотых: переносим в целые.
  return snprintf(out, size, "%lu.%02lu",
                  static_cast<unsigned long>(whole + hundredths / 100),
                  static_cast<unsigned long>(hundredths % 100));
}

// Байты PCM, влезающие в WAV: заголовок 44 байта, размер данных 32-битный.
constexpr uint32_t WAV_HEADER_BYTES = 44;

}  // namespace partmath
