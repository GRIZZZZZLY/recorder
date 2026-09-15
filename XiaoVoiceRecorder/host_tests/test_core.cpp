// Хост-тесты чистой логики прошивки. Без Arduino, без платы.
// Запуск: bash tools/host_tests.sh   (нужен g++; на Windows без него —
// tools/host_tests.sh --remote запускает на VPS).
//
// Это проверка алгоритмов, а не железа: DMA микрофона, скорость SD,
// одновременная запись с Wi-Fi здесь не проверяются и проверяться не могут.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../Button.h"
#include "../PartMath.h"
#include "../RingBuffer.h"
#include "../UploadPolicy.h"

static int g_failed = 0;
static int g_checked = 0;

#define CHECK(cond)                                                         \
  do {                                                                      \
    ++g_checked;                                                            \
    if (!(cond)) {                                                          \
      ++g_failed;                                                           \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);       \
    }                                                                       \
  } while (0)

// --- RingBuffer --------------------------------------------------------------

static void fill(uint8_t *buf, uint32_t n, uint32_t start) {
  for (uint32_t i = 0; i < n; ++i) buf[i] = static_cast<uint8_t>(start + i);
}

static bool sequential(const uint8_t *buf, uint32_t n, uint32_t start) {
  for (uint32_t i = 0; i < n; ++i) {
    if (buf[i] != static_cast<uint8_t>(start + i)) return false;
  }
  return true;
}

static void test_ring_requires_power_of_two() {
  uint8_t storage[64];
  RingBuffer ring;
  CHECK(!ring.init(storage, 60));
  CHECK(!ring.init(nullptr, 64));
  CHECK(ring.init(storage, 64));
  CHECK(ring.capacity() == 64);
  CHECK(ring.available() == 0);
}

static void test_ring_push_pop_with_wrap() {
  uint8_t storage[64], in[40], out[64];
  RingBuffer ring;
  ring.init(storage, 64);
  fill(in, 40, 0);
  ring.push(in, 40);
  CHECK(ring.available() == 40);
  CHECK(ring.pop(out, 30) == 30);
  CHECK(sequential(out, 30, 0));
  fill(in, 40, 40);
  ring.push(in, 40);  // пишется через границу массива
  CHECK(ring.available() == 50);
  CHECK(ring.pop(out, 64) == 50);
  CHECK(sequential(out, 50, 30));
  CHECK(ring.dropped() == 0);
  CHECK(ring.highWatermark() == 50);
}

static void test_ring_armed_keeps_last_capacity_bytes() {
  // В ARMED никто не читает: буфер хранит последние 64 байта, resync ставит
  // читателя на самый старый уцелевший и НЕ считает затёртое потерей.
  uint8_t storage[64], in[100], out[64];
  RingBuffer ring;
  ring.init(storage, 64);
  fill(in, 100, 0);
  ring.push(in, 30);
  ring.push(in + 30, 70);
  CHECK(ring.available() == 64);
  ring.resync();
  CHECK(ring.dropped() == 0);
  CHECK(ring.pop(out, 64) == 64);
  CHECK(sequential(out, 64, 36));  // байты 36..99
  CHECK(ring.dropped() == 0);
  CHECK(ring.available() == 0);
}

static void test_ring_push_longer_than_capacity_keeps_tail() {
  uint8_t storage[16], in[40], out[16];
  RingBuffer ring;
  ring.init(storage, 16);
  fill(in, 40, 0);
  ring.push(in, 40);
  ring.resync();
  CHECK(ring.pop(out, 16) == 16);
  CHECK(sequential(out, 16, 24));
}

static void test_ring_counts_overrun_as_dropped_when_recording() {
  uint8_t storage[64], in[100], out[64];
  RingBuffer ring;
  ring.init(storage, 64);
  fill(in, 100, 0);
  ring.push(in, 10);
  CHECK(ring.pop(out, 10) == 10);  // читатель включился: RECORDING
  ring.push(in + 10, 90);          // SD «не успела»: 26 байт затёрто
  CHECK(ring.available() == 64);
  CHECK(ring.pop(out, 64) == 64);
  CHECK(ring.dropped() == 26);
  CHECK(sequential(out, 64, 36));
}

