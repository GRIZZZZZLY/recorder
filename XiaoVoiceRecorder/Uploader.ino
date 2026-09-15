/*
 * Отправка готовых частей на личный сервер. Контракт: shared/protocol.md.
 *
 * Кооперативная машина состояний: каждый вызов serviceUpload() делает один
 * ограниченный шаг (подключение Wi-Fi без ожидания, несколько кусков тела по
 * 4 КиБ, чтение ответа), поэтому loop() между шагами успевает опросить кнопку
 * и слить буфер на карту. START во время передачи обрывает её немедленно
 * (preemptUpload); недосланная часть остаётся .WAV и уйдёт заново целиком —
 * сервер идемпотентен.
 *
 * Классы ответов — UploadPolicy.h: временная помеха откладывает очередь по
 * расписанию 1/5/15/60 минут, конфликт или отказ одной части переименовывает
 * её (.CONFLICT / .REJECT) и не держит остальные, 401 и кривой адрес
 * останавливают отправку до смены настроек. Ничего не удаляется.
 *
 * ponytail: CRC части считается чтением всего файла перед отправкой (2–4 с на
 * 19 МБ по SPI). Путь наверх — считать на лету при записи и хранить рядом.
 */

#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_rom_crc.h>
#include <time.h>

#include "Certificates.h"
#include "UploadPolicy.h"

namespace upl {
constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 20000;
constexpr uint32_t HANDSHAKE_TIMEOUT_MS = 15000;
constexpr uint32_t SOCKET_TIMEOUT_MS = 5000;   // одна операция записи в сокет
constexpr uint32_t REPLY_TIMEOUT_MS = 30000;
constexpr uint32_t EMPTY_QUEUE_RECHECK_MS = 60UL * 60UL * 1000UL;
constexpr size_t SEND_CHUNK_BYTES = 4096;
constexpr int CHUNKS_PER_STEP_IDLE = 8;        // в ARMED: loop свободен
constexpr int CHUNKS_PER_STEP_RECORDING = 1;   // при UPLOAD_DURING_RECORDING
constexpr size_t REPLY_MAX_BYTES = 1024;
constexpr const char *NVS_NAMESPACE = "recorder";
constexpr const char *NTP_SERVER = "pool.ntp.org";
}  // namespace upl

enum UpState { UP_IDLE, UP_WIFI, UP_SEND, UP_REPLY, UP_CLOSE };

Preferences g_prefs;

String g_wifiSsid;
String g_wifiPass;
String g_serverUrl;
String g_deviceToken;
String g_deviceId;

UpState g_upState = UP_IDLE;
bool g_uploadArmed = false;
uint32_t g_nextUploadAttemptMs = 0;
uint32_t g_uploadFailures = 0;      // подряд, сбрасывается удачей
bool g_uploadBlocked = false;       // 401 или кривой адрес: до смены настроек
bool g_cardLow = false;             // места меньше порога, а удалять нечего
uint32_t g_sentParts = 0;
uint32_t g_failedParts = 0;
bool g_ntpRequested = false;

// Текущая передача.
WiFiClientSecure g_client;
File g_upFile;
String g_upName;
uint32_t g_upSize = 0;
uint32_t g_upSent = 0;
uint32_t g_upCrc = 0;
uint32_t g_wifiStartedMs = 0;
uint32_t g_replyStartedMs = 0;
uint32_t g_transferStartedMs = 0;
String g_reply;

void loadUploadConfig() {
  g_prefs.begin(upl::NVS_NAMESPACE, true);
  g_wifiSsid = g_prefs.getString("wifi_ssid", "");
  g_wifiPass = g_prefs.getString("wifi_pass", "");
  g_serverUrl = g_prefs.getString("server_url", "");
  g_deviceToken = g_prefs.getString("device_token", "");
  g_deviceId = g_prefs.getString("device_id", "xiao-01");
  g_prefs.end();
}

// Значения приходят строками key=value через Serial. Пароль и токен обратно
// не печатаются никогда.
bool setUploadConfig(const String &key, const String &value) {
  static const char *allowed[] = {"wifi_ssid", "wifi_pass", "server_url",
                                  "device_token", "device_id"};
  for (const char *name : allowed) {
    if (key == name) {
      g_prefs.begin(upl::NVS_NAMESPACE, false);
      g_prefs.putString(name, value);
      g_prefs.end();
      loadUploadConfig();
      g_uploadBlocked = false;
      g_uploadFailures = 0;
      Serial.printf("[cfg] %s сохранено\n", name);
      return true;
    }
  }
  return false;
}

