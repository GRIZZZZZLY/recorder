// Жизненный цикл записи и обслуживание карты — без Arduino.
//
//   session::Writer      START с предзаписью, слив буфера, ротация частей ровно
//                        по байтам, метки, STOP с .END только после чистого
//                        закрытия, CRC закрытых частей без второго чтения
//   session::recoverUnfinished  .TMP после пропадания питания -> _R.WAV
//   session::cleanupSent        удаление только .SENT, старые первыми, пока не
//                               освободится нужное место
//   session::maxSessionOnCard   наибольший номер сессии в корне
//
// Всё общается с картой через Storage.h, поэтому проверяется на хосте с
// хранилищем в памяти и намеренными отказами (host_tests/test_session.cpp).
// Здесь нет ни Serial, ни String: журнал — через LogFn, строки — char[].
#pragma once

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "AudioFormat.h"
#include "Crc32.h"
#include "RingBuffer.h"
#include "Storage.h"
#include "UploadPolicy.h"

namespace session {

constexpr size_t PATH_MAX_LEN = 32;
constexpr uint16_t MAX_SESSIONS = 9999;
constexpr size_t KNOWN_CRCS = 16;

// --- имена и заголовок WAV ------------------------------------------------------

// /REC0007_P03.WAV — сессия 7, часть 3.
inline void partPath(char *out, size_t n, uint16_t sessionId, uint16_t part, const char *ext) {
  snprintf(out, n, "/REC%04u_P%02u.%s", sessionId, part, ext);
}

// /REC0007_N08.END — сессия 7 закрыта чисто, в ней 8 частей.
inline void endPath(char *out, size_t n, uint16_t sessionId, uint16_t parts) {
  snprintf(out, n, "/REC%04u_N%02u.END", sessionId, parts);
}

// Заголовок описывает формат, которым писалась ЭТА часть (AudioFormat.h).
inline bool writeWavHeader(StorageFile *f, const audio::Format &fmt, uint32_t pcmBytes) {
  uint8_t h[44];
  audio::buildWavHeader(h, fmt, pcmBytes);
  return f->seek(0) && f->write(h, 44) == 44;
}

inline void say(LogFn log, const char *line) {
  if (log) log(line);
}

// --- запись -------------------------------------------------------------------------

struct WriterStats {
  uint32_t sd_write_max_ms = 0;
  uint32_t sd_write_errors = 0;
  uint32_t part_switch_max_ms = 0;
  uint32_t written_frames = 0;   // кадров дошло до карты (кадр = отсчёт по всем каналам)
};

class Writer {
 public:
  struct Config {
    // Формат задаётся конструктором Writer вместе с размером части: моно и
    // стерео различаются только этими двумя числами, вся остальная
    // арифметика — байты и кадры — от них выводится.
    audio::Format format = audio::MONO_16K;
    uint32_t partMaxBytes = audio::MONO_16K.partMaxBytes();
    uint32_t writeChunkBytes = 8192;   // не больше CHUNK_CAP
    int writesPerService = 4;
    uint32_t flushIntervalMs = 1000;
    uint16_t maxParts = audio::MAX_PARTS;
  };
  static constexpr uint32_t CHUNK_CAP = 8192;

  Writer(Storage &fs, RingBuffer &ring, ClockFn now, LogFn log,
         const audio::Format &format = audio::MONO_16K)
      : m_fs(fs), m_ring(ring), m_now(now), m_log(log) {
    cfg.format = format;
    cfg.partMaxBytes = format.partMaxBytes();
  }

  Config cfg;

  bool recording() const { return m_recording; }
  // Позиция в сессии считается от этой отметки кольца: pos = head - ringBase.
  // Отсюда метку можно поставить по head, снятому в момент нажатия, а не в
  // момент, когда до неё добрался цикл.
  uint32_t ringBase() const { return m_ringBase; }
  uint16_t sessionId() const { return m_session; }
  uint16_t partIndex() const { return m_part; }
  uint32_t partBytes() const { return m_partBytes; }
  uint32_t sessionBytes() const { return m_sessionBytes; }
  uint32_t preRollBytes() const { return m_preRoll; }
  uint16_t marks() const { return m_marks; }
  bool partLimitReached() const { return m_partLimit; }
  bool endCreated() const { return m_endCreated; }
  const char *currentPath() const { return m_path; }
  WriterStats &stats() { return m_stats; }

