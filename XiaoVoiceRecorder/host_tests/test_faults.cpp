// Пропадание питания в каждый опасный момент и длительная эксплуатация —
// без платы и без ожидания реальных часов.
//
// Главное утверждение всего файла одно: **ни один байт, уже дошедший до
// карты, не теряется**. Что не успело дойти — потеряно по определению
// (питания не было), и это единственная допустимая потеря.
//
// Проверяется логика, а не карта: настоящие задержки microSD, износ ячеек и
// повреждение FAT здесь не воспроизводятся и воспроизведены быть не могут.
#define RING_BUFFER_TESTING
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
static void quietLog(const char *) {}

// Сколько звука лежит на карте: во всех закрытых частях плюс то, что успело
// дойти до незакрытой. Именно это число обязано пережить пропадание питания.
static uint32_t audioOnCard(const FakeStorage &fs) {
  uint32_t total = 0;
  for (const auto &entry : fs.files) {
    const std::string &name = entry.first;
    if (name.size() < 4) continue;
    const std::string ext = name.substr(name.size() - 4);
    if (ext == ".WAV" || ext == ".TMP" || ext == ".BAD") {
      const size_t bytes = entry.second.size();
      if (bytes > partmath::WAV_HEADER_BYTES) {
        total += partmath::wholeFrames(
            static_cast<uint32_t>(bytes - partmath::WAV_HEADER_BYTES));
      }
    }
  }
  return total;
}

// Звук в готовых файлах после восстановления: считается по заголовку, то есть
// ровно столько, сколько увидит сервер.
static uint32_t audioDeclared(const FakeStorage &fs) {
  uint32_t total = 0;
  for (const auto &entry : fs.files) {
    const std::string &name = entry.first;
    if (name.size() > 4 && name.compare(name.size() - 4, 4, ".WAV") == 0) {
      total += session::getLe32(entry.second.data() + 40);
    }
  }
  return total;
}

struct Rig {
  FakeStorage fs;
  uint8_t storage[1 << 15];  // 32 КиБ кольца
  RingBuffer ring;
  session::Writer writer;

  Rig() : writer(fs, ring, fakeNow, quietLog) {
    ring.init(storage, sizeof(storage));
    fs.total = 1ull << 32;
    writer.cfg.partMaxBytes = 8192;
    writer.cfg.writeChunkBytes = 1024;
  }
  void capture(uint32_t n) {
    std::vector<uint8_t> chunk(n, 0x5A);
    ring.push(chunk.data(), n);
  }
  bool drain() {
    for (int i = 0; i < 100000 && ring.available() >= writer.cfg.writeChunkBytes; ++i) {
      if (!writer.service()) return false;
      if (writer.partLimitReached()) return true;
    }
    return true;
  }
};

// --- питание пропало в каждый опасный момент ---------------------------------

static void test_power_loss_right_after_the_part_was_created() {
  Rig r;
  r.capture(3000);
  CHECK(r.writer.start(7));
  CHECK(r.fs.has("/REC0007_P01.TMP"));
  const uint32_t before = audioOnCard(r.fs);  // только заголовок, звука нет

  r.fs.powerLoss();
  CHECK(session::recoverUnfinished(r.fs, quietLog) == 1);

  CHECK(r.fs.has("/REC0007_P01_R.WAV"));
  CHECK(audioDeclared(r.fs) == before);
  CHECK(before == 0);  // ничего не успело записаться — и терять нечего
}

static void test_power_loss_in_the_middle_of_a_part_keeps_what_reached_the_card() {
  Rig r;
  r.capture(20000);
  CHECK(r.writer.start(7));
  CHECK(r.drain());
  const uint32_t reached = audioOnCard(r.fs);
  CHECK(reached > 0);

  r.fs.powerLoss();
  CHECK(session::recoverUnfinished(r.fs, quietLog) == 1);

  CHECK(audioDeclared(r.fs) == reached);
  CHECK(!r.fs.has("/REC0007_P03.TMP"));
  CHECK(r.fs.has("/REC0007_P01.WAV"));   // закрытые части уже были готовы
  CHECK(r.fs.has("/REC0007_P03_R.WAV")); // открытая починена и помечена _R
  // Восстановленная сессия не объявляет число частей: сервер подождёт тишины.
  CHECK(!r.fs.has("/REC0007_N03.END"));
}