void printUploadConfig() {
  Serial.println("--- UPLOAD CONFIG ---");
  Serial.printf("device_id:    %s\n", g_deviceId.c_str());
  Serial.printf("wifi_ssid:    %s\n", g_wifiSsid.isEmpty() ? "(не задано)" : g_wifiSsid.c_str());
  Serial.printf("wifi_pass:    %s\n", g_wifiPass.isEmpty() ? "(не задано)" : "(задан)");
  Serial.printf("server_url:   %s\n", g_serverUrl.isEmpty() ? "(не задано)" : g_serverUrl.c_str());
  Serial.printf("device_token: %s\n", g_deviceToken.isEmpty() ? "(не задано)" : "(задан)");
  Serial.printf("Отправлено частей: %lu, отказов: %lu, подряд неудач: %lu\n",
                static_cast<unsigned long>(g_sentParts),
                static_cast<unsigned long>(g_failedParts),
                static_cast<unsigned long>(g_uploadFailures));
  Serial.printf("Состояние: %s\n",
                g_uploadBlocked ? "остановлено до смены настроек"
                : g_upState != UP_IDLE ? "идёт передача" : "готово");
  if (g_upState == UP_IDLE && !g_uploadBlocked) {
    const int32_t wait = static_cast<int32_t>(g_nextUploadAttemptMs - millis());
    Serial.printf("Следующая попытка: %s%ld с\n", wait > 0 ? "через " : "сейчас, ", wait > 0 ? wait / 1000 : 0L);
  }
  Serial.printf("Отправка во время записи: %s\n", UPLOAD_DURING_RECORDING ? "включена" : "выключена");
  Serial.printf("Место на карте: %s\n", g_cardLow ? "МАЛО, удалять нечего" : "достаточно");
  Serial.println("Задать: wifi_ssid=... wifi_pass=... server_url=... device_token=...");
  Serial.println("---------------------");
}

bool uploadConfigured() {
  return !g_wifiSsid.isEmpty() && !g_serverUrl.isEmpty() && !g_deviceToken.isEmpty();
}

bool uploadBusy() { return g_upState != UP_IDLE; }

// Старейшая неотправленная часть. Имена сортируются лексикографически, что
// для нашей схемы совпадает с хронологией.
String findOldestReadyPart() {
  File root = SD.open("/");
  if (!root || !root.isDirectory()) {
    return String();
  }
  String best;
  for (File f = root.openNextFile(); f; f = root.openNextFile()) {
    if (!f.isDirectory()) {
      const String name = baseName(f);
      uint16_t session = 0, part = 0;
      bool recovered = false;
      if (uploadpolicy::parsePartName(name.c_str(), &session, &part, &recovered) &&
          (best.isEmpty() || name < best)) {
        best = name;
      }
    }
    f.close();
  }
  root.close();
  return best;
}

// Сколько частей объявила прошивка при чистом STOP: файл REC0007_N08.END.
// 0 — не объявлено (сессия восстановлена после сбоя), заголовок не ставится.
uint16_t declaredPartsFor(uint16_t session) {
  File root = SD.open("/");
  if (!root || !root.isDirectory()) {
    return 0;
  }
  uint16_t result = 0;
  for (File f = root.openNextFile(); f; f = root.openNextFile()) {
    if (!f.isDirectory()) {
      const String name = baseName(f);
      uint16_t s = 0, n = 0;
      if (uploadpolicy::parseEndName(name.c_str(), &s, &n) && s == session) {
        result = n;
      }
    }
    f.close();
  }
  root.close();
  return result;
}

// Метки части в виде значения X-Marks. Пустая строка — заголовка нет.
String marksHeaderFor(const String &partName) {
  char mrkName[32];
  uploadpolicy::marksNameFor(partName.c_str(), mrkName, sizeof(mrkName));
  const String path = "/" + String(mrkName);
  if (!SD.exists(path.c_str())) {
    return String();
  }
  File f = SD.open(path.c_str(), FILE_READ);
  if (!f) {
    return String();
  }
  // 200 меток по «9999.99\n» — 1,6 КиБ; больше не читаем, лишнее сервер не примет.
  static char text[2048];
  const int got = f.read(reinterpret_cast<uint8_t *>(text), sizeof(text) - 1);
  f.close();
  text[got > 0 ? got : 0] = '\0';
  static char header[2048];
  const int count = uploadpolicy::marksHeader(text, header, sizeof(header));
  return count > 0 ? String(header) : String();
}

