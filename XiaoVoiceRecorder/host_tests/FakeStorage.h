// Хранилище в памяти для хост-тестов жизненного цикла записи.
// Умеет отказывать по команде: не переименовать, оборвать запись на N-м байте,
// не открыть файл, провалить перезапись заголовка. Именно такие отказы карта
// устраивает в жизни, и именно их нельзя устроить на настоящей плате по заказу.
#pragma once

#include <map>
#include <set>
#include <string>
#include <vector>

#include "../Storage.h"

class FakeStorage;

class FakeFile : public StorageFile {
 public:
  FakeFile(FakeStorage &fs, const std::string &path, bool append)
      : m_fs(fs), m_path(path) {
    if (append) m_pos = data().size();
  }
  size_t write(const uint8_t *src, size_t n) override;
  int read(uint8_t *dst, size_t n) override {
    std::vector<uint8_t> &d = data();
    if (m_pos >= d.size()) return 0;
    size_t take = d.size() - m_pos;
    if (take > n) take = n;
    memcpy(dst, d.data() + m_pos, take);
    m_pos += take;
    return static_cast<int>(take);
  }
  bool seek(uint32_t pos) override {
    if (pos > data().size()) return false;
    m_pos = pos;
    return true;
  }
  uint32_t size() override { return static_cast<uint32_t>(data().size()); }
  void flush() override {}

 private:
  std::vector<uint8_t> &data();
  FakeStorage &m_fs;
  std::string m_path;
  size_t m_pos = 0;
};

class FakeStorage : public Storage {
 public:
  std::map<std::string, std::vector<uint8_t>> files;
  std::set<std::string> failRename;      // from-пути, которые не переименуются
  std::set<std::string> failOpen;        // пути, которые не откроются
  std::map<std::string, size_t> failWriteAfter;  // путь -> предел байт, дальше запись обрывается
  int failWritesAtZeroFrom = 0;          // с какого по счёту write() в позицию 0 отказывать (0 = никогда)
  int writesAtZero = 0;
  uint64_t total = 0;
  uint64_t usedByOthers = 0;             // «чужие» байты на карте помимо наших файлов
  std::vector<std::string> log;
  int openFiles = 0;

  StorageFile *open(const char *path, Mode mode) override {
    const std::string p(path);
    if (failOpen.count(p)) return nullptr;
    const bool exists = files.count(p) > 0;
    if (mode == READ || mode == UPDATE) {
      if (!exists) return nullptr;
    } else if (mode == WRITE) {
      files[p].clear();
    } else if (mode == APPEND && !exists) {
      files[p];
    }
    ++openFiles;
    return new FakeFile(*this, p, mode == APPEND);
  }
  void close(StorageFile *f) override {
    --openFiles;
    delete f;
  }
  bool exists(const char *path) override { return files.count(path) > 0; }
  bool rename(const char *from, const char *to) override {
    if (failRename.count(from) || !files.count(from) || files.count(to)) return false;
    files[to] = files[from];
    files.erase(from);
    return true;
  }
  bool remove(const char *path) override { return files.erase(path) > 0; }
  void listRoot(ListFn fn, void *ctx) override {
    for (auto &entry : files) {
      fn(entry.first.c_str() + 1, static_cast<uint32_t>(entry.second.size()), ctx);
    }
  }
  uint64_t totalBytes() override { return total; }
  uint64_t usedBytes() override {
    uint64_t used = usedByOthers;
    for (auto &entry : files) used += entry.second.size();
    return used;
  }

  bool has(const std::string &p) const { return files.count(p) > 0; }
  size_t sizeOf(const std::string &p) const {
    auto it = files.find(p);
    return it == files.end() ? 0 : it->second.size();
  }
};

inline std::vector<uint8_t> &FakeFile::data() { return m_fs.files[m_path]; }

inline size_t FakeFile::write(const uint8_t *src, size_t n) {
  std::vector<uint8_t> &d = data();
  if (m_pos == 0) {
    ++m_fs.writesAtZero;
    if (m_fs.failWritesAtZeroFrom && m_fs.writesAtZero >= m_fs.failWritesAtZeroFrom) return 0;
  }
  auto limit = m_fs.failWriteAfter.find(m_path);
  if (limit != m_fs.failWriteAfter.end() && m_pos + n > limit->second) {
    n = limit->second > m_pos ? limit->second - m_pos : 0;
  }
  if (d.size() < m_pos + n) d.resize(m_pos + n);
  memcpy(d.data() + m_pos, src, n);
  m_pos += n;
  return n;
}
