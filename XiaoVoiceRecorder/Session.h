// Жизненный цикл записи и обслуживание карты — без Arduino.
//
//   session::Writer      START с предзаписью, слив буфера, ротация частей ровно
//                        по байтам, метки, STOP с .END только после чистого
//                        закрытия
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

#include "PartMath.h"
#include "RingBuffer.h"
#include "Storage.h"
#include "UploadPolicy.h"

namespace session {

constexpr size_t PATH_MAX_LEN = 32;
constexpr uint16_t MAX_SESSIONS = 9999;

// --- имена и заголовок WAV ------------------------------------------------------

// /REC0007_P03.WAV — сессия 7, часть 3.
inline void partPath(char *out, size_t n, uint16_t sessionId, uint16_t part, const char *ext) {
  snprintf(out, n, "/REC%04u_P%02u.%s", sessionId, part, ext);
}

// /REC0007_N08.END — сессия 7 закрыта чисто, в ней 8 частей.
inline void endPath(char *out, size_t n, uint16_t sessionId, uint16_t parts) {
  snprintf(out, n, "/REC%04u_N%02u.END", sessionId, parts);
}

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

inline uint16_t getLe16(const uint8_t *s) { return s[0] | (s[1] << 8); }

inline uint32_t getLe32(const uint8_t *s) {
  return static_cast<uint32_t>(s[0]) | (static_cast<uint32_t>(s[1]) << 8) |
         (static_cast<uint32_t>(s[2]) << 16) | (static_cast<uint32_t>(s[3]) << 24);
}

inline void buildWavHeader(uint8_t h[44], uint32_t pcmBytes) {
  using namespace partmath;
  memset(h, 0, 44);
  memcpy(h + 0, "RIFF", 4);
  putLe32(h + 4, 36 + pcmBytes);
  memcpy(h + 8, "WAVE", 4);
  memcpy(h + 12, "fmt ", 4);
  putLe32(h + 16, 16);
  putLe16(h + 20, 1);                       // PCM
  putLe16(h + 22, 1);                       // моно
  putLe32(h + 24, SAMPLE_RATE);
  putLe32(h + 28, BYTES_PER_SECOND);
  putLe16(h + 32, FRAME_BYTES);
  putLe16(h + 34, 16);
  memcpy(h + 36, "data", 4);
  putLe32(h + 40, pcmBytes);
}

// Принимает только файл, который писала эта прошивка.
inline bool parseOurWavHeader(const uint8_t h[44], uint32_t *pcmBytes) {
  using namespace partmath;
  if (memcmp(h, "RIFF", 4) || memcmp(h + 8, "WAVE", 4) || memcmp(h + 12, "fmt ", 4) ||
      memcmp(h + 36, "data", 4)) {
    return false;
  }
  if (getLe16(h + 20) != 1 || getLe16(h + 22) != 1 || getLe32(h + 24) != SAMPLE_RATE ||
      getLe16(h + 34) != 16) {
    return false;
  }
  *pcmBytes = getLe32(h + 40);
  return true;
}

inline bool writeWavHeader(StorageFile *f, uint32_t pcmBytes) {
  uint8_t h[44];
  buildWavHeader(h, pcmBytes);
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
  uint32_t written_samples = 0;
};

class Writer {
 public:
  struct Config {
    uint32_t partMaxBytes = partmath::PART_MAX_BYTES;
    uint32_t writeChunkBytes = 8192;   // не больше CHUNK_CAP
    int writesPerService = 4;
    uint32_t flushIntervalMs = 1000;
    uint16_t maxParts = partmath::MAX_PARTS;
  };
  static constexpr uint32_t CHUNK_CAP = 8192;

  Writer(Storage &fs, RingBuffer &ring, ClockFn now, LogFn log)
      : m_fs(fs), m_ring(ring), m_now(now), m_log(log) {}

  Config cfg;

  bool recording() const { return m_recording; }
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
    m_ring.resync();
    m_preRoll = m_ring.available();
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

