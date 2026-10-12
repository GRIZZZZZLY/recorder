#include "../UploadPump.h"
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>

using namespace uploadpump;
struct Harness {
  uint64_t now = 0;
  uint64_t readUs = 100, writeUs = 50000;
  uint32_t sent = 0;
  bool pending = false;
  Stats stats;
  uint8_t buffer[4096];
  struct File {
    Harness &h;
    std::vector<uint8_t> data;
    size_t pos = 0, maxRead = 4096;
    int fail = 0;
    int read(uint8_t *dst, size_t n) {
      h.now += h.readUs;
      if (fail) return fail;
      n = std::min({n, maxRead, data.size() - pos});
      std::memcpy(dst, data.data() + pos, n);
      pos += n;
      return static_cast<int>(n);
    }
  } file{*this, {}};
  struct Client {
    Harness &h;
    std::vector<uint8_t> data;
    size_t maxWrite = 4096;
    bool edgeOnWrite = false;
    size_t write(const uint8_t *src, size_t n) {
      h.now += h.writeUs;
      n = std::min(n, maxWrite);
      data.insert(data.end(), src, src + n);
      if (edgeOnWrite) h.pending = true;
      return n;
    }
  } client{*this, {}};
  explicit Harness(size_t size = 19200044, uint32_t offset = 0) {
    file.data.resize(size);
    for (size_t i = 0; i < size; ++i) file.data[i] = (i * 13 + i / 256) % 251;
    file.pos = sent = offset;
  }
  Result step(unsigned chunks = 8, uint64_t budget = 25000) {
    return sendStep(file, client, buffer, sizeof(buffer), file.data.size(), sent,
                    chunks, budget, stats, [this] { return now; }, [this] { return pending; });
  }
};

int main() {
  // Real-size part, non-aligned resume, small final block: byte-exact stream.
  for (uint32_t offset : {0u, 1u, 40000u, 19200044u}) {
    Harness h(19200044, offset);
    while (h.step() == MORE) {}
    assert(h.sent == h.file.data.size());
    assert(h.client.data == std::vector<uint8_t>(h.file.data.begin() + offset, h.file.data.end()));
    assert(h.stats.accepted == h.file.data.size() - offset);
    assert(h.stats.write.calls == (h.stats.accepted + 4095) / 4096);
  }
  // REC already queued => no read or write. REC during write => no next block.
  {
    Harness h;
    h.pending = true;
    assert(h.step() == MORE && h.file.pos == 0 && h.stats.write.calls == 0);
    h.pending = false;
    h.client.edgeOnWrite = true;
    h.writeUs = 100;
    h.step();
    assert(h.sent == 4096 && h.stats.write.calls == 1);
  }
  // Slow card consumes the budget too; positive short reads preserve contents.
  {
    Harness h(20000);
    h.file.maxRead = 123;
    h.readUs = 30000;
    h.writeUs = 1;
    h.step();
    assert(h.sent == 123 && h.stats.write.calls == 1);
    while (h.step() == MORE) {}
    assert(h.client.data == h.file.data);
  }
  // Zero/short socket write: abort, no second write, re-probe authoritative offset.
  for (size_t accepted : {0u, 700u}) {
    Harness h(20000);
    h.client.maxWrite = accepted;
    assert(h.step() == WRITE_ERROR);
    assert(h.sent == accepted && h.stats.accepted == accepted);
    assert(h.stats.write.calls == 1 && h.file.pos == 4096);
    // Some accepted bytes may not reach server: simulate only 1/2 stored.
    const uint32_t serverOffset = accepted / 2;
    Harness resumed(20000, serverOffset);
    while (resumed.step() == MORE) {}
    auto stored = std::vector<uint8_t>(h.file.data.begin(), h.file.data.begin() + serverOffset);
    stored.insert(stored.end(), resumed.client.data.begin(), resumed.client.data.end());
    assert(stored == h.file.data);
  }
  // Premature EOF and negative read must not send stale buffer data.
  {
    Harness h(5000);
    h.file.maxRead = 0;
    assert(h.step() == READ_ERROR && h.stats.write.calls == 0);
    h.file.fail = -1;
    assert(h.step() == READ_ERROR && h.sent == 0);
  }
  // Recording mode remains one block per turn even on a fast link.
  {
    Harness h;
    h.writeUs = 1;
    h.step(1, 10000);
    assert(h.stats.write.calls == 1);
  }
  std::puts("upload pump: byte accuracy, resume, faults, REC yielding and timing checks passed");
}