uint32_t fileCrc32(const String &path, uint32_t &sizeOut) {
  File f = SD.open(path.c_str(), FILE_READ);
  if (!f) {
    sizeOut = 0;
    return 0;
  }
  static uint8_t buffer[upl::SEND_CHUNK_BYTES];
  uint32_t crc = 0;
  uint32_t total = 0;
  while (true) {
    const int read = f.read(buffer, sizeof(buffer));
    if (read <= 0) {
      break;
    }
    crc = esp_rom_crc32_le(crc, buffer, static_cast<uint32_t>(read));
    total += static_cast<uint32_t>(read);
  }
  f.close();
  sizeOut = total;
  return crc;
}

void disconnectWifi() {
  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_OFF);
}

void renamePart(const String &name, const char *suffix) {
  const String from = "/" + name;
  String to = from;
  to.replace(".WAV", suffix);
  const bool ok = SD.rename(from.c_str(), to.c_str());
  Serial.printf("[up] %s -> %s%s\n", name.c_str(), to.c_str() + 1, ok ? "" : " (переименование НЕ удалось)");
}

// Открывает TLS-соединение и шлёт заголовки. Тело уходит по кускам из UP_SEND.
SendResult beginTransfer(const String &name) {
  uint16_t session = 0, part = 0;
  bool recovered = false;
  if (!uploadpolicy::parsePartName(name.c_str(), &session, &part, &recovered)) {
    return SEND_PART_FATAL;
  }

  char host[96], prefix[64];
  uint16_t port = 443;
  if (!uploadpolicy::parseServerUrl(g_serverUrl.c_str(), host, sizeof(host), &port,
                                    prefix, sizeof(prefix))) {
    Serial.println("[up] Некорректный server_url: нужен вид https://host[:port]");
    return SEND_CONFIG_FATAL;
  }

  const String path = "/" + name;
  g_upSize = 0;
  g_upCrc = fileCrc32(path, g_upSize);
  if (g_upSize <= partmath::WAV_HEADER_BYTES) {
    Serial.printf("[up] %s пустой или не читается\n", name.c_str());
    return SEND_PART_FATAL;
  }
  const String marks = marksHeaderFor(name);
  const uint16_t declared = recovered ? 0 : declaredPartsFor(session);

  g_upFile = SD.open(path.c_str(), FILE_READ);
  if (!g_upFile) {
    return SEND_RETRY;
  }

  g_client.stop();
  g_client.setCACert(ISRG_ROOT_X1);
  g_client.setHandshakeTimeout(upl::HANDSHAKE_TIMEOUT_MS);
  // Рукопожатие блокирует loop() на секунду-две: буфер это переживает,
  // задача захвата не останавливается.
  if (!g_client.connect(host, port, upl::SOCKET_TIMEOUT_MS)) {
    char err[96];
    g_client.lastError(err, sizeof(err));
    Serial.printf("[up] Нет соединения с %s:%u: %s\n", host, port, err);
    g_upFile.close();
    return SEND_RETRY;
  }

  char buf[16];
  String req;
  req.reserve(512 + marks.length());
  req += "POST ";
  req += prefix;
  req += "/api/v1/upload HTTP/1.1\r\nHost: ";
  req += host;
  if (port != 443) {
    req += ":";
    req += port;
  }
  req += "\r\nAuthorization: Bearer ";
  req += g_deviceToken;
  req += "\r\nContent-Type: application/octet-stream\r\nContent-Length: ";
  req += g_upSize;
  req += "\r\nX-Device-Id: ";
  req += g_deviceId;
  snprintf(buf, sizeof(buf), "%u", session);
  req += "\r\nX-Session: ";
  req += buf;
  snprintf(buf, sizeof(buf), "%u", part);
  req += "\r\nX-Part: ";
  req += buf;
  req += "\r\nX-Recovered: ";
  req += recovered ? "1" : "0";
  snprintf(buf, sizeof(buf), "%08lx", static_cast<unsigned long>(g_upCrc));
  req += "\r\nX-Crc32: ";
  req += buf;
  if (!marks.isEmpty()) {
    req += "\r\nX-Marks: ";
    req += marks;
  }
  if (declared > 0) {
    snprintf(buf, sizeof(buf), "%u", declared);
    req += "\r\nX-Session-Parts: ";
    req += buf;
  }
  req += "\r\nConnection: close\r\n\r\n";

  if (g_client.print(req) != req.length()) {
    Serial.println("[up] Заголовки не ушли.");
    g_client.stop();
    g_upFile.close();
    return SEND_RETRY;
  }

  g_upName = name;
  g_upSent = 0;
  g_reply = "";
  g_transferStartedMs = millis();
  Serial.printf("[up] %s -> сервер, %lu байт, crc %08lx, меток %s, частей %u\n",
                name.c_str(), static_cast<unsigned long>(g_upSize),
                static_cast<unsigned long>(g_upCrc),
                marks.isEmpty() ? "нет" : marks.c_str(), declared);
  return SEND_OK;
}