  // Метка: секунды от начала своей части. Позиция «сейчас» — всё записанное за
  // сессию плюс то, что ещё в буфере и будет записано следом. За границей части
  // метка достаётся следующей — туда попадёт и сам звук. Части вращаются ровно
  // на partMaxBytes, поэтому номер части — чистая арифметика.
  // Имя файла меток не зависит от _R: восстановленная часть находит свои метки.
  bool mark() {
    if (!m_recording) return false;
    const uint32_t pos = m_sessionBytes + m_partBytes + m_ring.available();
    const uint16_t part = static_cast<uint16_t>(pos / cfg.partMaxBytes) + 1;
    const uint32_t offset = pos % cfg.partMaxBytes;

    char text[24];
    int len = partmath::formatMark(text, sizeof(text) - 1, offset);
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
    buildWavHeader(h, 0);
    if (m_file->write(h, 44) != 44) {
      say(m_log, "[rec] ERROR: cannot write WAV header.");
      m_fs.close(m_file);
      m_file = nullptr;
      return false;
    }
    m_partBytes = 0;
    m_lastFlushMs = m_now();
    return true;
  }

  // Только чистое закрытие даёт .WAV; всё остальное — .BAD, чтобы битая часть
  // никогда не выглядела готовой. Возвращает false и тогда, когда файл не
  // переименовался: он остался .TMP и будет восстановлен как _R при старте.
  bool closePart(bool healthy) {
    if (!m_file) return false;
    m_file->flush();
    const bool headerOk = writeWavHeader(m_file, m_partBytes);
    m_file->flush();
    m_fs.close(m_file);
    m_file = nullptr;

    const bool ok = healthy && headerOk;
    char finalPath[PATH_MAX_LEN];
    partPath(finalPath, sizeof(finalPath), m_session, m_part, ok ? "WAV" : "BAD");
    const bool renamed = m_fs.rename(m_path, finalPath);
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
    want = partmath::wholeFrames(want);
    if (want == 0) return true;
    const uint32_t got = m_ring.pop(m_chunk, want);
    if (got == 0) return true;

    const uint32_t t0 = m_now();
    const size_t written = m_file->write(m_chunk, got);
    const uint32_t dt = m_now() - t0;
    if (dt > m_stats.sd_write_max_ms) m_stats.sd_write_max_ms = dt;

    // Считаем то, что реально дошло до карты, чтобы заголовок описывал файл.
    const uint32_t kept = partmath::wholeFrames(static_cast<uint32_t>(written));
    m_partBytes += kept;
    m_stats.written_samples += kept / partmath::FRAME_BYTES;
    if (written != got) {
      ++m_stats.sd_write_errors;
      logf("[rec] ERROR: SD write incomplete (%u of %u bytes).",
           static_cast<unsigned>(written), static_cast<unsigned>(got));
      return false;
    }
    return true;
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
  uint32_t m_lastFlushMs = 0;
  char m_path[PATH_MAX_LEN] = {0};
  uint8_t m_chunk[CHUNK_CAP];
  WriterStats m_stats;
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
    uint32_t audio = u.sizes[i] > partmath::WAV_HEADER_BYTES ? u.sizes[i] - partmath::WAV_HEADER_BYTES : 0;
    audio = partmath::wholeFrames(audio);

    StorageFile *f = fs.open(tmpPath, Storage::UPDATE);  // WRITE обнулил бы файл
    if (!f) {
      char line[96];
      snprintf(line, sizeof(line), "[recover] ERROR: cannot reopen %s", tmpPath);
      say(log, line);
      continue;
    }
    uint8_t h[44];
    uint32_t declared = 0;
    const bool ours = f->seek(0) && f->read(h, 44) == 44 && parseOurWavHeader(h, &declared);
    const bool ok = ours && writeWavHeader(f, audio);
    f->flush();
    fs.close(f);

    char line[128];
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
    snprintf(line, sizeof(line), "[recover] %s -> %s | %lu audio bytes | renamed=%s", tmpPath,
             recoveredPath, static_cast<unsigned long>(audio), renamed ? "yes" : "NO");
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
