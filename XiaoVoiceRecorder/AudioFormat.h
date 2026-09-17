// Формат PCM и арифметика частей — единственный источник истины для частоты,
// числа каналов и разрядности. Из них выводятся байты на кадр, байты в секунду,
// размер части и заголовок WAV. Плата выбирает свой формат в BoardConfig.h;
// логика записи (Session.h) получает его значением и о плате не знает.
//
// Терминология: кадр (frame) — один отсчёт по всем каналам сразу. В моно кадр
// равен сэмплу, в стерео это два сэмпла по два байта. Все счётчики прошивки
// (captured_frames, written_frames, dropped_frames) считают кадры, поэтому
// одинаковы по смыслу на любой плате.
//
// Позиция (pos) — байты PCM с начала сессии, где ноль — самый старый байт
// предзаписи, попавший в первую часть. Часть заканчивается ровно на
// partMaxBytes(), поэтому стык не теряет ни одного кадра, а смещение метки
// считается точно. Без Arduino, проверяется на хосте.
#pragma once

#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace audio {

constexpr uint32_t PART_SECONDS = 10 * 60;
constexpr uint16_t MAX_PARTS = 99;
constexpr uint32_t WAV_HEADER_BYTES = 44;

struct Format {
  uint32_t sampleRate;
  uint16_t channels;
  uint16_t bitsPerSample;

  constexpr uint32_t frameBytes() const { return channels * (bitsPerSample / 8u); }
  constexpr uint32_t bytesPerSecond() const { return sampleRate * frameBytes(); }
  constexpr uint32_t partMaxBytes() const { return bytesPerSecond() * PART_SECONDS; }

  // Обрезка до целых кадров: на карту и в заголовок идут только целые кадры.
  constexpr uint32_t wholeFrames(uint32_t bytes) const { return bytes - bytes % frameBytes(); }
  constexpr uint32_t frames(uint32_t bytes) const { return bytes / frameBytes(); }

  double seconds(uint32_t bytes) const {
    return static_cast<double>(bytes) / static_cast<double>(bytesPerSecond());
  }

  // Ёмкость кольцевого буфера под seconds секунд, округлённая вверх до степени
  // двойки (RingBuffer работает маской). Моно 30 с -> 1 МиБ, стерео -> 2 МиБ.
  constexpr uint32_t ringBytesFor(uint32_t seconds) const {
    const uint32_t need = seconds * bytesPerSecond();
    uint32_t cap = 1;
    while (cap < need) cap <<= 1;
    return cap;
  }

  // Строка метки для .MRK и X-Marks: секунды с двумя знаками, точка.
  int formatMark(char *out, size_t size, uint32_t offsetBytes) const {
    const uint32_t bps = bytesPerSecond();
    const uint32_t whole = offsetBytes / bps;
    const uint32_t rem = offsetBytes % bps;
    const uint32_t hundredths = (rem * 100 + bps / 2) / bps;
    // Округление может дать 100 сотых: переносим в целые.
    return snprintf(out, size, "%lu.%02lu",
                    static_cast<unsigned long>(whole + hundredths / 100),
                    static_cast<unsigned long>(hundredths % 100));
  }

  constexpr bool operator==(const Format &o) const {
    return sampleRate == o.sampleRate && channels == o.channels && bitsPerSample == o.bitsPerSample;
  }
  constexpr bool operator!=(const Format &o) const { return !(*this == o); }
};

// XIAO ESP32-S3 Sense: встроенный PDM-микрофон.
constexpr Format MONO_16K{16000, 1, 16};
// Recorder PCB V1 RevA: два IM72D128V на общей линии DATA, слоты L/R.
constexpr Format STEREO_16K{16000, 2, 16};

// --- заголовок WAV ------------------------------------------------------------------

inline void putLe16(uint8_t *dst, uint16_t v) {
  dst[0] = v & 0xFF;
  dst[1] = (v >> 8) & 0xFF;
}

inline void putLe32(uint8_t *dst, uint32_t v) {
  dst[0] = v & 0xFF;
  dst[1] = (v >> 8) & 0xFF;
  dst[2] = (v >> 16) & 0xFF;
  dst[3] = (v >> 24) & 0xFF;
}

inline uint16_t getLe16(const uint8_t *s) { return static_cast<uint16_t>(s[0] | (s[1] << 8)); }

inline uint32_t getLe32(const uint8_t *s) {
  return static_cast<uint32_t>(s[0]) | (static_cast<uint32_t>(s[1]) << 8) |
         (static_cast<uint32_t>(s[2]) << 16) | (static_cast<uint32_t>(s[3]) << 24);
}

// Канонический 44-байтный заголовок PCM.
inline void buildWavHeader(uint8_t h[44], const Format &f, uint32_t pcmBytes) {
  memset(h, 0, 44);
  memcpy(h + 0, "RIFF", 4);
  putLe32(h + 4, 36 + pcmBytes);
  memcpy(h + 8, "WAVE", 4);
  memcpy(h + 12, "fmt ", 4);
  putLe32(h + 16, 16);
  putLe16(h + 20, 1);                                  // PCM
  putLe16(h + 22, f.channels);
  putLe32(h + 24, f.sampleRate);
  putLe32(h + 28, f.bytesPerSecond());                 // byte rate
  putLe16(h + 32, static_cast<uint16_t>(f.frameBytes()));  // block align
  putLe16(h + 34, f.bitsPerSample);
  memcpy(h + 36, "data", 4);
  putLe32(h + 40, pcmBytes);
}

// Разбор заголовка, каким его пишет эта прошивка: PCM 16 бит, 1 или 2 канала,
// поля согласованы между собой. Формат берётся из самого файла, а не из
// собранного профиля платы: карта из моно-прототипа восстанавливается и на
// стерео-плате. Возвращает false для всего, что мы не писали.
inline bool parseWavHeader(const uint8_t h[44], Format *fmt, uint32_t *pcmBytes) {
  if (memcmp(h, "RIFF", 4) || memcmp(h + 8, "WAVE", 4) || memcmp(h + 12, "fmt ", 4) ||
      memcmp(h + 36, "data", 4)) {
    return false;
  }
  if (getLe32(h + 16) != 16 || getLe16(h + 20) != 1 || getLe16(h + 34) != 16) return false;
  const Format f{getLe32(h + 24), getLe16(h + 22), 16};
  if (f.channels < 1 || f.channels > 2 || f.sampleRate == 0) return false;
  if (getLe16(h + 32) != f.frameBytes() || getLe32(h + 28) != f.bytesPerSecond()) return false;
  *fmt = f;
  *pcmBytes = getLe32(h + 40);
  return true;
}

}  // namespace audio