void closeTransfer() {
  g_client.stop();
  if (g_upFile) {
    g_upFile.close();
  }
}

// Шлёт до chunks кусков тела. Возвращает SEND_RETRY при обрыве, SEND_OK иначе.
SendResult sendBodyChunks(int chunks) {
  static uint8_t buffer[upl::SEND_CHUNK_BYTES];
  for (int i = 0; i < chunks && g_upSent < g_upSize; ++i) {
    const int read = g_upFile.read(buffer, sizeof(buffer));
    if (read <= 0) {
      Serial.println("[up] Файл оборвался при чтении.");
      return SEND_RETRY;
    }
    const size_t written = g_client.write(buffer, static_cast<size_t>(read));
    if (written != static_cast<size_t>(read)) {
      Serial.printf("[up] Обрыв на %lu из %lu байт\n",
                    static_cast<unsigned long>(g_upSent), static_cast<unsigned long>(g_upSize));
      return SEND_RETRY;
    }
    g_upSent += written;
    g_diag.upload_bytes += written;
  }
  return SEND_OK;
}

// Ответ читается по мере прихода; конец — сервер закрыл соединение
// (Connection: close) или тело длиной Content-Length получено целиком.
bool replyComplete() {
  const int split = g_reply.indexOf("\r\n\r\n");
  if (split < 0) {
    return false;
  }
  // Имена заголовков без регистра: uvicorn шлёт content-length, nginx — Content-Length.
  String head = g_reply.substring(0, split);
  head.toLowerCase();
  const int cl = head.indexOf("content-length:");
  if (cl >= 0) {
    const long length = head.substring(cl + 15, head.indexOf("\r\n", cl)).toInt();
    return static_cast<long>(g_reply.length() - split - 4) >= length;
  }
  return false;
}

// Сервер обязан вернуть размер и сумму того, что у него лежит. Без сверки
// пометка о доставке была бы верой на слово.
SendResult classifyReply() {
  int status = 0;
  if (g_reply.startsWith("HTTP/1.")) {
    status = g_reply.substring(9, 12).toInt();
  }
  const SendResult verdict = uploadpolicy::classifyStatus(status);
  const int split = g_reply.indexOf("\r\n\r\n");
  const String body = split >= 0 ? g_reply.substring(split + 4) : String();
  if (verdict != SEND_OK) {
    Serial.printf("[up] Ответ %d: %s\n", status, body.c_str());
    return verdict;
  }
  char buf[16];
  snprintf(buf, sizeof(buf), "%08lx", static_cast<unsigned long>(g_upCrc));
  const bool crcEchoed = body.indexOf(String("\"crc32\":\"") + buf + "\"") >= 0;
  const bool sizeEchoed = body.indexOf("\"bytes\":" + String(static_cast<unsigned long>(g_upSize))) >= 0;
  if (!crcEchoed || !sizeEchoed) {
    Serial.printf("[up] Сервер подтвердил не то: %s\n", body.c_str());
    return SEND_RETRY;
  }
  return SEND_OK;
}

bool markDelivered(const String &name) {
  const String from = "/" + name;
  String to = from;
  to.replace(".WAV", ".SENT");
  if (!SD.rename(from.c_str(), to.c_str())) {
    // Без пометки та же часть уйдёт снова. Сервер опознает дубликат, но
    // трафик будет потрачен впустую.
    Serial.printf("[up] ВНИМАНИЕ: не удалось переименовать %s\n", name.c_str());
    return false;
  }
  Serial.printf("[up] Доставлено: %s -> %s\n", name.c_str(), to.c_str() + 1);
  return true;
}

