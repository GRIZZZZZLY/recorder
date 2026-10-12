// CRC-32 zlib (полином 0xEDB88320) без Arduino: тот же, что esp_rom_crc32_le
// и X-Crc32 протокола, но проверяется на хосте.
//
//   update(crc, p, n)        продолжает crc на n байт; update(0, ...) — с нуля
//   combine(c1, c2, len2)    CRC склейки A+B по CRC(A), CRC(B) и длине B —
//                            так заголовок WAV, записанный последним,
//                            присоединяется к CRC звука, посчитанному раньше
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace crc32 {

struct Table {
  uint32_t v[256];
  constexpr Table() : v() {
    for (uint32_t i = 0; i < 256; ++i) {
      uint32_t c = i;
      for (int k = 0; k < 8; ++k) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
      v[i] = c;
    }
  }
};
inline constexpr Table TABLE{};

inline uint32_t update(uint32_t crc, const uint8_t *p, size_t n) {
  crc = ~crc;
  while (n--) crc = TABLE.v[(crc ^ *p++) & 0xFF] ^ (crc >> 8);
  return ~crc;
}

namespace detail {
inline uint32_t gf2Times(const uint32_t *mat, uint32_t vec) {
  uint32_t sum = 0;
  for (; vec; vec >>= 1, ++mat) {
    if (vec & 1) sum ^= *mat;
  }
  return sum;
}
inline void gf2Square(uint32_t *square, const uint32_t *mat) {
  for (int n = 0; n < 32; ++n) square[n] = gf2Times(mat, mat[n]);
}
}  // namespace detail

// Как crc32_combine из zlib: сдвиг crc1 на len2 нулевых байт возведением
// оператора «один нулевой бит» в квадрат, O(log len2).
inline uint32_t combine(uint32_t crc1, uint32_t crc2, uint32_t len2) {
  if (len2 == 0) return crc1;
  uint32_t even[32], odd[32];
  odd[0] = 0xEDB88320u;
  for (int n = 1; n < 32; ++n) odd[n] = 1u << (n - 1);
  detail::gf2Square(even, odd);  // два нулевых бита
  detail::gf2Square(odd, even);  // четыре
  do {
    detail::gf2Square(even, odd);
    if (len2 & 1) crc1 = detail::gf2Times(even, crc1);
    len2 >>= 1;
    if (len2 == 0) break;
    detail::gf2Square(odd, even);
    if (len2 & 1) crc1 = detail::gf2Times(odd, crc1);
    len2 >>= 1;
  } while (len2 != 0);
  return crc1 ^ crc2;
}

}  // namespace crc32
