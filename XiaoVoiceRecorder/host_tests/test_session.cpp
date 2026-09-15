// Хост-тесты жизненного цикла записи: START с предзаписью, ротация частей,
// метки, STOP с .END, восстановление после сбоя, очистка карты.
//
// Хранилище — в памяти, с отказами по заказу. Это проверка ЛОГИКИ обращения с
// файлами, а не карты: скорость SD, FAT и питание здесь не участвуют.
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#include "../Session.h"
#include "FakeStorage.h"

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

static uint32_t g_now = 1000;
static uint32_t fakeNow() { return g_now; }
static std::vector<std::string> g_log;
static void fakeLog(const char *line) { g_log.push_back(line); }

struct Rig {
  FakeStorage fs;
  uint8_t storage[1 << 16];  // 64 КиБ = 2 с звука
  RingBuffer ring;
  session::Writer writer;
  Rig() : writer(fs, ring, fakeNow, fakeLog) {
    ring.init(storage, sizeof(storage));
    g_log.clear();
    fs.total = 1ull << 40;
  }
  // Микрофон: n байт по возрастанию, чтобы потом сверить содержимое.
  void capture(uint32_t n, uint8_t start = 0) {
    std::vector<uint8_t> chunk(n);
    for (uint32_t i = 0; i < n; ++i) chunk[i] = static_cast<uint8_t>(start + i);
    ring.push(chunk.data(), n);
  }
  // Гоняем service, пока буфер не опустеет ниже куска или пока не ошибка.
  bool drain() {
    for (int i = 0; i < 100000 && ring.available() >= writer.cfg.writeChunkBytes; ++i) {
      if (!writer.service()) return false;
      if (writer.partLimitReached()) return true;
    }
    return true;
  }
};

static uint32_t pcmBytesOf(const FakeStorage &fs, const std::string &path) {
  const std::vector<uint8_t> &d = fs.files.at(path);
  return session::getLe32(d.data() + 40);
}

static bool logHas(const char *needle) {
  for (const std::string &line : g_log) {
    if (line.find(needle) != std::string::npos) return true;
  }
  return false;
}

// --- START / предзапись / STOP -----------------------------------------------------

static void test_start_includes_preroll_and_stop_writes_end() {
  Rig r;
  r.capture(10000);  // 10 000 байт лежали в буфере ДО нажатия
  CHECK(r.writer.start(7));
  CHECK(r.writer.preRollBytes() == 10000);
  CHECK(r.fs.has("/REC0007_P01.TMP"));
  CHECK(r.fs.sizeOf("/REC0007_P01.TMP") == 44);  // только заголовок, данные ещё в буфере

  r.capture(6000, 10000 & 0xFF);
  CHECK(r.drain());
  CHECK(r.writer.stop(true));

  CHECK(!r.fs.has("/REC0007_P01.TMP"));
  CHECK(r.fs.has("/REC0007_P01.WAV"));
  CHECK(r.fs.sizeOf("/REC0007_P01.WAV") == 44 + 16000);
  CHECK(pcmBytesOf(r.fs, "/REC0007_P01.WAV") == 16000);
  CHECK(r.fs.has("/REC0007_N01.END"));
  CHECK(r.writer.endCreated());
  CHECK(r.ring.available() == 0);
  CHECK(r.fs.openFiles == 0);
  // Содержимое — те же байты, что пришли с микрофона, начиная с предзаписи.
  const std::vector<uint8_t> &d = r.fs.files["/REC0007_P01.WAV"];
  bool sequential = true;
  for (uint32_t i = 0; i < 16000; ++i) {
    if (d[44 + i] != static_cast<uint8_t>(i)) sequential = false;
  }
  CHECK(sequential);
}

static void test_parts_rotate_exactly_on_the_byte_boundary() {
  Rig r;
  r.writer.cfg.partMaxBytes = 32000;  // секунда — чтобы тест был быстрым
  r.capture(50000);
  CHECK(r.writer.start(3));
  CHECK(r.drain());
  r.capture(20000);
  CHECK(r.drain());
  CHECK(r.writer.stop(true));

  CHECK(pcmBytesOf(r.fs, "/REC0003_P01.WAV") == 32000);
  CHECK(pcmBytesOf(r.fs, "/REC0003_P02.WAV") == 32000);
  CHECK(pcmBytesOf(r.fs, "/REC0003_P03.WAV") == 6000);
  CHECK(r.fs.has("/REC0003_N03.END"));
  CHECK(r.writer.sessionBytes() == 70000);
  CHECK(!r.fs.has("/REC0003_P01.TMP") && !r.fs.has("/REC0003_P03.TMP"));
}