static void test_ring_survives_counter_wraparound() {
  // Счётчики 32-битные и переполняются через 4 ГБ. Имитируем: 2^32 - 5 байт
  // уже прошло, добавляем ещё — маска должна дать те же позиции.
  uint8_t storage[16], in[24], out[16];
  RingBuffer ring;
  ring.init(storage, 16);
  // Прокрутим счётчики почти до конца через большие push; каждый push
  // не больше ёмкости, поэтому для скорости двигаем их напрямую ─ у класса
  // нет такого API, значит эмулируем: 2^32 = 268435456 * 16, кратно ёмкости,
  // поэтому позиция после 2^32-16 байт равна нулю. Пушим 2^28 раз по 16.
  uint8_t block[16];
  fill(block, 16, 0);
  for (uint32_t i = 0; i < (1u << 28) - 1; ++i) ring.push(block, 16);
  CHECK(ring.head() == 0xFFFFFFF0u);
  ring.resync();
  ring.discardAll();
  fill(in, 24, 100);
  ring.push(in, 12);   // head -> 0xFFFFFFFC
  ring.push(in + 12, 12);  // head переполняется -> 0x00000008
  CHECK(ring.head() == 8u);
  CHECK(ring.available() == 16);
  ring.resync();
  CHECK(ring.pop(out, 16) == 16);
  CHECK(sequential(out, 16, 108));
}

// --- ButtonDecoder -------------------------------------------------------------

static void test_button_short_press_fires_on_release() {
  ButtonDecoder b(40, 2000);
  CHECK(b.feed(true, 0) == ButtonDecoder::NONE);
  CHECK(b.feed(true, 50) == ButtonDecoder::NONE);   // стало стабильно нажато
  CHECK(b.feed(true, 300) == ButtonDecoder::NONE);
  CHECK(b.feed(false, 400) == ButtonDecoder::NONE);  // антидребезг отпускания
  CHECK(b.feed(false, 450) == ButtonDecoder::SHORT);
  CHECK(b.feed(false, 5000) == ButtonDecoder::NONE);
}

static void test_button_long_press_fires_while_held_and_release_is_silent() {
  ButtonDecoder b(40, 2000);
  b.feed(true, 0);
  b.feed(true, 50);
  CHECK(b.feed(true, 1999) == ButtonDecoder::NONE);
  CHECK(b.feed(true, 2000) == ButtonDecoder::LONG);   // считаем от фронта, не от 50
  CHECK(b.feed(true, 3000) == ButtonDecoder::NONE);   // один раз
  b.feed(false, 3100);
  CHECK(b.feed(false, 3150) == ButtonDecoder::NONE);  // не SHORT
}

static void test_button_glitch_shorter_than_debounce_is_ignored() {
  ButtonDecoder b(40, 2000);
  b.feed(true, 0);
  CHECK(b.feed(true, 20) == ButtonDecoder::NONE);
  b.feed(false, 30);
  CHECK(b.feed(false, 100) == ButtonDecoder::NONE);
  CHECK(b.feed(false, 200) == ButtonDecoder::NONE);
}

// --- PartMath ---------------------------------------------------------------------

static void test_part_boundaries_are_exact_in_bytes() {
  using namespace partmath;
  CHECK(PART_MAX_BYTES == 19200000u);
  CHECK(partForPosition(0) == 1);
  CHECK(partForPosition(PART_MAX_BYTES - 2) == 1);
  CHECK(partForPosition(PART_MAX_BYTES) == 2);
  CHECK(offsetInPart(PART_MAX_BYTES + 32000) == 32000u);
  CHECK(roomInPart(0) == PART_MAX_BYTES);
  CHECK(roomInPart(PART_MAX_BYTES - 10) == 10u);
  CHECK(roomInPart(PART_MAX_BYTES + 10) == 0u);
  CHECK(wholeFrames(4097) == 4096u);
  // 99 частей укладываются в 32 бита позиции с запасом.
  CHECK(static_cast<uint64_t>(PART_MAX_BYTES) * MAX_PARTS < 0xFFFFFFFFull);
}

