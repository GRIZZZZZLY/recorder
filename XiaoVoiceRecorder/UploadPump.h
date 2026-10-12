// Cooperative body pump shared by the firmware and deterministic host tests.
// A budget can only yield BETWEEN blocking SD/TLS calls, never interrupt one.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace uploadpump {
struct Timing {
  uint64_t us = 0;
  uint64_t maxUs = 0;
  uint32_t calls = 0;
  void add(uint64_t elapsed) {
    us += elapsed;
    if (elapsed > maxUs) maxUs = elapsed;
    ++calls;
  }
};

struct Stats {
  Timing read;
  Timing write;
  Timing step;
  uint32_t accepted = 0;  // accepted by client.write, NOT acknowledged by server
};

enum Result { MORE, DONE, READ_ERROR, WRITE_ERROR };

// Clock returns monotonic microseconds. pending() only observes button events;
// it must not dispatch them (dispatch can close the file/client reentrantly).
template <typename File, typename Client, typename Clock, typename Pending>
Result sendStep(File &file, Client &client, uint8_t *buffer, size_t capacity,
                uint32_t total, uint32_t &sent, unsigned chunks, uint64_t budgetUs,
                Stats &stats, Clock clock, Pending pending) {
  const uint64_t started = clock();
  Result result = MORE;
  for (unsigned i = 0; i < chunks && sent < total; ++i) {
    if (pending() || (i > 0 && clock() - started >= budgetUs)) break;
    const size_t want = total - sent < capacity ? total - sent : capacity;
    uint64_t at = clock();
    const int got = file.read(buffer, want);
    stats.read.add(clock() - at);
    if (got <= 0 || static_cast<size_t>(got) > want) {
      result = READ_ERROR;
      break;
    }
    at = clock();
    const size_t written = client.write(buffer, static_cast<size_t>(got));
    stats.write.add(clock() - at);
    if (written <= static_cast<size_t>(got)) {
      sent += written;
      stats.accepted += written;
    }
    if (written != static_cast<size_t>(got)) {
      // File position may be ahead of sent. Close and re-probe next attempt;
      // never retry the whole buffer on this connection or trust sent as offset.
      result = WRITE_ERROR;
      break;
    }
  }
  stats.step.add(clock() - started);
  return result != MORE ? result : sent >= total ? DONE : MORE;
}

// Threshold crossing, not modulo: a resumed offset need not be 4-KiB aligned,
// and SD is allowed to return positive short reads.
inline bool progressDue(uint32_t accepted, uint32_t &lastReported) {
  if (accepted - lastReported < (1UL << 20)) return false;
  lastReported = accepted;
  return true;
}

inline uint64_t kibPerSecond(uint32_t bytes, uint64_t elapsedUs) {
  return elapsedUs ? static_cast<uint64_t>(bytes) * 1000000ULL / 1024 / elapsedUs : 0;
}
}  // namespace uploadpump