void scheduleRetry() {
  ++g_uploadFailures;
  ++g_failedParts;
  g_nextUploadAttemptMs = millis() + uploadpolicy::backoffMs(g_uploadFailures);
  Serial.printf("[up] Повтор через %lu мин (неудач подряд: %lu)\n",
                static_cast<unsigned long>(uploadpolicy::backoffMs(g_uploadFailures) / 60000UL),
                static_cast<unsigned long>(g_uploadFailures));
}

// Следующая часть в этом же сеансе Wi-Fi или закрытие сети.
void nextPartOrClose() {
  const String name = findOldestReadyPart();
  if (name.isEmpty()) {
    Serial.println("[up] Очередь пуста.");
    g_nextUploadAttemptMs = millis() + upl::EMPTY_QUEUE_RECHECK_MS;
    g_upState = UP_CLOSE;
    return;
  }
  switch (beginTransfer(name)) {
    case SEND_OK:
      g_upState = UP_SEND;
      break;
    case SEND_RETRY:
      scheduleRetry();
      g_upState = UP_CLOSE;
      break;
    case SEND_CONFLICT:
      renamePart(name, ".CONFLICT");
      break;  // остаёмся в текущем состоянии: следующий вызов возьмёт другую часть
    case SEND_PART_FATAL:
      renamePart(name, ".REJECT");
      break;
    case SEND_CONFIG_FATAL:
      g_uploadBlocked = true;
      Serial.println("[up] Отправка остановлена до смены настроек.");
      g_upState = UP_CLOSE;
      break;
  }
}

void finishTransfer(SendResult result) {
  closeTransfer();
  g_diag.upload_duration_ms += millis() - g_transferStartedMs;
  switch (result) {
    case SEND_OK:
      ++g_sentParts;
      g_uploadFailures = 0;
      if (!markDelivered(g_upName)) {
        g_upState = UP_CLOSE;  // иначе тот же файл пойдёт по кругу
        return;
      }
      break;
    case SEND_RETRY:
      scheduleRetry();
      g_upState = UP_CLOSE;
      return;
    case SEND_CONFLICT:
      renamePart(g_upName, ".CONFLICT");
      break;
    case SEND_PART_FATAL:
      renamePart(g_upName, ".REJECT");
      break;
    case SEND_CONFIG_FATAL:
      g_uploadBlocked = true;
      Serial.println("[up] Отправка остановлена до вмешательства владельца.");
      g_upState = UP_CLOSE;
      return;
  }
  g_upState = UP_WIFI;  // сеть уже есть: следующая часть без переподключения
}

// Вызывается после каждой остановки записи и при старте: пробуем отдать всё.
void armUpload() {
  g_uploadArmed = true;
}

// START во время передачи: сеть выключается сразу, часть уйдёт заново позже.
void preemptUpload() {
  if (g_upState == UP_IDLE) {
    return;
  }
  Serial.printf("[up] Запись началась, отправка %s прервана.\n", g_upName.c_str());
  closeTransfer();
  disconnectWifi();
  g_upState = UP_IDLE;
  ++g_diag.upload_preempted;
  g_nextUploadAttemptMs = millis();
}

void serviceUpload() {
  const uint32_t now = millis();
  switch (g_upState) {
    case UP_IDLE: {
      if (!g_sdReady || g_uploadBlocked || !uploadConfigured()) {
        return;
      }
      if (!g_uploadArmed && static_cast<int32_t>(now - g_nextUploadAttemptMs) < 0) {
        return;
      }
      g_uploadArmed = false;
      if (findOldestReadyPart().isEmpty()) {
        g_nextUploadAttemptMs = now + upl::EMPTY_QUEUE_RECHECK_MS;
        return;
      }
      WiFi.mode(WIFI_STA);
      WiFi.begin(g_wifiSsid.c_str(), g_wifiPass.c_str());
      g_wifiStartedMs = now;
      g_ntpRequested = false;
      g_upState = UP_WIFI;
      Serial.printf("[net] Подключение к %s...\n", g_wifiSsid.c_str());
      return;
    }
    case UP_WIFI: {
      if (WiFi.status() != WL_CONNECTED) {
        if (now - g_wifiStartedMs >= upl::WIFI_CONNECT_TIMEOUT_MS) {
          Serial.println("[net] Сеть недоступна.");
          scheduleRetry();
          g_upState = UP_CLOSE;
        }
        return;
      }
      if (!g_ntpRequested) {
        // Часы — только когда сеть уже есть ради отправки, никогда ради времени.
        Serial.printf("[net] Подключено, IP %s\n", WiFi.localIP().toString().c_str());
        configTime(0, 0, upl::NTP_SERVER);
        g_ntpRequested = true;
      }
      nextPartOrClose();
      return;
    }
    case UP_SEND: {
      const int chunks = g_state == ST_RECORDING ? upl::CHUNKS_PER_STEP_RECORDING
                                                 : upl::CHUNKS_PER_STEP_IDLE;
      if (sendBodyChunks(chunks) != SEND_OK) {
        finishTransfer(SEND_RETRY);
        return;
      }
      if (g_upSent >= g_upSize) {
        g_replyStartedMs = now;
        g_upState = UP_REPLY;
      }
      return;
    }
    case UP_REPLY: {
      while (g_client.available() > 0 && g_reply.length() < upl::REPLY_MAX_BYTES) {
        g_reply += static_cast<char>(g_client.read());
      }
      const bool closed = !g_client.connected() && g_client.available() == 0;
      if (replyComplete() || (closed && g_reply.length() > 0)) {
        finishTransfer(classifyReply());
        return;
      }
      if (closed || now - g_replyStartedMs >= upl::REPLY_TIMEOUT_MS) {
        Serial.println("[up] Сервер не ответил.");
        finishTransfer(SEND_RETRY);
      }
      return;
    }
    case UP_CLOSE:
      closeTransfer();
      disconnectWifi();
      g_upState = UP_IDLE;
      return;
  }
}