static void test_power_loss_on_the_boundary_between_two_parts() {
  Rig r;
  r.capture(24000);  // три части по 8192
  CHECK(r.writer.start(7));
  CHECK(r.drain());
  const uint32_t reached = audioOnCard(r.fs);

  r.fs.powerLoss();
  CHECK(session::recoverUnfinished(r.fs, quietLog) == 1);  // чинится только открытая

  CHECK(audioDeclared(r.fs) == reached);
  CHECK(r.fs.has("/REC0007_P01.WAV"));   // закрытые части уже готовы
  CHECK(r.fs.has("/REC0007_P02.WAV"));
  CHECK(r.fs.has("/REC0007_P03_R.WAV"));
}

static void test_power_loss_after_the_last_part_but_before_the_end_file() {
  Rig r;
  r.capture(5000);
  CHECK(r.writer.start(7));
  CHECK(r.drain());
  // Питание пропало ровно между переименованием части и созданием .END.
  r.fs.failOpen.insert("/REC0007_N01.END");
  const bool clean = r.writer.stop(true);
  CHECK(!clean == false);  // сам звук закрыт чисто
  CHECK(r.fs.has("/REC0007_P01.WAV"));
  CHECK(!r.fs.has("/REC0007_N01.END"));

  // Ничего чинить не нужно, звук готов; сессия просто уйдёт по тишине.
  CHECK(session::recoverUnfinished(r.fs, quietLog) == 0);
  CHECK(audioDeclared(r.fs) == 5000);  // всё, что было в буфере, дописано
}

static void test_power_loss_while_a_mark_was_being_written() {
  Rig r;
  r.capture(4000);
  CHECK(r.writer.start(7));
  CHECK(r.writer.mark());
  CHECK(r.writer.mark());
  // Обрывок последней строки: питание пропало посреди дописи.
  std::vector<uint8_t> &mrk = r.fs.files["/REC0007_P01.MRK"];
  mrk.push_back('1');
  mrk.push_back('2');
  mrk.push_back('.');
  r.fs.powerLoss();
  session::recoverUnfinished(r.fs, quietLog);

  // Обрывок отбрасывается, целые метки остаются: иначе сервер отверг бы часть.
  const std::string text(r.fs.files["/REC0007_P01.MRK"].begin(),
                         r.fs.files["/REC0007_P01.MRK"].end());
  char header[256];
  CHECK(uploadpolicy::marksHeader(text.c_str(), header, sizeof(header)) == 2);
  CHECK(strcmp(header, "0.13,0.13") == 0);  // 4000 байт предзаписи = 0,125 с
}

static void test_power_loss_during_cleanup_loses_nothing_unconfirmed() {
  FakeStorage fs;
  const uint64_t MB = 1024 * 1024;
  for (int s = 1; s <= 6; ++s) {
    char name[32];
    snprintf(name, sizeof(name), "/REC%04d_P01.SENT", s);
    fs.files[name] = std::vector<uint8_t>(MB, 0);
  }
  fs.files["/REC0007_P01.WAV"] = std::vector<uint8_t>(MB, 0);
  fs.total = 10 * MB;
  fs.usedByOthers = 2 * MB;

  // Питание пропало после двух удалений: остальное просто не случилось.
  fs.failRemove.insert("/REC0003_P01.SENT");
  session::cleanupSent(fs, 3 * MB, 5 * MB, quietLog);

  CHECK(fs.has("/REC0007_P01.WAV"));  // неподтверждённое не трогали ни разу
  CHECK(!fs.has("/REC0001_P01.SENT"));
  CHECK(fs.has("/REC0003_P01.SENT"));
}

static void test_recovery_twice_in_a_row_is_safe() {
  Rig r;
  r.capture(9000);
  CHECK(r.writer.start(7));
  CHECK(r.drain());
  r.fs.powerLoss();

  CHECK(session::recoverUnfinished(r.fs, quietLog) == 1);
  const uint32_t after_first = audioDeclared(r.fs);
  CHECK(session::recoverUnfinished(r.fs, quietLog) == 0);  // чинить больше нечего
  CHECK(session::recoverUnfinished(r.fs, quietLog) == 0);
  CHECK(audioDeclared(r.fs) == after_first);
}