static void test_mark_lands_in_the_part_where_the_audio_will_land() {
  Rig r;
  r.writer.cfg.partMaxBytes = 32000;
  r.capture(38000);  // 1,1875 с: ещё ничего не записано, всё в буфере
  CHECK(r.writer.start(9));
  CHECK(r.writer.mark());
  // Позиция 38000 — это часть 2, смещение 6000 байт = 0,19 с (0,1875 округлено).
  CHECK(r.fs.has("/REC0009_P02.MRK"));
  CHECK(!r.fs.has("/REC0009_P01.MRK"));
  const std::vector<uint8_t> &m = r.fs.files["/REC0009_P02.MRK"];
  CHECK(std::string(m.begin(), m.end()) == "0.19\n");

  r.drain();
  CHECK(r.writer.mark());  // ещё одна: буфер почти пуст, позиция ~38000 + хвост
  CHECK(r.writer.marks() == 2);
  const std::vector<uint8_t> &m2 = r.fs.files["/REC0009_P02.MRK"];
  CHECK(std::string(m2.begin(), m2.end()).rfind("0.19\n", 0) == 0);  // append, не перезапись
  CHECK(r.writer.stop(true));
  CHECK(r.fs.has("/REC0009_P02.WAV"));  // звук и метка сошлись в одной части
}

// --- отказы при закрытии: главное, ради чего этот файл существует -------------------------

static void test_failed_rename_at_stop_means_no_end_file() {
  Rig r;
  r.capture(9000);
  CHECK(r.writer.start(7));
  r.fs.failRename.insert("/REC0007_P01.TMP");

  CHECK(!r.writer.stop(true));

  CHECK(r.fs.has("/REC0007_P01.TMP"));  // остался .TMP: при старте станет _R
  CHECK(!r.fs.has("/REC0007_P01.WAV"));
  CHECK(!r.fs.has("/REC0007_N01.END"));
  CHECK(!r.writer.endCreated());
  CHECK(!r.writer.recording());
  CHECK(logHas("renamed=NO"));
}

static void test_failed_header_rewrite_at_stop_makes_bad_and_no_end() {
  Rig r;
  r.capture(9000);
  CHECK(r.writer.start(7));
  r.fs.failWritesAtZeroFrom = 2;  // первая запись в 0 — заголовок при открытии, вторая — при закрытии

  CHECK(!r.writer.stop(true));

  CHECK(r.fs.has("/REC0007_P01.BAD"));
  CHECK(!r.fs.has("/REC0007_P01.WAV"));
  CHECK(!r.fs.has("/REC0007_N01.END"));
}

static void test_write_error_mid_part_stops_dirty_without_end() {
  Rig r;
  r.writer.cfg.partMaxBytes = 32000;
  r.fs.failWriteAfter["/REC0007_P01.TMP"] = 44 + 20000;  // карта «кончилась» на 20 000-м байте
  r.capture(40000);
  CHECK(r.writer.start(7));

  CHECK(!r.drain());  // service вернул false
  CHECK(r.writer.recording());  // часть ещё открыта: закрыть обязан вызывающий
  CHECK(r.writer.stats().sd_write_errors == 1);
  CHECK(!r.writer.stop(false));

  CHECK(r.fs.has("/REC0007_P01.BAD"));
  CHECK(!r.fs.has("/REC0007_N01.END"));
  // Заголовок описывает то, что реально дошло, а не то, что хотели записать.
  CHECK(pcmBytesOf(r.fs, "/REC0007_P01.BAD") == 20000);
}

static void test_failed_rotation_in_the_middle_stops_dirty() {
  Rig r;
  r.writer.cfg.partMaxBytes = 32000;
  r.fs.failRename.insert("/REC0007_P01.TMP");
  r.capture(50000);  // за границей части остаётся больше куска: дойдём до ротации
  CHECK(r.writer.start(7));
  CHECK(!r.drain());  // ротация упала на переименовании первой части
  CHECK(!r.writer.stop(false));
  CHECK(!r.fs.has("/REC0007_N01.END") && !r.fs.has("/REC0007_N02.END"));
}

