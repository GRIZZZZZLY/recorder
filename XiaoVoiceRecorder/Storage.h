// Интерфейс хранилища: ровно то, что нужно жизненному циклу записи.
//
// Прошивка реализует его поверх Arduino SD (SdStorage.h), хост-тесты — в
// памяти (host_tests/FakeStorage.h) с намеренными отказами: не удалось
// переименовать, запись оборвалась на N-м байте, файл не открылся. Так самый
// опасный код — закрытие части, .END, восстановление, очистка — проверяется
// без карты и без платы.
//
// Без Arduino, без heap внутри интерфейса: файлы выдаются из пула реализации.
#pragma once

#include <stddef.h>
#include <stdint.h>

class StorageFile {
 public:
  virtual ~StorageFile() {}
  virtual size_t write(const uint8_t *data, size_t n) = 0;
  // Байт прочитано; 0 в конце файла, отрицательное — ошибка.
  virtual int read(uint8_t *dst, size_t n) = 0;
  virtual bool seek(uint32_t pos) = 0;
  virtual uint32_t size() = 0;
  virtual void flush() = 0;
};

class Storage {
 public:
  enum Mode {
    READ,        // "r"
    WRITE,       // "w": создать или обнулить
    APPEND,      // "a"
    UPDATE,      // "r+": читать и писать, не обнуляя
  };

  // Имя записи в корне: имя без слэша и размер в байтах.
  typedef void (*ListFn)(const char *name, uint32_t size, void *ctx);

  virtual ~Storage() {}
  // nullptr — не открылось. Полученный файл обязательно вернуть через close().
  virtual StorageFile *open(const char *path, Mode mode) = 0;
  virtual void close(StorageFile *file) = 0;
  virtual bool exists(const char *path) = 0;
  virtual bool rename(const char *from, const char *to) = 0;
  virtual bool remove(const char *path) = 0;
  virtual void listRoot(ListFn fn, void *ctx) = 0;
  virtual uint64_t totalBytes() = 0;
  virtual uint64_t usedBytes() = 0;
};

// Часы и журнал тоже подменяются на хосте.
typedef uint32_t (*ClockFn)();
typedef void (*LogFn)(const char *line);