// --- очистка карты ------------------------------------------------------------------

// Удаляет только .SENT (и их .MRK), старые первыми, и только когда свободного
// места меньше CLEANUP_MIN_FREE_BYTES — до CLEANUP_TARGET_FREE_BYTES. Всё
// неподтверждённое (.WAV .TMP .BAD .CONFLICT .REJECT) не трогается никогда.
// SD.usedBytes() на FAT32 читает таблицу целиком — секунды на 32 ГБ, не
// измерено; поэтому вызывается после STOP и при старте, но не перед START.
void cleanupSentParts() {
  if (!g_sdReady) {
    return;
  }
  const uint64_t total = SD.totalBytes();
  uint64_t free = total - SD.usedBytes();
  if (free >= cfg::CLEANUP_MIN_FREE_BYTES) {
    g_cardLow = false;
    Serial.printf("[clean] Свободно %llu МБ, удалять не нужно.\n",
                  static_cast<unsigned long long>(free / (1024ULL * 1024ULL)));
    return;
  }

  // Список сначала, удаление потом: менять каталог во время обхода нельзя.
  constexpr int MAX_CANDIDATES = 64;
  String names[MAX_CANDIDATES];
  uint32_t sizes[MAX_CANDIDATES];
  int found = 0;
  File root = SD.open("/");
  if (!root || !root.isDirectory()) {
    return;
  }
  for (File f = root.openNextFile(); f && found < MAX_CANDIDATES; f = root.openNextFile()) {
    if (!f.isDirectory()) {
      const String name = baseName(f);
      if (uploadpolicy::isSentPart(name.c_str())) {
        names[found] = name;
        sizes[found] = static_cast<uint32_t>(f.size());
        ++found;
      }
    }
    f.close();
  }
  root.close();

  int deleted = 0;
  while (free < cfg::CLEANUP_TARGET_FREE_BYTES) {
    int oldest = -1;
    for (int i = 0; i < found; ++i) {
      if (!names[i].isEmpty() && (oldest < 0 || names[i] < names[oldest])) {
        oldest = i;
      }
    }
    if (oldest < 0) {
      break;
    }
    const String path = "/" + names[oldest];
    if (SD.remove(path.c_str())) {
      free += sizes[oldest];
      ++deleted;
      char mrk[32];
      uploadpolicy::marksNameFor(names[oldest].c_str(), mrk, sizeof(mrk));
      const String mrkPath = "/" + String(mrk);
      if (SD.exists(mrkPath.c_str())) {
        SD.remove(mrkPath.c_str());
      }
      Serial.printf("[clean] Удалено %s\n", names[oldest].c_str());
    }
    names[oldest] = "";
  }

  g_cardLow = free < cfg::CLEANUP_MIN_FREE_BYTES;
  Serial.printf("[clean] Удалено %d, свободно %llu МБ%s\n", deleted,
                static_cast<unsigned long long>(free / (1024ULL * 1024ULL)),
                g_cardLow ? " — МАЛО, подтверждённых частей для удаления нет" : "");
  if (g_cardLow) {
    feedbackEvent("error");
  }
}