static void test_mark_formatting_rounds_to_hundredths() {
  char buf[16];
  partmath::formatMark(buf, sizeof(buf), 0);
  CHECK(strcmp(buf, "0.00") == 0);
  partmath::formatMark(buf, sizeof(buf), 32000 * 12 + 16000);  // 12.5 с
  CHECK(strcmp(buf, "12.50") == 0);
  partmath::formatMark(buf, sizeof(buf), 32000 * 301 + 6400);  // 301.2 с
  CHECK(strcmp(buf, "301.20") == 0);
  partmath::formatMark(buf, sizeof(buf), 32000 * 7 + 31999);   // 7.99997 -> 8.00
  CHECK(strcmp(buf, "8.00") == 0);
}

// --- UploadPolicy --------------------------------------------------------------------

static void test_status_classification() {
  using namespace uploadpolicy;
  CHECK(classifyStatus(201) == SEND_OK);
  CHECK(classifyStatus(200) == SEND_OK);
  CHECK(classifyStatus(401) == SEND_CONFIG_FATAL);
  CHECK(classifyStatus(409) == SEND_CONFLICT);
  CHECK(classifyStatus(400) == SEND_PART_FATAL);
  CHECK(classifyStatus(413) == SEND_PART_FATAL);
  CHECK(classifyStatus(422) == SEND_RETRY);
  CHECK(classifyStatus(429) == SEND_RETRY);
  CHECK(classifyStatus(500) == SEND_RETRY);
  CHECK(classifyStatus(503) == SEND_RETRY);
  CHECK(classifyStatus(-1) == SEND_RETRY);
  CHECK(classifyStatus(0) == SEND_RETRY);
}

static void test_backoff_schedule() {
  using namespace uploadpolicy;
  const uint32_t minute = 60000;
  CHECK(backoffMs(0) == 1 * minute);
  CHECK(backoffMs(1) == 1 * minute);
  CHECK(backoffMs(2) == 5 * minute);
  CHECK(backoffMs(3) == 15 * minute);
  CHECK(backoffMs(4) == 60 * minute);
  CHECK(backoffMs(5) == 60 * minute);
  CHECK(backoffMs(1000) == 60 * minute);
}

static void test_part_and_end_names() {
  using namespace uploadpolicy;
  uint16_t s = 0, p = 0;
  bool r = true;
  CHECK(parsePartName("REC0007_P01.WAV", &s, &p, &r) && s == 7 && p == 1 && !r);
  CHECK(parsePartName("REC0123_P45_R.WAV", &s, &p, &r) && s == 123 && p == 45 && r);
  CHECK(!parsePartName("REC0007_P01.TMP", &s, &p, &r));
  CHECK(!parsePartName("REC0007_P01.SENT", &s, &p, &r));
  CHECK(!parsePartName("REC0007_P01.CONFLICT", &s, &p, &r));
  CHECK(!parsePartName("REC0000_P01.WAV", &s, &p, &r));
  CHECK(!parsePartName("REC0007_P00.WAV", &s, &p, &r));
  CHECK(!parsePartName("REC0007_N08.END", &s, &p, &r));
  CHECK(!parsePartName("RECabcd_P01.WAV", &s, &p, &r));
  CHECK(parseEndName("REC0007_N08.END", &s, &p) && s == 7 && p == 8);
  CHECK(!parseEndName("REC0007_N00.END", &s, &p));
  CHECK(!parseEndName("REC0007_P08.END", &s, &p));
  CHECK(!parseEndName("REC0007_P01.WAV", &s, &p));
}