  // CRC-32 файла части (заголовок + звук), посчитанный при записи, — для
  // X-Crc32 без второго чтения карты. Есть только у частей, чисто закрытых
  // в .WAV с момента загрузки, и только у последних KNOWN_CRCS. name — имя без
  // «/», fileBytes — размер файла на карте: другой размер значит другой файл.
  bool knownCrc(const char *name, uint32_t fileBytes, uint32_t *crc) const {
    for (const KnownCrc &k : m_known) {
      if (k.fileBytes == fileBytes && k.fileBytes != 0 && strcmp(k.name, name) == 0) {
        *crc = k.crc;
        return true;
      }
    }
    return false;
  }

  // Сервер не принял CRC (422): то, что лежит на карте, разошлось с тем, что
  // писалось. Дальше CRC этой части считается только чтением файла.
  void forgetCrc(const char *name) {
    for (KnownCrc &k : m_known) {
      if (strcmp(k.name, name) == 0) k = KnownCrc{};
    }
  }

  // START. Всё, что буфер держит сейчас, — предзапись: становится началом части 1.
  bool start(uint16_t sessionId) {
    if (m_recording) return true;
    m_session = sessionId;
    m_part = 1;
    m_sessionBytes = 0;
    m_marks = 0;
    m_partLimit = false;
    m_endCreated = false;
    m_ring.resetStats();
    // Запас 1/16 ёмкости (2 с в моно): микрофону есть куда писать, пока
    // открывается часть и уходит первая порция на карту.
    m_ring.resync(m_ring.capacity() - m_ring.capacity() / 16);
    m_preRoll = m_ring.available();
    m_ringBase = m_ring.tail();
    if (!openPart()) return false;
    m_recording = true;
    return true;
  }

  // Слив буфера на карту ограниченными порциями. false — ошибка карты: часть
  // ещё открыта, вызывающий обязан сделать stop(false), чтобы она стала .BAD.
  bool service() {
    if (!m_recording) return true;
    const uint32_t chunk = cfg.writeChunkBytes < CHUNK_CAP ? cfg.writeChunkBytes : CHUNK_CAP;
    for (int i = 0; i < cfg.writesPerService && m_ring.available() >= chunk; ++i) {
      if (roomInPart() == 0) {
        if (m_part >= cfg.maxParts) {
          m_partLimit = true;  // вызывающий останавливает чисто
          return true;
        }
        if (!rotatePart()) return false;
      }
      if (!writeChunk(chunk)) return false;
    }
    if (m_file && m_now() - m_lastFlushMs >= cfg.flushIntervalMs) {
      m_file->flush();
      m_lastFlushMs = m_now();
    }
    return true;
  }

  // Метка по текущему моменту.
  bool mark() { return markAt(m_ring.head()); }

  // Метка по положению потока, снятому в момент нажатия кнопки. Между
  // нажатием и разбором события цикл мог простоять секунду-две на
  // TLS-рукопожатии; метка обязана указывать туда, где человек нажал, а не
  // туда, где прошивка об этом узнала.
  //
  // Позиция в сессии — байты от начала первой части: pos = head - ringBase.
  // Части вращаются ровно на partMaxBytes, поэтому номер части — чистая
  // арифметика. За границей части метка достаётся следующей — туда попадёт и
  // сам звук. Имя файла меток не зависит от _R: восстановленная часть находит
  // свои метки.
  bool markAt(uint32_t ringHead) {
    if (!m_recording) return false;
    // Нажатие могло случиться до начала этой сессии: на переходе через предел
    // частей прошивка успевает закрыть одну сессию и открыть другую, пока
    // событие кнопки ждёт разбора. Тогда ближайшая правда — самое начало
    // новой сессии, а не мусорный номер части от переполнения разности.
    const int32_t delta = static_cast<int32_t>(ringHead - m_ringBase);
    uint32_t pos = delta > 0 ? static_cast<uint32_t>(delta) : 0;
    // Позиция за пределом сессии означает то же самое с другой стороны:
    // звук уйдёт в следующую сессию, а метка остаётся в последней части этой.
    const uint32_t lastPartStart = static_cast<uint32_t>(cfg.maxParts - 1) * cfg.partMaxBytes;
    if (pos > lastPartStart + cfg.partMaxBytes - 1) {
      pos = lastPartStart + cfg.partMaxBytes - 1;
    }
    const uint16_t part = static_cast<uint16_t>(pos / cfg.partMaxBytes) + 1;
    const uint32_t offset = pos % cfg.partMaxBytes;

    char text[24];
    int len = cfg.format.formatMark(text, sizeof(text) - 1, offset);
    text[len++] = '\n';

    char path[PATH_MAX_LEN];
    partPath(path, sizeof(path), m_session, part, "MRK");
    StorageFile *f = m_fs.open(path, Storage::APPEND);
    if (!f) {
      logf("[mark] ERROR: cannot open %s", path);
      return false;
    }
    const bool ok = f->write(reinterpret_cast<const uint8_t *>(text), len) == static_cast<size_t>(len);
    f->flush();  // нажатие должно пережить пропадание питания сразу после него
    m_fs.close(f);
    if (ok) {
      ++m_marks;
      text[len - 1] = '\0';
      logf("[mark] %s <- %s (%u в сессии)", path, text, m_marks);
    }
    return ok;
  }