static void test_part_limit_stops_cleanly_with_end() {
  Rig r;
  r.writer.cfg.partMaxBytes = 8192;
  r.writer.cfg.maxParts = 2;
  r.capture(30000);
  CHECK(r.writer.start(7));
  CHECK(r.drain());
  CHECK(r.writer.partLimitReached());
  CHECK(r.writer.stop(true));
  CHECK(r.fs.has("/REC0007_P01.WAV") && r.fs.has("/REC0007_P02.WAV"));
  CHECK(!r.fs.has("/REC0007_P03.WAV") && !r.fs.has("/REC0007_P03.TMP"));
  CHECK(r.fs.has("/REC0007_N02.END"));
  CHECK(logHas("Part limit"));
}

static void test_missing_end_is_reported_but_audio_is_still_clean() {
  Rig r;
  r.capture(1000);
  CHECK(r.writer.start(7));
  r.fs.failOpen.insert("/REC0007_N01.END");
  CHECK(r.writer.stop(true));  // звук цел: сервер просто подождёт тишины
  CHECK(r.fs.has("/REC0007_P01.WAV"));
  CHECK(!r.writer.endCreated());
  CHECK(logHas("cannot create /REC0007_N01.END"));
}

// --- восстановление ----------------------------------------------------------------------

static void test_recovery_fixes_header_and_renames_to_r() {
  FakeStorage fs;
  fs.total = 1ull << 30;
  uint8_t h[44];
  session::buildWavHeader(h, 0);
  std::vector<uint8_t> tmp(h, h + 44);
  tmp.insert(tmp.end(), 1001, 0x55);  // 1000 байт звука и один лишний
  fs.files["/REC0005_P02.TMP"] = tmp;
  fs.files["/REC0006_P01.TMP"] = std::vector<uint8_t>(300, 'x');  // не наш формат
  fs.files["/REC0005_P02.MRK"] = std::vector<uint8_t>{'1', '.', '5', '0', '\n'};

  g_log.clear();
  CHECK(session::recoverUnfinished(fs, fakeLog) == 1);

  CHECK(!fs.has("/REC0005_P02.TMP"));
  CHECK(fs.has("/REC0005_P02_R.WAV"));
  CHECK(pcmBytesOf(fs, "/REC0005_P02_R.WAV") == 1000);  // нечётный хвост в заголовок не попал
  CHECK(fs.has("/REC0006_P01.TMP"));  // чужое не трогаем
  CHECK(logHas("SKIP /REC0006_P01.TMP"));
  // Метки восстановленной части находятся по имени без _R.
  char mrk[32];
  uploadpolicy::marksNameFor("REC0005_P02_R.WAV", mrk, sizeof(mrk));
  CHECK(fs.has(std::string("/") + mrk));
}

static void test_recovery_with_nothing_to_do_is_quiet() {
  FakeStorage fs;
  fs.files["/REC0005_P02.WAV"] = std::vector<uint8_t>(100, 0);
  g_log.clear();
  CHECK(session::recoverUnfinished(fs, fakeLog) == 0);
  CHECK(logHas("No unfinished parts"));
}

static void test_max_session_on_card_reads_every_kind_of_file() {
  FakeStorage fs;
  fs.files["/REC0007_P01.SENT"];
  fs.files["/REC0012_N03.END"];
  fs.files["/REC0009_P01.TMP"];
  fs.files["/OTHER.TXT"];
  CHECK(session::maxSessionOnCard(fs) == 12);
  FakeStorage empty;
  CHECK(session::maxSessionOnCard(empty) == 0);
}

// --- очистка -------------------------------------------------------------------------------

static std::string sentName(int session, bool recovered = false) {
  char buf[40];
  snprintf(buf, sizeof(buf), "/REC%04d_P01%s.SENT", session, recovered ? "_R" : "");
  return buf;
}

