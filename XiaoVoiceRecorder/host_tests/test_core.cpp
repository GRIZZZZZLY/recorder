// Хост-тесты чистой логики прошивки. Без Arduino, без платы.
// Запуск: bash tools/host_tests.sh   (нужен g++; на Windows без него —
// tools/host_tests.sh --remote запускает на VPS).
//
// Это проверка алгоритмов, а не железа: DMA микрофона, скорость SD,
// одновременная запись с Wi-Fi здесь не проверяются и проверяться не могут.
#define RING_BUFFER_TESTING
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../AudioFormat.h"
#include "../Button.h"
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


static void test_ring_resync_leaves_headroom_so_first_frames_are_not_dropped() {
  // Полный буфер при START: оставляем 48 из 64, следующие 16 байт микрофона
  // ложатся в запас, а не затирают начало предзаписи.
  uint8_t storage[64], in[100], out[64];
  RingBuffer ring;
  ring.init(storage, 64);
  fill(in, 100, 0);
  ring.push(in, 80);
  ring.resync(48);
  CHECK(ring.available() == 48);
  ring.push(in + 80, 16);
  CHECK(ring.pop(out, 64) == 64);
  CHECK(ring.dropped() == 0);
  CHECK(sequential(out, 64, 32));  // байты 32..95
}


static void test_ring_survives_counter_wraparound() {
  // Счётчики 32-битные и переполняются через 4 ГБ звука. Ставим их к краю и
  // проверяем, что маска даёт те же позиции, а разность head-tail не ломается.
  uint8_t storage[16], in[24], out[16];
  RingBuffer ring;
  ring.init(storage, 16);
  ring.testSetCounters(0xFFFFFFF0u, 0xFFFFFFF0u);
  fill(in, 24, 100);
  ring.push(in, 12);       // head -> 0xFFFFFFFC
  ring.push(in + 12, 12);  // head переполняется -> 0x00000008
  CHECK(ring.head() == 8u);
  CHECK(ring.available() == 16);
  ring.resync();
  CHECK(ring.pop(out, 16) == 16);
  CHECK(sequential(out, 16, 108));
  CHECK(ring.available() == 0);
  CHECK(ring.dropped() == 0);
}

// --- ButtonDecoder -------------------------------------------------------------


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


static void test_edges_captured_while_the_loop_was_blocked_still_decode() {
  // Так их проигрывает handleButton(): фронт с его настоящим временем, затем
  // тот же уровень через антидребезг. Нажатие, случившееся целиком внутри
  // TLS-рукопожатия, не должно ни потеряться, ни стать длинным.
  ButtonDecoder b(40, 2000);
  const uint32_t pressAt = 1000, releaseAt = 1300, now = 3000;  // loop проснулся в 3000
  CHECK(b.feed(true, pressAt) == ButtonDecoder::NONE);
  CHECK(b.feed(true, pressAt + 40) == ButtonDecoder::NONE);
  CHECK(b.feed(false, releaseAt) == ButtonDecoder::NONE);
  CHECK(b.feed(false, releaseAt + 40) == ButtonDecoder::SHORT);
  CHECK(b.feed(false, now) == ButtonDecoder::NONE);  // опрос после разбора очереди

  // Удержание, начавшееся во время блокировки: длинное считается от фронта,
  // поэтому срабатывает сразу, как только цикл освободился.
  ButtonDecoder h(40, 2000);
  CHECK(h.feed(true, 1000) == ButtonDecoder::NONE);
  CHECK(h.feed(true, 1040) == ButtonDecoder::NONE);
  CHECK(h.feed(true, 3000) == ButtonDecoder::LONG);
  CHECK(h.feed(true, 3100) == ButtonDecoder::NONE);
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
  CHECK(classifyStatus(416) == SEND_RETRY);  // докачка: спросить место заново
}

static void test_offset_reply_parsing() {
  using namespace uploadpolicy;
  uint32_t off = 7;
  CHECK(parseOffsetReply("{\"offset\":0}", 100, &off) && off == 0);
  CHECK(parseOffsetReply("{\"offset\":5238784}", 19200044, &off) && off == 5238784);
  CHECK(parseOffsetReply("{\"offset\": 100}", 100, &off) && off == 100);
  off = 7;
  CHECK(!parseOffsetReply("{\"offset\":101}", 100, &off) && off == 7);  // больше файла
  CHECK(!parseOffsetReply("{\"offset\":99999999999999999999}", 100, &off));
  CHECK(!parseOffsetReply("{\"status\":\"stored\"}", 100, &off));
  CHECK(!parseOffsetReply("{\"offset\":-1}", 100, &off));
  CHECK(!parseOffsetReply("", 100, &off));
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


int main() {
  test_ring_armed_keeps_last_capacity_bytes();
  test_ring_resync_leaves_headroom_so_first_frames_are_not_dropped();
  test_ring_survives_counter_wraparound();
  test_button_long_press_fires_while_held_and_release_is_silent();
  test_edges_captured_while_the_loop_was_blocked_still_decode();
  test_status_classification();
  test_offset_reply_parsing();
  test_part_and_end_names();

  printf("host tests: %d checks, %d failed\n", g_checked, g_failed);
  return g_failed == 0 ? 0 : 1;
}