static void test_recovered_part_recovered_again_after_a_second_power_loss() {
  Rig r;
  r.capture(9000);
  CHECK(r.writer.start(7));
  CHECK(r.drain());
  r.fs.powerLoss();
  CHECK(session::recoverUnfinished(r.fs, quietLog) == 1);
  const uint32_t first = audioDeclared(r.fs);

  // Новая сессия после перезагрузки, и снова пропало питание.
  Rig r2;
  r2.fs.files = r.fs.files;
  r2.capture(5000);
  CHECK(r2.writer.start(session::maxSessionOnCard(r2.fs) + 1));
  CHECK(r2.drain());
  const uint32_t reached = audioOnCard(r2.fs);
  r2.fs.powerLoss();
  CHECK(session::recoverUnfinished(r2.fs, quietLog) == 1);

  CHECK(audioDeclared(r2.fs) == reached);
  CHECK(audioDeclared(r2.fs) > first);  // старая запись на месте, новая добавилась
}

// --- номера сессий -------------------------------------------------------------

static void test_session_numbers_run_out_instead_of_wrapping_onto_old_audio() {
  CHECK(session::nextSessionId(0, 0) == 1);
  CHECK(session::nextSessionId(5, 12) == 13);   // берётся больший из двух
  CHECK(session::nextSessionId(12, 5) == 13);   // даже если карта уже очищена
  CHECK(session::nextSessionId(9998, 0) == 9999);
  // 9999 — предел формата имени. Ноль означает «номера кончились»: повторить
  // номер нельзя, сервер ответил бы 409 на чужую часть.
  CHECK(session::nextSessionId(9999, 0) == 0);
  CHECK(session::nextSessionId(0, 9999) == 0);
}

// Тот же сценарий, но так, как он идёт в живой прошивке: после каждой
// остановки номер следующей сессии считается helper-ом, а не просто +1.
// Иначе после сессии 9999 получилось бы пятизначное имя, под которое формат
// файла не рассчитан, а сервер ключует части по номеру сессии.
static void test_after_stopping_session_9999_the_next_start_is_refused() {
  uint16_t next = session::nextSessionId(9998, 9998);
  CHECK(next == 9999);

  Rig r;
  r.capture(2000);
  CHECK(r.writer.start(next));
  CHECK(r.drain());
  CHECK(r.writer.stop(true));
  CHECK(r.fs.has("/REC9999_P01.WAV"));
  CHECK(r.fs.has("/REC9999_N01.END"));

  // Так это делает stopRecording(): не sessionId() + 1.
  next = session::nextSessionId(r.writer.sessionId(), r.writer.sessionId());
  CHECK(next == 0);  // startRecording() при нуле отказывает

  // И после перезагрузки ответ тот же: карта помнит 9999.
  CHECK(session::nextSessionId(0, session::maxSessionOnCard(r.fs)) == 0);
}

static void test_mark_pressed_just_before_the_session_rolled_over() {
  // Предел частей: прошивка закрывает сессию и открывает следующую, пока
  // событие кнопки ещё ждёт разбора. Метка не должна уйти в мусорный номер
  // части от переполнения разности.
  Rig r;
  r.writer.cfg.maxParts = 2;
  r.capture(8000);
  CHECK(r.writer.start(7));
  // Кнопку нажали в самом начале сессии 7, а событие ждёт разбора.
  const uint32_t headAtPress = r.ring.head();

  r.capture(30000);
  CHECK(r.drain());
  CHECK(r.writer.partLimitReached());
  CHECK(r.writer.stop(true));
  CHECK(r.writer.start(8));                 // новая сессия, ringBase уехал вперёд
  CHECK(r.writer.ringBase() > headAtPress); // нажатие осталось позади её начала

  const uint32_t headBeforeRollover = headAtPress;

  CHECK(r.writer.markAt(headBeforeRollover));
  // Ближайшая правда — самое начало новой сессии.
  CHECK(r.fs.has("/REC0008_P01.MRK"));
  const std::vector<uint8_t> &m = r.fs.files["/REC0008_P01.MRK"];
  CHECK(std::string(m.begin(), m.end()) == "0.00\n");
  // Мусорных номеров частей не появилось.
  for (const auto &entry : r.fs.files) {
    CHECK(entry.first.size() <= 18);
  }
}

