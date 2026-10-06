// Кольцевой буфер PCM для предзаписи и сглаживания записи.
//
// Один писатель (задача захвата микрофона) и один читатель (главный цикл,
// пишущий на SD). Без блокировок: писатель двигает только head, читатель —
// только tail; оба счётчика монотонные 32-битные, позиция в памяти считается
// маской, поэтому ёмкость обязана быть степенью двойки, и переполнение
// счётчика через 4 ГБ (37 часов звука) ничего не ломает.
//
// Политика при переполнении — затирать самое старое. В состоянии ARMED это и
// есть замысел: буфер всегда хранит последние N секунд. В RECORDING затёртое
// означает, что SD не успевала, и читатель считает это потерей (dropped).
//
// Заголовок не зависит от Arduino и проверяется на хосте: host_tests/.
#pragma once

#include <atomic>
#include <stdint.h>
#include <string.h>

class RingBuffer {
 public:
  // storage выделяется заранее (PSRAM), сюда передаётся указатель. capacity —
  // степень двойки. Возвращает false при неверных аргументах.
  bool init(uint8_t *storage, uint32_t capacity) {
    if (storage == nullptr || capacity == 0 || (capacity & (capacity - 1)) != 0) {
      return false;
    }
    m_buf = storage;
    m_cap = capacity;
    m_mask = capacity - 1;
    m_head.store(0, std::memory_order_relaxed);
    m_tail.store(0, std::memory_order_relaxed);
    m_dropped = 0;
    m_hwm = 0;
    return true;
  }

  uint32_t capacity() const { return m_cap; }

  // --- писатель ---------------------------------------------------------

  // head двигается на все n байт, даже когда влезает только хвост: head —
  // это счётчик произведённого звука, а не записанного, и именно по разнице
  // с tail читатель узнаёт, сколько байт затёрто.
  void push(const uint8_t *data, uint32_t n) {
    const uint32_t h = m_head.load(std::memory_order_relaxed);
    const uint32_t end = h + n;
    uint32_t kept = n;
    if (kept > m_cap) {
      data += kept - m_cap;
      kept = m_cap;
    }
    const uint32_t pos = (end - kept) & m_mask;
    uint32_t first = m_cap - pos;
    if (first > kept) {
      first = kept;
    }
    memcpy(m_buf + pos, data, first);
    if (kept > first) {
      memcpy(m_buf, data + first, kept - first);
    }
    m_head.store(end, std::memory_order_release);

    uint32_t fill = end - m_tail.load(std::memory_order_relaxed);
    if (fill > m_cap) {
      fill = m_cap;
    }
    if (fill > m_hwm) {
      m_hwm = fill;
    }
  }

  // --- читатель ---------------------------------------------------------

  // Сколько байт можно прочитать. Не больше ёмкости: остальное уже затёрто.
  uint32_t available() const {
    const uint32_t d = m_head.load(std::memory_order_acquire) -
                       m_tail.load(std::memory_order_relaxed);
    return d > m_cap ? m_cap : d;
  }

  // Переставляет tail на самый старый уцелевший байт, не считая затёртое
  // потерей. Вызывается при START: всё, что буфер держит, — предзапись.
  // keep — сколько самых свежих байт оставить. При START буфер полон до края,
  // и первый же кадр микрофона, пришедший раньше первой записи на карту,
  // затирал бы начало предзаписи и считался потерей (на плате: 512 кадров).
  void resync(uint32_t keep = UINT32_MAX) {
    if (keep > m_cap) {
      keep = m_cap;
    }
    const uint32_t h = m_head.load(std::memory_order_acquire);
    const uint32_t t = m_tail.load(std::memory_order_relaxed);
    if (h - t > keep) {
      m_tail.store(h - keep, std::memory_order_release);
    }
  }

  void discardAll() {
    m_tail.store(m_head.load(std::memory_order_acquire), std::memory_order_release);
  }

  // Копирует до max самых старых байт, возвращает сколько скопировано.
  // Если писатель затёр читаемое прямо во время копирования, кусок считается
  // потерянным, а читатель перескакивает к новому самому старому байту.
  uint32_t pop(uint8_t *dst, uint32_t max) {
    for (int attempt = 0; attempt < 4; ++attempt) {
      const uint32_t h = m_head.load(std::memory_order_acquire);
      uint32_t t = m_tail.load(std::memory_order_relaxed);
      if (h - t > m_cap) {
        m_dropped += (h - t) - m_cap;
        t = h - m_cap;
      }
      uint32_t n = h - t;
      if (n > max) {
        n = max;
      }
      if (n == 0) {
        m_tail.store(t, std::memory_order_release);
        return 0;
      }
      const uint32_t pos = t & m_mask;
      uint32_t first = m_cap - pos;
      if (first > n) {
        first = n;
      }
      memcpy(dst, m_buf + pos, first);
      if (n > first) {
        memcpy(dst + first, m_buf, n - first);
      }
      // Пока копировали, писатель мог уйти дальше ёмкости от начала куска:
      // тогда прочитанное частично свежее, чем должно быть. Повторяем.
      const uint32_t h2 = m_head.load(std::memory_order_acquire);
      if (h2 - t > m_cap) {
        m_tail.store(t, std::memory_order_release);
        continue;
      }
      m_tail.store(t + n, std::memory_order_release);
      return n;
    }
    return 0;
  }

  // --- диагностика --------------------------------------------------------

  uint32_t head() const { return m_head.load(std::memory_order_acquire); }
  uint32_t tail() const { return m_tail.load(std::memory_order_relaxed); }
  // Байты, затёртые до того, как читатель их забрал (считаются в pop).
  uint32_t dropped() const { return m_dropped; }
  // Наибольшее заполнение, байт. Пишет писатель, читает диагностика: гонка
  // безвредна, число только для отчёта.
  uint32_t highWatermark() const { return m_hwm; }
  void resetStats() {
    m_dropped = 0;
    m_hwm = 0;
  }

#ifdef RING_BUFFER_TESTING
  // Только для хост-тестов: поставить счётчики к краю 32 бит, чтобы проверить
  // переполнение за несколько операций, а не за 2^28 push().
  void testSetCounters(uint32_t head, uint32_t tail) {
    m_head.store(head, std::memory_order_relaxed);
    m_tail.store(tail, std::memory_order_relaxed);
  }
#endif

 private:
  uint8_t *m_buf = nullptr;
  uint32_t m_cap = 0;
  uint32_t m_mask = 0;
  std::atomic<uint32_t> m_head{0};
  std::atomic<uint32_t> m_tail{0};
  uint32_t m_dropped = 0;
  volatile uint32_t m_hwm = 0;
};