  // STOP. Дописывает ровно то, что накопилось к моменту нажатия. Возвращает
  // true только если каждая часть закрыта чисто — тогда и только тогда
  // создаётся .END. Ошибка закрытия последней части делает её .BAD, и сервер
  // не получит ложного «сессия закончена».
  bool stop(bool healthy) {
    if (!m_recording) return false;

    uint32_t pending = healthy ? m_ring.available() : 0;
    while (pending > 0 && healthy) {
      if (roomInPart() == 0) {
        if (m_part >= cfg.maxParts) {
          say(m_log, "[rec] Part limit: tail of the buffer is not stored.");
          break;
        }
        if (!rotatePart()) {
          healthy = false;
          break;
        }
      }
      const uint32_t before = m_partBytes;
      if (!writeChunk(pending)) {
        healthy = false;
        break;
      }
      if (m_partBytes == before) break;  // буфер не отдал ни байта: не крутимся вечно
      pending -= m_partBytes - before;
    }

    if (!closePart(healthy)) healthy = false;
    m_recording = false;

    if (healthy) {
      char path[PATH_MAX_LEN];
      endPath(path, sizeof(path), m_session, m_part);
      StorageFile *f = m_fs.open(path, Storage::WRITE);
      if (f) {
        m_fs.close(f);
        m_endCreated = true;
      } else {
        logf("[rec] WARNING: cannot create %s; server will wait for quiet instead", path);
      }
    }
    return healthy;
  }

 private:
  uint32_t roomInPart() const {
    return m_partBytes >= cfg.partMaxBytes ? 0 : cfg.partMaxBytes - m_partBytes;
  }

  bool openPart() {
    partPath(m_path, sizeof(m_path), m_session, m_part, "TMP");
    m_file = m_fs.open(m_path, Storage::WRITE);
    if (!m_file) {
      logf("[rec] ERROR: cannot create %s", m_path);
      return false;
    }
    uint8_t h[44];
    audio::buildWavHeader(h, cfg.format, 0);
    if (m_file->write(h, 44) != 44) {
      say(m_log, "[rec] ERROR: cannot write WAV header.");
      m_fs.close(m_file);
      m_file = nullptr;
      return false;
    }
    m_partBytes = 0;
    m_dataCrc = 0;
    m_lastFlushMs = m_now();
    return true;
  }

  // Только чистое закрытие даёт .WAV; всё остальное — .BAD, чтобы битая часть
  // никогда не выглядела готовой. Возвращает false и тогда, когда файл не
  // переименовался: он остался .TMP и будет восстановлен как _R при старте.
  bool closePart(bool healthy) {
    if (!m_file) return false;
    m_file->flush();
    const bool headerOk = writeWavHeader(m_file, cfg.format, m_partBytes);
    m_file->flush();
    m_fs.close(m_file);
    m_file = nullptr;

    const bool ok = healthy && headerOk;
    char finalPath[PATH_MAX_LEN];
    partPath(finalPath, sizeof(finalPath), m_session, m_part, ok ? "WAV" : "BAD");
    const bool renamed = m_fs.rename(m_path, finalPath);
    if (ok && renamed) rememberCrc(finalPath + 1);
    m_sessionBytes += m_partBytes;
    logf("[rec] %s %s | %lu audio bytes | header=%s | renamed=%s",
         ok ? "PART OK" : "PART FAILED", renamed ? finalPath : m_path,
         static_cast<unsigned long>(m_partBytes), headerOk ? "ok" : "BAD",
         renamed ? "yes" : "NO");
    return ok && renamed;
  }