static void test_server_url_parsing() {
  using namespace uploadpolicy;
  char host[64], prefix[64];
  uint16_t port = 0;
  CHECK(parseServerUrl("https://esp-recorder.duckdns.org:8443", host, 64, &port, prefix, 64));
  CHECK(strcmp(host, "esp-recorder.duckdns.org") == 0 && port == 8443 && prefix[0] == '\0');
  CHECK(parseServerUrl("https://example.org/", host, 64, &port, prefix, 64));
  CHECK(port == 443 && prefix[0] == '\0');
  CHECK(parseServerUrl("https://example.org/rec/", host, 64, &port, prefix, 64));
  CHECK(strcmp(prefix, "/rec") == 0);
  CHECK(!parseServerUrl("http://example.org", host, 64, &port, prefix, 64));
  CHECK(!parseServerUrl("https://:8443", host, 64, &port, prefix, 64));
  CHECK(!parseServerUrl("https://example.org:abc", host, 64, &port, prefix, 64));
  CHECK(!parseServerUrl("https://example.org:0", host, 64, &port, prefix, 64));
  CHECK(!parseServerUrl("", host, 64, &port, prefix, 64));
}

static void test_marks_header_from_file_text() {
  using namespace uploadpolicy;
  char out[128];
  CHECK(marksHeader("12.50\n301.20\n407.73\n", out, sizeof(out)) == 3);
  CHECK(strcmp(out, "12.50,301.20,407.73") == 0);
  // Обрывок последней строки после потери питания и мусор отбрасываются.
  CHECK(marksHeader("12.50\r\n\n30\n12.\nabc\n4.5.6\n", out, sizeof(out)) == 2);
  CHECK(strcmp(out, "12.50,30") == 0);
  CHECK(marksHeader("", out, sizeof(out)) == 0);
  CHECK(out[0] == '\0');
  // Не больше 200 меток: предел сервера.
  char many[8 * 260];
  many[0] = '\0';
  for (int i = 0; i < 250; ++i) strcat(many, "1.00\n");
  char big[8 * 260];
  CHECK(marksHeader(many, big, sizeof(big)) == 200);
  // Тесный буфер: заголовок обрезается по целой метке, не по середине.
  CHECK(marksHeader("1.00\n2.00\n3.00\n", out, 10) == 2);
  CHECK(strcmp(out, "1.00,2.00") == 0);
}

static void test_cleanup_touches_only_sent() {
  using namespace uploadpolicy;
  CHECK(isSentPart("REC0007_P01.SENT"));
  CHECK(!isSentPart("REC0007_P01.WAV"));
  CHECK(!isSentPart("REC0007_P01.TMP"));
  CHECK(!isSentPart("REC0007_P01.BAD"));
  CHECK(!isSentPart("REC0007_P01.CONFLICT"));
  CHECK(!isSentPart("REC0007_P01.REJECT"));
  CHECK(!isSentPart("REC0007_P01_R.SENT"));  // длина другая: восстановленная
  CHECK(!isSentPart("REC0007_N08.END"));
  char mrk[32];
  marksNameFor("REC0007_P01.SENT", mrk, sizeof(mrk));
  CHECK(strcmp(mrk, "REC0007_P01.MRK") == 0);
  marksNameFor("REC0007_P01_R.WAV", mrk, sizeof(mrk));
  CHECK(strcmp(mrk, "REC0007_P01_R.MRK") == 0);
}

int main() {
  test_ring_requires_power_of_two();
  test_ring_push_pop_with_wrap();
  test_ring_armed_keeps_last_capacity_bytes();
  test_ring_push_longer_than_capacity_keeps_tail();
  test_ring_counts_overrun_as_dropped_when_recording();
  test_ring_survives_counter_wraparound();
  test_button_short_press_fires_on_release();
  test_button_long_press_fires_while_held_and_release_is_silent();
  test_button_glitch_shorter_than_debounce_is_ignored();
  test_part_boundaries_are_exact_in_bytes();
  test_mark_formatting_rounds_to_hundredths();
  test_status_classification();
  test_backoff_schedule();
  test_part_and_end_names();
  test_server_url_parsing();
  test_marks_header_from_file_text();
  test_cleanup_touches_only_sent();

  printf("host tests: %d checks, %d failed\n", g_checked, g_failed);
  return g_failed == 0 ? 0 : 1;
}