static void test_mark_beyond_the_part_limit_stays_in_the_last_part() {
  Rig r;
  r.writer.cfg.maxParts = 2;
  r.writer.cfg.partMaxBytes = 32000;
  r.capture(10000);
  CHECK(r.writer.start(7));
  // Положение далеко за пределом сессии: звук туда уже не попадёт.
  CHECK(r.writer.markAt(r.writer.ringBase() + 32000 * 10));
  CHECK(r.fs.has("/REC0007_P02.MRK"));     // последняя часть этой сессии
  CHECK(!r.fs.has("/REC0007_P11.MRK"));
}

// --- долгая эксплуатация ---------------------------------------------------------

static void test_five_hundred_sessions_do_not_lose_a_byte() {
  // Не ждём девять часов: часть уменьшена до 8 КиБ, но кода это не касается —
  // он режет части по байтам, а не по часам.
  Rig r;
  uint32_t expected = 0;
  uint32_t marks = 0;
  for (int s = 1; s <= 500; ++s) {
    const uint32_t chunk = 3000 + (s % 7) * 1500;
    r.capture(chunk);
    expected += chunk;
    CHECK(r.writer.start(static_cast<uint16_t>(s)));
    CHECK(r.drain());
    if (s % 3 == 0) {
      CHECK(r.writer.mark());
      ++marks;
    }
    CHECK(r.writer.stop(true));
    g_now += 1000;
  }
  CHECK(audioDeclared(r.fs) == expected);
  CHECK(r.ring.available() == 0);
  CHECK(r.ring.dropped() == 0);
  CHECK(r.fs.openFiles == 0);

  int endFiles = 0, mrkFiles = 0, parts = 0;
  for (const auto &entry : r.fs.files) {
    const std::string &n = entry.first;
    if (n.size() > 4 && n.compare(n.size() - 4, 4, ".END") == 0) ++endFiles;
    if (n.size() > 4 && n.compare(n.size() - 4, 4, ".MRK") == 0) ++mrkFiles;
    if (n.size() > 4 && n.compare(n.size() - 4, 4, ".WAV") == 0) ++parts;
  }
  CHECK(endFiles == 500);
  CHECK(mrkFiles == static_cast<int>(marks));
  CHECK(parts > 500);
}

static void test_thousands_of_parts_in_one_session_stop_at_the_limit() {
  Rig r;
  r.writer.cfg.maxParts = 99;
  CHECK(r.writer.start(1));
  uint32_t fed = 0;
  for (int i = 0; i < 200 && !r.writer.partLimitReached(); ++i) {
    r.capture(8192);
    fed += 8192;
    CHECK(r.drain());
  }
  CHECK(r.writer.partLimitReached());
  CHECK(r.writer.partIndex() == 99);
  const uint32_t tail = r.ring.available();
  CHECK(r.writer.stop(true));
  CHECK(r.fs.has("/REC0001_N99.END"));
  // Хвост не выброшен: он уходит в следующую сессию, как в живом устройстве.
  CHECK(r.writer.start(2));
  CHECK(r.writer.preRollBytes() == tail);
  CHECK(r.drain());
  CHECK(r.writer.stop(true));
  CHECK(audioDeclared(r.fs) == fed);
}