  bool rotatePart() {
    const uint32_t t0 = m_now();
    if (!closePart(true)) {
      say(m_log, "[rec] ERROR: could not finish part.");
      return false;
    }
    ++m_part;
    if (!openPart()) {
      say(m_log, "[rec] ERROR: could not open next part.");
      return false;
    }
    const uint32_t dt = m_now() - t0;
    if (dt > m_stats.part_switch_max_ms) m_stats.part_switch_max_ms = dt;
    logf("[rec] NEXT PART -> %s (%lu ms)", m_path, static_cast<unsigned long>(dt));
    return true;
  }

  // Одна запись из буфера: не больше limit, не больше куска, не дальше границы части.
  bool writeChunk(uint32_t limit) {
    uint32_t want = limit < CHUNK_CAP ? limit : CHUNK_CAP;
    const uint32_t room = roomInPart();
    if (want > room) want = room;
    want = cfg.format.wholeFrames(want);
    if (want == 0) return true;
    const uint32_t got = m_ring.pop(m_chunk, want);
    if (got == 0) return true;

    const uint32_t t0 = m_now();
    const size_t written = m_file->write(m_chunk, got);
    const uint32_t dt = m_now() - t0;
    if (dt > m_stats.sd_write_max_ms) m_stats.sd_write_max_ms = dt;

    // Считаем то, что реально дошло до карты, чтобы заголовок описывал файл.
    const uint32_t kept = cfg.format.wholeFrames(static_cast<uint32_t>(written));
    m_dataCrc = crc32::update(m_dataCrc, m_chunk, kept);
    m_partBytes += kept;
    m_stats.written_frames += cfg.format.frames(kept);
    if (written != got) {
      ++m_stats.sd_write_errors;
      logf("[rec] ERROR: SD write incomplete (%u of %u bytes).",
           static_cast<unsigned>(written), static_cast<unsigned>(got));
      return false;
    }
    return true;
  }

  // CRC файла = CRC итогового заголовка, продолженный CRC звука: заголовок
  // переписывается последним, а звук уже посчитан по мере записи.
  // ponytail: только в памяти, до перезагрузки; после неё загрузчик читает
  // файл целиком, как раньше. Путь наверх — записывать CRC рядом с частью.
  void rememberCrc(const char *name) {
    uint8_t h[audio::WAV_HEADER_BYTES];
    audio::buildWavHeader(h, cfg.format, m_partBytes);
    KnownCrc &k = m_known[m_knownNext];
    m_knownNext = (m_knownNext + 1) % KNOWN_CRCS;
    snprintf(k.name, sizeof(k.name), "%s", name);
    k.fileBytes = audio::WAV_HEADER_BYTES + m_partBytes;
    k.crc = crc32::combine(crc32::update(0, h, sizeof(h)), m_dataCrc, m_partBytes);
  }

  void logf(const char *fmt, ...) {
    if (!m_log) return;
    char line[160];
    va_list args;
    va_start(args, fmt);
    vsnprintf(line, sizeof(line), fmt, args);
    va_end(args);
    m_log(line);
  }

  Storage &m_fs;
  RingBuffer &m_ring;
  ClockFn m_now;
  LogFn m_log;
  StorageFile *m_file = nullptr;
  bool m_recording = false;
  bool m_partLimit = false;
  bool m_endCreated = false;
  uint16_t m_session = 0;
  uint16_t m_part = 0;
  uint16_t m_marks = 0;
  uint32_t m_partBytes = 0;
  uint32_t m_sessionBytes = 0;
  uint32_t m_preRoll = 0;
  uint32_t m_ringBase = 0;
  uint32_t m_lastFlushMs = 0;
  char m_path[PATH_MAX_LEN] = {0};
  uint8_t m_chunk[CHUNK_CAP];
  WriterStats m_stats;
  uint32_t m_dataCrc = 0;  // CRC звука открытой части, без заголовка