static void test_cleanup_deletes_oldest_sent_in_batches_until_target() {
  FakeStorage fs;
  const uint64_t MB = 1024 * 1024;
  for (int s = 1; s <= 200; ++s) {
    fs.files[sentName(s)] = std::vector<uint8_t>(MB, 0);
    fs.files["/REC" + std::string(sentName(s).substr(4, 4)) + "_P01.MRK"] = {'1', '\n'};
  }
  fs.files["/REC0201_P01.WAV"] = std::vector<uint8_t>(MB, 0);
  fs.files["/REC0202_P01.CONFLICT"] = std::vector<uint8_t>(MB, 0);
  fs.files["/REC0203_P01.TMP"] = std::vector<uint8_t>(MB, 0);
  fs.files["/REC0001_N01.END"];
  fs.total = 300 * MB;
  fs.usedByOthers = 90 * MB;  // свободно ~7 МБ

  g_log.clear();
  session::CleanupResult r = session::cleanupSent(fs, 50 * MB, 150 * MB, fakeLog);

  CHECK(r.deleted > 32);  // больше одной пачки: ограничение пачки не остановило очистку
  CHECK(r.freeBytes >= 150 * MB);
  CHECK(!r.cardLow);
  CHECK(!fs.has(sentName(1)) && !fs.has(sentName(2)));           // старые ушли
  CHECK(!fs.has("/REC0001_P01.MRK"));                             // вместе с метками
  CHECK(fs.has(sentName(200)) && fs.has(sentName(199)));         // новые остались
  CHECK(fs.has("/REC0201_P01.WAV") && fs.has("/REC0202_P01.CONFLICT") &&
        fs.has("/REC0203_P01.TMP") && fs.has("/REC0001_N01.END"));
  // Удалены ровно самые старые: ни одного пропуска в хвосте.
  int firstKept = 0;
  for (int s = 1; s <= 200 && !firstKept; ++s) {
    if (fs.has(sentName(s))) firstKept = s;
  }
  bool contiguous = true;
  for (int s = firstKept; s <= 200; ++s) {
    if (!fs.has(sentName(s))) contiguous = false;
  }
  CHECK(contiguous);
  CHECK(firstKept - 1 == r.deleted);
}

static void test_cleanup_flags_low_card_only_when_nothing_is_left_to_delete() {
  FakeStorage fs;
  const uint64_t MB = 1024 * 1024;
  fs.files["/REC0001_P01.WAV"] = std::vector<uint8_t>(MB, 0);
  fs.files["/REC0002_P01.BAD"] = std::vector<uint8_t>(MB, 0);
  fs.files["/REC0003_P01.REJECT"] = std::vector<uint8_t>(MB, 0);
  fs.total = 100 * MB;
  fs.usedByOthers = 95 * MB;

  session::CleanupResult r = session::cleanupSent(fs, 50 * MB, 80 * MB, fakeLog);
  CHECK(r.deleted == 0);
  CHECK(r.cardLow);
  CHECK(fs.files.size() == 3);  // неподтверждённое не тронуто
}

static void test_cleanup_does_nothing_while_space_is_fine() {
  FakeStorage fs;
  const uint64_t MB = 1024 * 1024;
  fs.files[sentName(1)] = std::vector<uint8_t>(MB, 0);
  fs.total = 1000 * MB;
  fs.usedByOthers = 10 * MB;
  session::CleanupResult r = session::cleanupSent(fs, 50 * MB, 80 * MB, fakeLog);
  CHECK(r.deleted == 0 && !r.cardLow);
  CHECK(fs.has(sentName(1)));
}

static void test_cleanup_removes_recovered_sent_and_its_marks() {
  FakeStorage fs;
  const uint64_t MB = 1024 * 1024;
  fs.files[sentName(4, true)] = std::vector<uint8_t>(MB, 0);   // REC0004_P01_R.SENT
  fs.files["/REC0004_P01.MRK"] = {'2', '\n'};
  fs.total = 100 * MB;
  fs.usedByOthers = 98 * MB;
  session::CleanupResult r = session::cleanupSent(fs, 50 * MB, 60 * MB, fakeLog);
  CHECK(r.deleted == 1);
  CHECK(!fs.has(sentName(4, true)));
  CHECK(!fs.has("/REC0004_P01.MRK"));
}

int main() {
  test_start_includes_preroll_and_stop_writes_end();
  test_parts_rotate_exactly_on_the_byte_boundary();
  test_mark_lands_in_the_part_where_the_audio_will_land();
  test_failed_rename_at_stop_means_no_end_file();
  test_failed_header_rewrite_at_stop_makes_bad_and_no_end();
  test_write_error_mid_part_stops_dirty_without_end();
  test_failed_rotation_in_the_middle_stops_dirty();
  test_part_limit_stops_cleanly_with_end();
  test_missing_end_is_reported_but_audio_is_still_clean();
  test_recovery_fixes_header_and_renames_to_r();
  test_recovery_with_nothing_to_do_is_quiet();
  test_max_session_on_card_reads_every_kind_of_file();
  test_cleanup_deletes_oldest_sent_in_batches_until_target();
  test_cleanup_flags_low_card_only_when_nothing_is_left_to_delete();
  test_cleanup_does_nothing_while_space_is_fine();
  test_cleanup_removes_recovered_sent_and_its_marks();

  printf("session tests: %d checks, %d failed\n", g_checked, g_failed);
  return g_failed == 0 ? 0 : 1;
}