static void test_a_full_card_never_eats_unconfirmed_audio() {
  FakeStorage fs;
  const uint64_t MB = 1024 * 1024;
  for (int s = 1; s <= 50; ++s) {
    char sent[32], wav[32];
    snprintf(sent, sizeof(sent), "/REC%04d_P01.SENT", s);
    snprintf(wav, sizeof(wav), "/REC%04d_P02.WAV", s);
    fs.files[sent] = std::vector<uint8_t>(MB, 0);
    fs.files[wav] = std::vector<uint8_t>(MB, 0);
  }
  fs.total = 110 * MB;   // 100 МБ занято нашими файлами, свободно 10 МБ

  session::CleanupResult r = session::cleanupSent(fs, 20 * MB, 200 * MB, quietLog);

  CHECK(r.deleted == 50);          // все подтверждённые ушли
  CHECK(r.cardLow == false);       // 60 МБ свободно, порог 20 МБ пройден
  int wavLeft = 0;
  for (const auto &entry : fs.files) {
    if (entry.first.size() > 4 &&
        entry.first.compare(entry.first.size() - 4, 4, ".WAV") == 0) {
      ++wavLeft;
    }
  }
  CHECK(wavLeft == 50);            // ни одна неотправленная часть не тронута
}

// --- точность метки ---------------------------------------------------------------

static void test_mark_uses_the_moment_of_the_press_not_of_the_handling() {
  // Кнопка нажата, затем цикл простоял на TLS-рукопожатии, и только потом
  // метка дошла до карты. Она обязана указывать на момент нажатия.
  Rig r;
  r.writer.cfg.partMaxBytes = partmath::PART_MAX_BYTES;
  r.capture(32000);  // секунда предзаписи
  CHECK(r.writer.start(7));
  const uint32_t headAtPress = r.ring.head();   // ISR снял бы ровно это

  r.capture(32000 * 2);  // пока цикл был занят, набежало ещё две секунды
  CHECK(r.drain());

  CHECK(r.writer.markAt(headAtPress));
  const std::vector<uint8_t> &m = r.fs.files["/REC0007_P01.MRK"];
  CHECK(std::string(m.begin(), m.end()) == "1.00\n");

  // Для сравнения: метка «по текущему моменту» отстала бы на эти две секунды.
  CHECK(r.writer.mark());
  const std::vector<uint8_t> &m2 = r.fs.files["/REC0007_P01.MRK"];
  CHECK(std::string(m2.begin(), m2.end()) == "1.00\n3.00\n");
}

static void test_mark_position_survives_the_counter_wrapping() {
  // Кольцевые счётчики 32-битные. Позиция метки — разность двух из них,
  // поэтому переполнение её не ломает.
  Rig r;
  r.writer.cfg.partMaxBytes = partmath::PART_MAX_BYTES;
  r.ring.testSetCounters(0xFFFFF000u, 0xFFFFF000u);
  r.capture(32000);
  CHECK(r.writer.start(7));
  CHECK(r.writer.ringBase() == 0xFFFFF000u);
  const uint32_t headAtPress = r.ring.head();
  r.capture(20000);
  CHECK(r.drain());
  CHECK(r.writer.markAt(headAtPress));
  const std::vector<uint8_t> &m = r.fs.files["/REC0007_P01.MRK"];
  CHECK(std::string(m.begin(), m.end()) == "1.00\n");
}

int main() {
  test_power_loss_right_after_the_part_was_created();
  test_power_loss_in_the_middle_of_a_part_keeps_what_reached_the_card();
  test_power_loss_on_the_boundary_between_two_parts();
  test_power_loss_after_the_last_part_but_before_the_end_file();
  test_power_loss_while_a_mark_was_being_written();
  test_power_loss_during_cleanup_loses_nothing_unconfirmed();
  test_recovery_twice_in_a_row_is_safe();
  test_recovered_part_recovered_again_after_a_second_power_loss();
  test_session_numbers_run_out_instead_of_wrapping_onto_old_audio();
  test_after_stopping_session_9999_the_next_start_is_refused();
  test_mark_pressed_just_before_the_session_rolled_over();
  test_mark_beyond_the_part_limit_stays_in_the_last_part();
  test_five_hundred_sessions_do_not_lose_a_byte();
  test_thousands_of_parts_in_one_session_stop_at_the_limit();
  test_a_full_card_never_eats_unconfirmed_audio();
  test_mark_uses_the_moment_of_the_press_not_of_the_handling();
  test_mark_position_survives_the_counter_wrapping();

  printf("fault tests: %d checks, %d failed\n", g_checked, g_failed);
  return g_failed == 0 ? 0 : 1;
}
