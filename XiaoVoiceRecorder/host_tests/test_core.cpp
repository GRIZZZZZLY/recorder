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
#include "../Battery.h"
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

// --- AudioFormat --------------------------------------------------------------------

static void test_mono_format_matches_the_sense_prototype() {
  // XIAO Sense: 16 кГц, 16 бит, один канал. Эти числа — контракт с сервером
  // (db.py считает длительность по 32 000 байт в секунду) и с документацией.
  using namespace audio;
  constexpr Format f = MONO_16K;
  CHECK(f.frameBytes() == 2u);
  CHECK(f.bytesPerSecond() == 32000u);
  CHECK(f.partMaxBytes() == 19200000u);
  CHECK(f.wholeFrames(4097) == 4096u);
  CHECK(f.frames(4096) == 2048u);
  CHECK(f.ringBytesFor(30) == 1024u * 1024u);  // 960 000 -> 1 МиБ = 32,8 с
  CHECK(f.seconds(1024u * 1024u) > 32.7 && f.seconds(1024u * 1024u) < 32.9);
  // 99 частей укладываются в 32 бита позиции с запасом.
  CHECK(static_cast<uint64_t>(f.partMaxBytes()) * MAX_PARTS < 0xFFFFFFFFull);
}

static void test_stereo_format_doubles_every_byte_count() {
  // Recorder RevA: два IM72D128V, кадр = L + R = 4 байта. Всё, что считается
  // в байтах, удваивается; всё, что в кадрах и секундах, — нет.
  using namespace audio;
  constexpr Format f = STEREO_16K;
  CHECK(f.frameBytes() == 4u);
  CHECK(f.bytesPerSecond() == 64000u);
  CHECK(f.partMaxBytes() == 38400000u);
  CHECK(f.wholeFrames(4099) == 4096u);
  CHECK(f.wholeFrames(4098) == 4096u);   // два лишних байта — полкадра, отбрасываются
  CHECK(f.frames(4096) == 1024u);
  CHECK(f.ringBytesFor(30) == 2048u * 1024u);  // 1 920 000 -> 2 МиБ = 32,8 с
  CHECK(f.seconds(2048u * 1024u) > 32.7 && f.seconds(2048u * 1024u) < 32.9);
  CHECK(f.seconds(f.partMaxBytes()) == 600.0);
  CHECK(static_cast<uint64_t>(f.partMaxBytes()) * MAX_PARTS < 0xFFFFFFFFull);
  CHECK(f != MONO_16K);
}

static void test_mark_formatting_rounds_to_hundredths() {
  using namespace audio;
  char buf[16];
  MONO_16K.formatMark(buf, sizeof(buf), 0);
  CHECK(strcmp(buf, "0.00") == 0);
  MONO_16K.formatMark(buf, sizeof(buf), 32000 * 12 + 16000);  // 12.5 с
  CHECK(strcmp(buf, "12.50") == 0);
  MONO_16K.formatMark(buf, sizeof(buf), 32000 * 301 + 6400);  // 301.2 с
  CHECK(strcmp(buf, "301.20") == 0);
  MONO_16K.formatMark(buf, sizeof(buf), 32000 * 7 + 31999);   // 7.99997 -> 8.00
  CHECK(strcmp(buf, "8.00") == 0);
  // Те же секунды в стерео — вдвое больше байт. Метка обязана остаться в секундах.
  STEREO_16K.formatMark(buf, sizeof(buf), 64000 * 12 + 32000);
  CHECK(strcmp(buf, "12.50") == 0);
  STEREO_16K.formatMark(buf, sizeof(buf), 32000 * 12 + 16000);  // моно-байты в стерео — ошибка вдвое
  CHECK(strcmp(buf, "6.25") == 0);
}

static void checkWavHeader(const uint8_t *h, const audio::Format &f, uint32_t pcm) {
  using namespace audio;
  CHECK(memcmp(h, "RIFF", 4) == 0);
  CHECK(getLe32(h + 4) == 36 + pcm);           // размер RIFF без первых 8 байт
  CHECK(memcmp(h + 8, "WAVE", 4) == 0);
  CHECK(memcmp(h + 12, "fmt ", 4) == 0);
  CHECK(getLe32(h + 16) == 16);                // длина fmt
  CHECK(getLe16(h + 20) == 1);                 // PCM
  CHECK(getLe16(h + 22) == f.channels);
  CHECK(getLe32(h + 24) == f.sampleRate);
  CHECK(getLe32(h + 28) == f.bytesPerSecond());   // byte rate
  CHECK(getLe16(h + 32) == f.frameBytes());       // block align
  CHECK(getLe16(h + 34) == f.bitsPerSample);
  CHECK(memcmp(h + 36, "data", 4) == 0);
  CHECK(getLe32(h + 40) == pcm);
}

static void test_wav_header_describes_mono_and_stereo_correctly() {
  using namespace audio;
  uint8_t h[44];
  buildWavHeader(h, MONO_16K, 19200000u);
  checkWavHeader(h, MONO_16K, 19200000u);
  CHECK(getLe16(h + 22) == 1 && getLe32(h + 28) == 32000u && getLe16(h + 32) == 2);

  buildWavHeader(h, STEREO_16K, 38400000u);
  checkWavHeader(h, STEREO_16K, 38400000u);
  CHECK(getLe16(h + 22) == 2 && getLe32(h + 28) == 64000u && getLe16(h + 32) == 4);

  // Пустая часть: только заголовок, размер данных ноль — так открывается .TMP.
  buildWavHeader(h, STEREO_16K, 0);
  checkWavHeader(h, STEREO_16K, 0);
}