  struct KnownCrc {
    char name[PATH_MAX_LEN] = {0};
    uint32_t fileBytes = 0;
    uint32_t crc = 0;
  };
  KnownCrc m_known[KNOWN_CRCS];
  size_t m_knownNext = 0;
};

// --- обслуживание карты -------------------------------------------------------------------

namespace detail {

struct MaxSession {
  uint16_t value = 0;
};

inline void maxSessionCb(const char *name, uint32_t, void *ctx) {
  if (strncmp(name, "REC", 3) != 0 || strlen(name) < 7) return;
  unsigned n = 0;
  for (int i = 3; i < 7; ++i) {
    if (name[i] < '0' || name[i] > '9') return;
    n = n * 10 + (name[i] - '0');
  }
  MaxSession *m = static_cast<MaxSession *>(ctx);
  if (n > m->value) m->value = static_cast<uint16_t>(n);
}

constexpr int MAX_REPAIRS = 16;

struct Unfinished {
  char names[MAX_REPAIRS][PATH_MAX_LEN];
  uint32_t sizes[MAX_REPAIRS];
  int found = 0;
};

inline void unfinishedCb(const char *name, uint32_t size, void *ctx) {
  Unfinished *u = static_cast<Unfinished *>(ctx);
  const size_t len = strlen(name);
  if (u->found >= MAX_REPAIRS || len >= PATH_MAX_LEN - 1) return;
  if (strncmp(name, "REC", 3) != 0 || len < 4 || strcmp(name + len - 4, ".TMP") != 0) return;
  strcpy(u->names[u->found], name);
  u->sizes[u->found] = size;
  ++u->found;
}

// K лексикографически наименьших .SENT за один проход: старые первыми.
constexpr int CLEANUP_BATCH = 32;

struct SentBatch {
  char names[CLEANUP_BATCH][PATH_MAX_LEN];
  uint32_t sizes[CLEANUP_BATCH];
  int count = 0;
};

inline void sentBatchCb(const char *name, uint32_t size, void *ctx) {
  SentBatch *b = static_cast<SentBatch *>(ctx);
  if (!uploadpolicy::isSentPart(name) || strlen(name) >= PATH_MAX_LEN - 1) return;
  // Вставка с сохранением порядка; при полном наборе самое новое выпадает.
  int pos = b->count;
  while (pos > 0 && strcmp(name, b->names[pos - 1]) < 0) --pos;
  if (pos >= CLEANUP_BATCH) return;
  const int last = b->count < CLEANUP_BATCH ? b->count : CLEANUP_BATCH - 1;
  for (int i = last; i > pos; --i) {
    strcpy(b->names[i], b->names[i - 1]);
    b->sizes[i] = b->sizes[i - 1];
  }
  strcpy(b->names[pos], name);
  b->sizes[pos] = size;
  if (b->count < CLEANUP_BATCH) ++b->count;
}

}  // namespace detail

// Номер следующей сессии: больше и того, что лежит на карте, и того, что уже
// выдавалось (NVS). Иначе после автоудаления старых .SENT номер повторился бы,
// а сервер ключует части по (устройство, сессия, часть) и ответил бы 409.
// Ноль означает, что номера кончились: 9999 сессий записаны, карту пора чистить.
inline uint16_t nextSessionId(uint16_t issued, uint16_t onCard) {
  const uint16_t highest = issued > onCard ? issued : onCard;
  if (highest >= MAX_SESSIONS) return 0;
  return static_cast<uint16_t>(highest + 1);
}

// Наибольший номер сессии на карте. Один проход вместо 9999 exists().
inline uint16_t maxSessionOnCard(Storage &fs) {
  detail::MaxSession m;
  fs.listRoot(detail::maxSessionCb, &m);
  return m.value;
}

// Оставшийся .TMP значит, что питание пропало во время записи: заголовок
// заявляет ноль байт. Чиним заголовок по реальному размеру и переименовываем
// в _R.WAV. Выживает только то, что дошло до карты. Возвращает число
// восстановленных частей.
//
// Формат — из заголовка самого файла, а не из профиля собранной платы: карта
// из моно-прототипа восстанавливается и в стерео-прошивке, и обрезка до целых
// кадров идёт по её собственному размеру кадра.
inline int recoverUnfinished(Storage &fs, LogFn log) {
  detail::Unfinished u;
  fs.listRoot(detail::unfinishedCb, &u);  // список сначала: менять каталог во время обхода нельзя
  if (u.found == 0) {
    say(log, "[recover] No unfinished parts.");
    return 0;
  }
  int recovered = 0;
  for (int i = 0; i < u.found; ++i) {
    char tmpPath[PATH_MAX_LEN + 1];
    snprintf(tmpPath, sizeof(tmpPath), "/%s", u.names[i]);

    StorageFile *f = fs.open(tmpPath, Storage::UPDATE);  // WRITE обнулил бы файл
    if (!f) {
      char line[96];
      snprintf(line, sizeof(line), "[recover] ERROR: cannot reopen %s", tmpPath);
      say(log, line);
      continue;
    }
    uint8_t h[44];
    uint32_t declared = 0;
    audio::Format fmt = audio::MONO_16K;
    const bool ours = f->seek(0) && f->read(h, 44) == 44 && audio::parseWavHeader(h, &fmt, &declared);
    const uint32_t raw = u.sizes[i] > audio::WAV_HEADER_BYTES ? u.sizes[i] - audio::WAV_HEADER_BYTES : 0;
    const uint32_t audioBytes = ours ? fmt.wholeFrames(raw) : 0;
    const bool ok = ours && writeWavHeader(f, fmt, audioBytes);
    f->flush();
    fs.close(f);

    char line[160];  // два пути по 35 байт, размер, каналы: до 131 байта
    if (!ok) {
      snprintf(line, sizeof(line), "[recover] SKIP %s (%s)", tmpPath,
               ours ? "header rewrite failed" : "not our PCM format");
      say(log, line);
      continue;
    }
    char recoveredPath[PATH_MAX_LEN + 3];
    snprintf(recoveredPath, sizeof(recoveredPath), "%.*s_R.WAV",
             static_cast<int>(strlen(tmpPath) - 4), tmpPath);
    const bool renamed = fs.rename(tmpPath, recoveredPath);
    snprintf(line, sizeof(line), "[recover] %s -> %s | %lu audio bytes | %u ch | renamed=%s", tmpPath,
             recoveredPath, static_cast<unsigned long>(audioBytes), fmt.channels, renamed ? "yes" : "NO");
    say(log, line);
    if (renamed) ++recovered;
  }
  return recovered;
}

struct CleanupResult {
  int deleted = 0;
  uint64_t freeBytes = 0;
  bool cardLow = false;   // места меньше порога, а удалять уже нечего
};

// Удаляет только .SENT (и их .MRK), старые первыми, и только когда свободного
// места меньше minFree — до targetFree. Работает пачками, пока цель не
// достигнута или подтверждённых частей не осталось: ограниченная пачка не
// может объявить «удалять нечего», пока на карте ещё есть .SENT.
// Всё неподтверждённое (.WAV .TMP .BAD .CONFLICT .REJECT .END) не трогается.
inline CleanupResult cleanupSent(Storage &fs, uint64_t minFree, uint64_t targetFree, LogFn log) {
  CleanupResult r;
  const uint64_t total = fs.totalBytes();
  const uint64_t used = fs.usedBytes();
  r.freeBytes = total > used ? total - used : 0;
  char line[192];
  if (r.freeBytes >= minFree) {
    snprintf(line, sizeof(line), "[clean] Свободно %llu МБ, удалять не нужно.",
             static_cast<unsigned long long>(r.freeBytes >> 20));
    say(log, line);
    return r;
  }

  while (r.freeBytes < targetFree) {
    detail::SentBatch batch;
    fs.listRoot(detail::sentBatchCb, &batch);
    if (batch.count == 0) break;
    // Пачка берётся заново каждый проход, поэтому неудаляемый файл попадал бы
    // в неё снова и снова. Проход без единого удаления — конец очистки, а не
    // повод крутиться вечно: очистка идёт при загрузке, до перехода в ARMED.
    const int deletedBefore = r.deleted;
    for (int i = 0; i < batch.count && r.freeBytes < targetFree; ++i) {
      char path[PATH_MAX_LEN + 1];
      snprintf(path, sizeof(path), "/%s", batch.names[i]);
      if (!fs.remove(path)) {
        snprintf(line, sizeof(line), "[clean] WARNING: cannot remove %s", path);
        say(log, line);
        continue;
      }
      r.freeBytes += batch.sizes[i];
      ++r.deleted;
      char mrk[PATH_MAX_LEN];
      uploadpolicy::marksNameFor(batch.names[i], mrk, sizeof(mrk));
      char mrkPath[PATH_MAX_LEN + 1];
      snprintf(mrkPath, sizeof(mrkPath), "/%s", mrk);
      if (fs.exists(mrkPath)) fs.remove(mrkPath);
      snprintf(line, sizeof(line), "[clean] Удалено %s", batch.names[i]);
      say(log, line);
    }
    if (r.deleted == deletedBefore) {
      say(log, "[clean] Ни один файл не удалился: очистка остановлена.");
      break;
    }
  }

  r.cardLow = r.freeBytes < minFree;
  snprintf(line, sizeof(line), "[clean] Удалено %d, свободно %llu МБ%s", r.deleted,
           static_cast<unsigned long long>(r.freeBytes >> 20),
           r.cardLow ? " — МАЛО, подтверждённых частей для удаления нет" : "");
  say(log, line);
  return r;
}

}  // namespace session
