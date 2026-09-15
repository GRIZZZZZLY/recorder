// Storage.h поверх Arduino SD. Единственное место, где жизненный цикл записи
// встречается с настоящей картой; всё остальное в Session.h проверяется на
// хосте с хранилищем в памяти.
//
// Файлы выдаются из фиксированного пула, а не с кучи: у ESP32 куча дробится,
// а открытых файлов одновременно нужно два-три (часть, метки, восстановление).
#pragma once

#include <FS.h>
#include <SD.h>

#include "Storage.h"

class SdFile : public StorageFile {
 public:
  File handle;
  bool busy = false;

  size_t write(const uint8_t *data, size_t n) override { return handle.write(data, n); }
  int read(uint8_t *dst, size_t n) override { return handle.read(dst, n); }
  bool seek(uint32_t pos) override { return handle.seek(pos); }
  uint32_t size() override { return static_cast<uint32_t>(handle.size()); }
  void flush() override { handle.flush(); }
};

class SdStorage : public Storage {
 public:
  static constexpr int POOL = 4;

  StorageFile *open(const char *path, Mode mode) override {
    SdFile *slot = nullptr;
    for (SdFile &f : m_pool) {
      if (!f.busy) {
        slot = &f;
        break;
      }
    }
    if (!slot) return nullptr;
    const char *flags = mode == READ ? "r" : mode == WRITE ? "w" : mode == APPEND ? "a" : "r+";
    slot->handle = SD.open(path, flags);
    if (!slot->handle) return nullptr;
    slot->busy = true;
    return slot;
  }

  void close(StorageFile *file) override {
    SdFile *f = static_cast<SdFile *>(file);
    f->handle.close();
    f->busy = false;
  }

  bool exists(const char *path) override { return SD.exists(path); }
  bool rename(const char *from, const char *to) override { return SD.rename(from, to); }
  bool remove(const char *path) override { return SD.remove(path); }

  // File::name() на одних ядрах даёт имя, на других — полный путь.
  void listRoot(ListFn fn, void *ctx) override {
    File root = SD.open("/");
    if (!root || !root.isDirectory()) return;
    for (File f = root.openNextFile(); f; f = root.openNextFile()) {
      if (!f.isDirectory()) {
        const char *name = f.name();
        const char *slash = strrchr(name, '/');
        fn(slash ? slash + 1 : name, static_cast<uint32_t>(f.size()), ctx);
      }
      f.close();
    }
    root.close();
  }

  // На FAT32 usedBytes() читает таблицу целиком: секунды на 32 ГБ, не измерено.
  uint64_t totalBytes() override { return SD.totalBytes(); }
  uint64_t usedBytes() override { return SD.usedBytes(); }

 private:
  SdFile m_pool[POOL];
};