static void test_wav_header_parses_back_with_its_own_format() {
  using namespace audio;
  uint8_t h[44];
  Format f{0, 0, 0};
  uint32_t pcm = 0;

  buildWavHeader(h, MONO_16K, 12345);
  CHECK(parseWavHeader(h, &f, &pcm) && f == MONO_16K && pcm == 12345u);
  buildWavHeader(h, STEREO_16K, 4444);
  CHECK(parseWavHeader(h, &f, &pcm) && f == STEREO_16K && pcm == 4444u);

  // Не наше: чужая сигнатура, 8 бит, несогласованный byte rate, три канала.
  buildWavHeader(h, MONO_16K, 1);
  h[0] = 'X';
  CHECK(!parseWavHeader(h, &f, &pcm));
  buildWavHeader(h, MONO_16K, 1);
  putLe16(h + 34, 8);
  CHECK(!parseWavHeader(h, &f, &pcm));
  buildWavHeader(h, STEREO_16K, 1);
  putLe32(h + 28, 32000);  // byte rate моно при двух каналах
  CHECK(!parseWavHeader(h, &f, &pcm));
  buildWavHeader(h, STEREO_16K, 1);
  putLe16(h + 22, 3);
  CHECK(!parseWavHeader(h, &f, &pcm));
  buildWavHeader(h, MONO_16K, 1);
  putLe16(h + 20, 3);  // не PCM
  CHECK(!parseWavHeader(h, &f, &pcm));
}

// --- Battery -------------------------------------------------------------------------

static void test_battery_voltage_is_restored_through_the_divider() {
  using namespace battery;
  CHECK(packMillivolts(2100, 2.0f) == 4200u);
  CHECK(packMillivolts(1850, 2.0f) == 3700u);
  CHECK(packMillivolts(0, 2.0f) == 0u);
}

static void test_battery_percent_follows_a_lipo_curve_not_a_line() {
  using namespace battery;
  CHECK(percentFromMillivolts(4300) == 100);
  CHECK(percentFromMillivolts(4200) == 100);
  CHECK(percentFromMillivolts(3300) == 0);
  CHECK(percentFromMillivolts(3000) == 0);
  CHECK(percentFromMillivolts(4050) == 84);   // между 4000 (78) и 4100 (90)
  CHECK(percentFromMillivolts(3700) == 20);
  // Линейная формула 3,3..4,2 дала бы на 3,8 В около 56 %; кривая честнее.
  CHECK(percentFromMillivolts(3800) == 45);
  // Монотонность по всей шкале: разряд не может «подрастать».
  uint8_t prev = 0;
  bool monotonic = true;
  for (uint32_t mv = 3000; mv <= 4300; mv += 10) {
    const uint8_t p = percentFromMillivolts(mv);
    if (p < prev) monotonic = false;
    prev = p;
  }
  CHECK(monotonic);
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
  CHECK(isSentPart("REC0007_P01_R.SENT"));   // восстановленная тоже подтверждена
  CHECK(!isSentPart("REC0007_P01_X.SENT"));
  CHECK(!isSentPart("REC0007_N08.SENT"));
  CHECK(!isSentPart("REC0007_N08.END"));
  char mrk[32];
  marksNameFor("REC0007_P01.SENT", mrk, sizeof(mrk));
  CHECK(strcmp(mrk, "REC0007_P01.MRK") == 0);
  // Метки пишутся до сбоя как P01.MRK; восстановленная _R-часть ищет их же.
  marksNameFor("REC0007_P01_R.WAV", mrk, sizeof(mrk));
  CHECK(strcmp(mrk, "REC0007_P01.MRK") == 0);
  marksNameFor("REC0007_P01_R.SENT", mrk, sizeof(mrk));
  CHECK(strcmp(mrk, "REC0007_P01.MRK") == 0);
  marksNameFor("REC0007_P01.WAV", mrk, sizeof(mrk));
  CHECK(strcmp(mrk, "REC0007_P01.MRK") == 0);
}

int main() {
  test_ring_requires_power_of_two();
  test_ring_push_pop_with_wrap();
  test_ring_armed_keeps_last_capacity_bytes();
  test_ring_push_longer_than_capacity_keeps_tail();
  test_ring_resync_leaves_headroom_so_first_frames_are_not_dropped();
  test_ring_counts_overrun_as_dropped_when_recording();
  test_ring_survives_counter_wraparound();
  test_button_short_press_fires_on_release();
  test_button_long_press_fires_while_held_and_release_is_silent();
  test_button_glitch_shorter_than_debounce_is_ignored();
  test_edges_captured_while_the_loop_was_blocked_still_decode();
  test_mono_format_matches_the_sense_prototype();
  test_stereo_format_doubles_every_byte_count();
  test_mark_formatting_rounds_to_hundredths();
  test_wav_header_describes_mono_and_stereo_correctly();
  test_wav_header_parses_back_with_its_own_format();
  test_battery_voltage_is_restored_through_the_divider();
  test_battery_percent_follows_a_lipo_curve_not_a_line();
  test_status_classification();
  test_offset_reply_parsing();
  test_backoff_schedule();
  test_part_and_end_names();
  test_server_url_parsing();
  test_marks_header_from_file_text();
  test_cleanup_touches_only_sent();

  printf("host tests: %d checks, %d failed\n", g_checked, g_failed);
  return g_failed == 0 ? 0 : 1;
}
