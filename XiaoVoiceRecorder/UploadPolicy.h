// Правила отправки без железа: имена файлов, разбор адреса сервера, классы
// ответов HTTP, расписание повторов, заголовок X-Marks из .MRK, что можно
// удалять с карты. Всё чистые функции, проверяются на хосте: host_tests/.
//
// Arduino вставляет автоматические прототипы в начало объединённого файла
// раньше тел .ino, поэтому тип SendResult живёт здесь и подключается из
// главного скетча — иначе прототип sendPart не компилируется.
#pragma once

#include <stdint.h>
#include <stdio.h>
#include <string.h>

// Итог одной попытки отправки части.
enum SendResult {
  SEND_OK,            // сервер подтвердил размер и контрольную сумму
  SEND_RETRY,         // временная помеха: сеть, таймаут, 5xx, 422, искажение
  SEND_CONFLICT,      // 409: у сервера эта часть с другими данными; файл -> .CONFLICT
  SEND_PART_FATAL,    // 400/413/битое имя: виновата сама часть; файл -> .REJECT
  SEND_CONFIG_FATAL,  // 401, кривой server_url: очередь стоит до смены настроек
};

namespace uploadpolicy {

// Классификация ответа сервера. Статус <= 0 — сеть или таймаут.
inline SendResult classifyStatus(int status) {
  if (status == 200 || status == 201) return SEND_OK;
  if (status == 401) return SEND_CONFIG_FATAL;
  if (status == 409) return SEND_CONFLICT;
  if (status == 400 || status == 413) return SEND_PART_FATAL;
  return SEND_RETRY;  // 422, 429, 5xx, обрыв
}

// Пауза перед следующей попыткой по числу подряд неудач: 1, 5, 15, 60, 60...
// минут. Сброс — при первой удачной части.
inline uint32_t backoffMs(uint32_t consecutiveFailures) {
  static const uint32_t minutes[] = {1, 5, 15, 60};
  const uint32_t idx = consecutiveFailures == 0 ? 0 : consecutiveFailures - 1;
  const uint32_t m = minutes[idx < 3 ? idx : 3];
  return m * 60UL * 1000UL;
}

// REC0007_P01.WAV -> сессия 7, часть 1. Восстановленная несёт _R: REC0007_P01_R.WAV
inline bool parsePartName(const char *name, uint16_t *session, uint16_t *part,
                          bool *recovered) {
  const size_t len = strlen(name);
  if (len != 15 && len != 17) return false;  // REC0007_P01.WAV | REC0007_P01_R.WAV
  if (strncmp(name, "REC", 3) != 0 || name[7] != '_' || name[8] != 'P') return false;
  if (strcmp(name + len - 4, ".WAV") != 0) return false;
  for (int i = 3; i < 7; ++i) if (name[i] < '0' || name[i] > '9') return false;
  for (int i = 9; i < 11; ++i) if (name[i] < '0' || name[i] > '9') return false;
  const bool rec = (len == 17);
  if (rec && (name[11] != '_' || name[12] != 'R')) return false;
  const unsigned s = (name[3] - '0') * 1000 + (name[4] - '0') * 100 +
                     (name[5] - '0') * 10 + (name[6] - '0');
  const unsigned p = (name[9] - '0') * 10 + (name[10] - '0');
  if (s < 1 || p < 1) return false;
  *session = static_cast<uint16_t>(s);
  *part = static_cast<uint16_t>(p);
  *recovered = rec;
  return true;
}

// REC0007_N08.END -> сессия 7 закрыта чисто с 8 частями.
inline bool parseEndName(const char *name, uint16_t *session, uint16_t *parts) {
  if (strlen(name) != 15) return false;
  if (strncmp(name, "REC", 3) != 0 || name[7] != '_' || name[8] != 'N') return false;
  if (strcmp(name + 11, ".END") != 0) return false;
  for (int i = 3; i < 7; ++i) if (name[i] < '0' || name[i] > '9') return false;
  for (int i = 9; i < 11; ++i) if (name[i] < '0' || name[i] > '9') return false;
  const unsigned s = (name[3] - '0') * 1000 + (name[4] - '0') * 100 +
                     (name[5] - '0') * 10 + (name[6] - '0');
  const unsigned p = (name[9] - '0') * 10 + (name[10] - '0');
  if (s < 1 || p < 1) return false;
  *session = static_cast<uint16_t>(s);
  *parts = static_cast<uint16_t>(p);
  return true;
}

// https://host[:port][/prefix]  ->  host, port (443 по умолчанию), prefix без
// хвостового слэша. Только https: сертификат сервера проверяется по вшитому
// корню, открытый http контрактом не предусмотрен.
inline bool parseServerUrl(const char *url, char *host, size_t hostSize,
                           uint16_t *port, char *prefix, size_t prefixSize) {
  const char *scheme = "https://";
  if (strncmp(url, scheme, strlen(scheme)) != 0) return false;
  const char *p = url + strlen(scheme);
  const char *slash = strchr(p, '/');
  const char *hostEnd = slash ? slash : p + strlen(p);
  const char *colon = static_cast<const char *>(memchr(p, ':', hostEnd - p));
  const char *nameEnd = colon ? colon : hostEnd;
  const size_t nameLen = nameEnd - p;
  if (nameLen == 0 || nameLen >= hostSize) return false;
  memcpy(host, p, nameLen);
  host[nameLen] = '\0';

  unsigned portValue = 443;
  if (colon) {
    portValue = 0;
    for (const char *c = colon + 1; c < hostEnd; ++c) {
      if (*c < '0' || *c > '9') return false;
      portValue = portValue * 10 + (*c - '0');
      if (portValue > 65535) return false;
    }
    if (portValue == 0) return false;
  }
  *port = static_cast<uint16_t>(portValue);

  size_t prefixLen = slash ? strlen(slash) : 0;
  while (prefixLen > 0 && slash[prefixLen - 1] == '/') --prefixLen;
  if (prefixLen >= prefixSize) return false;
  if (prefixLen) memcpy(prefix, slash, prefixLen);
  prefix[prefixLen] = '\0';
  return true;
}

// Строка .MRK: цифры с необязательной дробной частью. Обрывок после потери
// питания ("12." или "") отбрасывается, чтобы сервер не отверг всю часть.
inline bool isMarkToken(const char *s, size_t len) {
  if (len == 0) return false;
  size_t i = 0;
  while (i < len && s[i] >= '0' && s[i] <= '9') ++i;
  if (i == 0) return false;
  if (i == len) return true;
  if (s[i] != '.') return false;
  const size_t dot = i++;
  while (i < len && s[i] >= '0' && s[i] <= '9') ++i;
  return i == len && i > dot + 1;
}

constexpr int MAX_MARKS_PER_PART = 200;  // предел сервера, shared/protocol.md

// Содержимое .MRK (по метке на строку) -> значение X-Marks через запятую.
// Возвращает число меток; 0 — заголовок не нужен.
inline int marksHeader(const char *text, char *out, size_t outSize) {
  int count = 0;
  size_t used = 0;
  out[0] = '\0';
  const char *p = text;
  while (*p && count < MAX_MARKS_PER_PART) {
    const char *end = p;
    while (*end && *end != '\n' && *end != '\r') ++end;
    const size_t len = end - p;
    if (isMarkToken(p, len)) {
      const size_t need = len + (count ? 1 : 0);
      if (used + need >= outSize) break;
      if (count) out[used++] = ',';
      memcpy(out + used, p, len);
      used += len;
      out[used] = '\0';
      ++count;
    }
    p = end;
    while (*p == '\n' || *p == '\r') ++p;
  }
  return count;
}

// Автоудаление касается только подтверждённых сервером частей.
// Восстановленная часть тоже подтверждается: REC0007_P01.SENT и REC0007_P01_R.SENT.
inline bool isSentPart(const char *name) {
  const size_t len = strlen(name);
  if (strncmp(name, "REC", 3) != 0 || len < 9 || name[7] != '_' || name[8] != 'P') return false;
  if (len == 16) return strcmp(name + 11, ".SENT") == 0;
  if (len == 18) return name[11] == '_' && name[12] == 'R' && strcmp(name + 13, ".SENT") == 0;
  return false;
}

// Рядом с частью лежит файл меток; уходит вместе с ней.
// Метки пишутся во время записи как REC0007_P01.MRK и не знают, что часть
// потом восстановят как _R. Поэтому суффикс _R здесь отбрасывается: иначе
// метки восстановленной части терялись бы при отправке и оставались при очистке.
inline void marksNameFor(const char *partName, char *out, size_t outSize) {
  const char *dot = strrchr(partName, '.');
  size_t stem = dot ? static_cast<size_t>(dot - partName) : strlen(partName);
  if (stem >= 2 && partName[stem - 2] == '_' && partName[stem - 1] == 'R') stem -= 2;
  if (stem + 5 >= outSize) {
    out[0] = '\0';
    return;
  }
  memcpy(out, partName, stem);
  memcpy(out + stem, ".MRK", 5);
}

}  // namespace uploadpolicy
