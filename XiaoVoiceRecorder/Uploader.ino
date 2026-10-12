/*
 * Отправка готовых частей на личный сервер. Контракт: shared/protocol.md.
 *
 * Кооперативная машина состояний: CRC и тело уступают loop() между блоками
 * при событии REC или исчерпании бюджета. Отдельный вызов SD/TLS, DNS,
 * рукопожатие и операции каталога остаются блокирующими — бюджет не является
 * верхней границей задержки REC. Захват микрофона работает отдельной задачей.
 * START прерывает передачу; следующая попытка спрашивает серверный offset.
 * Доставка подтверждается только после сверки размера и CRC ответа.
 *
 * Классы ответов — UploadPolicy.h: временная помеха откладывает очередь по
 * расписанию 1/5/15/60 минут, конфликт или отказ одной части переименовывает
 * её (.CONFLICT / .REJECT) и не держит остальные, 401 и кривой адрес
 * останавливают отправку до смены настроек. Ничего не удаляется.
 *
 * Измерения [up-perf] разделяют CRC, соединение, SD и TLS write; скорость
 * тела не включает CRC, но включает паузы между вызовами serviceUpload().
 *
 * ponytail: контрольная сумма считается чтением всего файла перед каждой
 * попыткой. Путь наверх — считать на лету при записи и хранить рядом с
 * частью, тогда 19 МБ не перечитываются вовсе.
 */

#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_rom_crc.h>
#include <esp_timer.h>
#include <lwip/opt.h>
#include <time.h>

#include "Certificates.h"
#include "Session.h"
#include "UploadPolicy.h"
#include "UploadPump.h"

namespace upl {
constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 20000;
// В СЕКУНДАХ: NetworkClientSecure::setHandshakeTimeout сам умножает на 1000
// (arduino-esp32 3.3.11). Прежние 15000 давали 4 ч 10 мин блокировки loop().
// DNS имеет собственное ожидание; общей гарантии 20 с нет. Особенно важен
// замер задержек с fallback-буфером без PSRAM (около 2 с в моно).
constexpr unsigned long HANDSHAKE_TIMEOUT_S = 15;
constexpr uint32_t SOCKET_TIMEOUT_MS = 5000;   // connect / отсутствие прогресса write, не жёсткий deadline
constexpr uint32_t REPLY_TIMEOUT_MS = 30000;
constexpr uint32_t EMPTY_QUEUE_RECHECK_MS = 60UL * 60UL * 1000UL;
// Arduino-ESP32 3.3.11 send_ssl_data() сам дробит write на <=4096 байт.
constexpr size_t CHUNK_BYTES = 4096;
constexpr uint64_t STEP_BUDGET_US_IDLE = 25000;
constexpr uint64_t STEP_BUDGET_US_RECORDING = 10000;
constexpr int SEND_CHUNKS_PER_STEP_IDLE = 8;       // в ARMED: loop свободен
constexpr int SEND_CHUNKS_PER_STEP_RECORDING = 1;  // при UPLOAD_DURING_RECORDING
constexpr int CRC_CHUNKS_PER_STEP_IDLE = 16;       // 64 КиБ с карты за оборот loop()
constexpr int CRC_CHUNKS_PER_STEP_RECORDING = 2;
constexpr size_t REPLY_MAX_BYTES = 1024;
constexpr const char *NVS_NAMESPACE = "recorder";
constexpr const char *NTP_SERVER = "pool.ntp.org";
}  // namespace upl

enum UpState { UP_IDLE, UP_WIFI, UP_CRC, UP_CONNECT, UP_PROBE, UP_SEND, UP_REPLY, UP_CLOSE };

Preferences g_prefs;

// До трёх сетей: дом, офис, телефон. Ключи wifi_ssid/wifi_pass, затем с
// суффиксом 2 и 3. Неудача сразу переходит к следующей заданной сети;
// удачная запоминается до перезагрузки и пробуется первой.
constexpr int WIFI_SLOTS = 3;
const char *const SSID_KEYS[WIFI_SLOTS] = {"wifi_ssid", "wifi_ssid2", "wifi_ssid3"};
const char *const PASS_KEYS[WIFI_SLOTS] = {"wifi_pass", "wifi_pass2", "wifi_pass3"};
String g_wifiSsids[WIFI_SLOTS];
String g_wifiPasses[WIFI_SLOTS];
int g_wifiIdx = 0;    // сеть текущей или следующей попытки
int g_wifiTried = 0;  // сколько сетей не ответило подряд в этом круге
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
uint16_t g_upSession = 0;
uint16_t g_upPart = 0;
bool g_upRecovered = false;
char g_upHost[96];
char g_upPrefix[64];
uint16_t g_upPort = 443;
uint32_t g_upSize = 0;
uint32_t g_upSent = 0;
uint32_t g_upCrc = 0;
uint32_t g_upCrcDone = 0;
uint32_t g_wifiStartedMs = 0;
uint32_t g_replyStartedMs = 0;
uint32_t g_transferStartedMs = 0;
String g_reply;

// CRC и тело никогда не работают одновременно: один буфер вместо двух.
static uint8_t g_uploadBuffer[upl::CHUNK_BYTES];
struct UploadPerf {
  uploadpump::Stats body;
  uploadpump::Timing crcRead, crcCompute, crcStep, connect, headers;
  uint64_t ended = 0;
  uint64_t started = 0, crcWallUs = 0, probeStarted = 0, probeUs = 0;
  uint64_t bodyStarted = 0, bodyUs = 0, replyStarted = 0;
  uint32_t lastReported = 0;
  bool active = false, probing = false, sending = false, replying = false;
};
UploadPerf g_upPerf;

static uint64_t uploadClockUs() { return static_cast<uint64_t>(esp_timer_get_time()); }
static bool uploadButtonPending() { return g_btnHead != g_btnTail; }

void freezeUploadPerf() {
  if (!g_upPerf.active || g_upPerf.ended) return;
  const uint64_t now = uploadClockUs();
  g_upPerf.ended = now;
  if (g_upState == UP_CRC) g_upPerf.crcWallUs = now - g_upPerf.started;
  if (g_upPerf.probing) g_upPerf.probeUs = now - g_upPerf.probeStarted;
  if (g_upPerf.sending) g_upPerf.bodyUs = now - g_upPerf.bodyStarted;
}

void printUploadPerf(const char *outcome) {
  if (!g_upPerf.active) return;
  freezeUploadPerf();
  const uint64_t now = g_upPerf.ended;
  const uint64_t replyUs = g_upPerf.replying ? now - g_upPerf.replyStarted : 0;
  Serial.printf("[up-perf] outcome=%s file_ms=%llu crc_wall_ms=%llu crc_read_us=%llu "
                "crc_compute_us=%llu crc_calls=%lu crc_step_max_us=%llu\n",
                outcome, (now - g_upPerf.started) / 1000ULL, g_upPerf.crcWallUs / 1000ULL,
                static_cast<unsigned long long>(g_upPerf.crcRead.us),
                static_cast<unsigned long long>(g_upPerf.crcCompute.us),
                static_cast<unsigned long>(g_upPerf.crcRead.calls),
                static_cast<unsigned long long>(g_upPerf.crcStep.maxUs));
  Serial.printf("[up-perf] connect_ms=%llu headers_us=%llu probe_ms=%llu reply_ms=%llu "
                "body_ms=%llu accepted=%lu rate_KiB_s=%llu\n",
                g_upPerf.connect.us / 1000ULL,
                static_cast<unsigned long long>(g_upPerf.headers.us), g_upPerf.probeUs / 1000ULL,
                replyUs / 1000ULL, g_upPerf.bodyUs / 1000ULL,
                static_cast<unsigned long>(g_upPerf.body.accepted),
                static_cast<unsigned long long>(uploadpump::kibPerSecond(g_upPerf.body.accepted, g_upPerf.bodyUs)));
  Serial.printf("[up-perf] sd_us=%llu sd_max_us=%llu sd_calls=%lu write_us=%llu "
                "write_max_us=%llu write_calls=%lu body_step_max_us=%llu heap=%lu\n",
                static_cast<unsigned long long>(g_upPerf.body.read.us),
                static_cast<unsigned long long>(g_upPerf.body.read.maxUs),
                static_cast<unsigned long>(g_upPerf.body.read.calls),
                static_cast<unsigned long long>(g_upPerf.body.write.us),
                static_cast<unsigned long long>(g_upPerf.body.write.maxUs),
                static_cast<unsigned long>(g_upPerf.body.write.calls),
                static_cast<unsigned long long>(g_upPerf.body.step.maxUs),
                static_cast<unsigned long>(ESP.getFreeHeap()));
  g_upPerf.active = false;
}

int wifiCount() {
  int n = 0;
  for (const String &s : g_wifiSsids) n += s.isEmpty() ? 0 : 1;
  return n;
}

// Ставит g_wifiIdx на заданную сеть, начиная с from (по кругу).
void pickWifiFrom(int from) {
  for (int k = 0; k < WIFI_SLOTS; ++k) {
    const int i = (from + k) % WIFI_SLOTS;
    if (!g_wifiSsids[i].isEmpty()) {
      g_wifiIdx = i;
      return;
    }
  }
}

void loadUploadConfig() {
  g_prefs.begin(upl::NVS_NAMESPACE, true);
  for (int i = 0; i < WIFI_SLOTS; ++i) {
    g_wifiSsids[i] = g_prefs.getString(SSID_KEYS[i], "");
    g_wifiPasses[i] = g_prefs.getString(PASS_KEYS[i], "");
  }
  g_serverUrl = g_prefs.getString("server_url", "");
  g_deviceToken = g_prefs.getString("device_token", "");
  g_deviceId = g_prefs.getString("device_id", "xiao-01");
  g_prefs.end();
}

// Значения приходят строками key=value через Serial. Пароль и токен обратно
// не печатаются никогда.
bool setUploadConfig(const String &key, const String &value) {
  static const char *allowed[] = {"wifi_ssid", "wifi_pass", "wifi_ssid2", "wifi_pass2",
                                  "wifi_ssid3", "wifi_pass3", "server_url",
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
  for (int i = 0; i < WIFI_SLOTS; ++i) {
    if (i > 0 && g_wifiSsids[i].isEmpty()) continue;
    Serial.printf("%-11s   %s, пароль %s%s\n", SSID_KEYS[i],
                  g_wifiSsids[i].isEmpty() ? "(не задано)" : g_wifiSsids[i].c_str(),
                  g_wifiPasses[i].isEmpty() ? "не задан" : "задан",
                  i == g_wifiIdx && !g_wifiSsids[i].isEmpty() ? "  <- следующая попытка" : "");
  }
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
  Serial.println("Задать: wifi_ssid=... wifi_pass=... (вторая и третья сеть: wifi_ssid2= wifi_pass2= ...)");
  Serial.println("        server_url=... device_token=...  Убрать сеть: wifi_ssid2= (пусто)");
  Serial.println("---------------------");
}

bool uploadConfigured() {
  return wifiCount() > 0 && !g_serverUrl.isEmpty() && !g_deviceToken.isEmpty();
}

bool uploadBusy() { return g_upState != UP_IDLE; }

// --- очередь на карте ------------------------------------------------------------------

struct OldestPart {
  char name[session::PATH_MAX_LEN] = {0};
};

static void oldestPartCb(const char *name, uint32_t, void *ctx) {
  uint16_t s = 0, p = 0;
  bool recovered = false;
  if (!uploadpolicy::parsePartName(name, &s, &p, &recovered)) return;
  OldestPart *o = static_cast<OldestPart *>(ctx);
  if (o->name[0] == '\0' || strcmp(name, o->name) < 0) {
    strncpy(o->name, name, sizeof(o->name) - 1);
  }
}

// Старейшая неотправленная часть. Имена сортируются лексикографически, что
// для нашей схемы совпадает с хронологией.
String findOldestReadyPart() {
  OldestPart oldest;
  g_sd.listRoot(oldestPartCb, &oldest);
  return String(oldest.name);
}

struct DeclaredParts {
  uint16_t session = 0;
  uint16_t parts = 0;
};

static void declaredPartsCb(const char *name, uint32_t, void *ctx) {
  DeclaredParts *d = static_cast<DeclaredParts *>(ctx);
  uint16_t s = 0, n = 0;
  if (uploadpolicy::parseEndName(name, &s, &n) && s == d->session) d->parts = n;
}

// Сколько частей объявила прошивка при чистом STOP: файл REC0007_N08.END.
// 0 — не объявлено (сессия восстановлена после сбоя), заголовок не ставится.
uint16_t declaredPartsFor(uint16_t sessionId) {
  DeclaredParts d;
  d.session = sessionId;
  g_sd.listRoot(declaredPartsCb, &d);
  return d.parts;
}

// Метки части в виде значения X-Marks. Пустая строка — заголовка нет.
// Имя файла меток не зависит от _R: восстановленная часть находит свои метки.
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

// Последняя причина отказа от роутера (wifi_err_reason_t): 15/204 — пароль,
// 201 — сеть не найдена. WiFi.status() этого не различает.
volatile uint8_t g_wifiReason = 0;

void watchWifiReason() {
  WiFi.onEvent([](WiFiEvent_t, WiFiEventInfo_t info) {
    g_wifiReason = info.wifi_sta_disconnected.reason;
  }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
}

bool wifiKnown(const String &ssid) {
  for (const String &s : g_wifiSsids) {
    if (!s.isEmpty() && s == ssid) return true;
  }
  return false;
}

// Команда w: какие сети плата видит и с каким сигналом. Только вне записи и
// отправки — сканирование занимает радио на пару секунд.
void scanWifi() {
  WiFi.mode(WIFI_STA);
  const int n = WiFi.scanNetworks();
  Serial.printf("[net] Видно сетей: %d\n", n < 0 ? 0 : n);
  for (int i = 0; i < n; ++i) {
    Serial.printf("  %-32s %4d dBm  канал %2d%s\n", WiFi.SSID(i).c_str(),
                  static_cast<int>(WiFi.RSSI(i)), static_cast<int>(WiFi.channel(i)),
                  wifiKnown(WiFi.SSID(i)) ? "  <- настроенная" : "");
  }
  WiFi.scanDelete();
  WiFi.mode(WIFI_OFF);
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

// --- передача одной части --------------------------------------------------------------------

// Шаг 1: разобрать имя и адрес, открыть файл. Контрольная сумма считается
// потом по кускам (UP_CRC), чтобы кнопка не ждала чтения 19 МБ с карты.
SendResult prepareTransfer(const String &name) {
  if (!uploadpolicy::parsePartName(name.c_str(), &g_upSession, &g_upPart, &g_upRecovered)) {
    return SEND_PART_FATAL;
  }
  if (!uploadpolicy::parseServerUrl(g_serverUrl.c_str(), g_upHost, sizeof(g_upHost), &g_upPort,
                                    g_upPrefix, sizeof(g_upPrefix))) {
    Serial.println("[up] Некорректный server_url: нужен вид https://host[:port]");
    return SEND_CONFIG_FATAL;
  }
  const String path = "/" + name;
  g_upFile = SD.open(path.c_str(), FILE_READ);
  if (!g_upFile) {
    return SEND_RETRY;
  }
  g_upSize = static_cast<uint32_t>(g_upFile.size());
  if (g_upSize <= audio::WAV_HEADER_BYTES) {
    Serial.printf("[up] %s пустой или не читается\n", name.c_str());
    g_upFile.close();
    return SEND_PART_FATAL;
  }
  g_upName = name;
  g_upCrc = 0;
  g_upCrcDone = 0;
  g_upSent = 0;
  g_reply = "";
  g_transferStartedMs = millis();
  g_upPerf = UploadPerf{};
  g_upPerf.active = true;
  Serial.printf("[up-perf] block=%u sd_hz=%lu tcp_snd_buf=%u tcp_wnd=%u tcp_mss=%u rssi=%d heap=%lu\n",
                static_cast<unsigned>(upl::CHUNK_BYTES), static_cast<unsigned long>(cfg::SD_SPI_HZ),
                static_cast<unsigned>(TCP_SND_BUF), static_cast<unsigned>(TCP_WND),
                static_cast<unsigned>(TCP_MSS), static_cast<int>(WiFi.RSSI()),
                static_cast<unsigned long>(ESP.getFreeHeap()));
  g_upPerf.started = uploadClockUs();
  return SEND_OK;
}

// Шаг 2: до chunks кусков контрольной суммы.
//   CRC_MORE  — читать дальше на следующем обороте loop()
//   CRC_DONE  — файл прочитан целиком
//   CRC_ERROR — карта перестала отдавать байты раньше конца файла
// Сам тип CrcStep живёт в Types.h: Arduino вставляет прототипы в начало
// объединённого файла, раньше тел .ino.
CrcStep serviceCrc(int chunks) {
  const uint64_t started = uploadClockUs();
  const uint64_t budget = g_state == ST_RECORDING ? upl::STEP_BUDGET_US_RECORDING : upl::STEP_BUDGET_US_IDLE;
  for (int i = 0; i < chunks && g_upCrcDone < g_upSize; ++i) {
    if (uploadButtonPending() || (i > 0 && uploadClockUs() - started >= budget)) break;
    uint64_t at = uploadClockUs();
    const int read = g_upFile.read(g_uploadBuffer, sizeof(g_uploadBuffer));
    g_upPerf.crcRead.add(uploadClockUs() - at);
    if (read <= 0) {
      Serial.printf("[up] Карта отдала %lu из %lu байт, дальше чтения нет.\n",
                    static_cast<unsigned long>(g_upCrcDone),
                    static_cast<unsigned long>(g_upSize));
      g_upPerf.crcStep.add(uploadClockUs() - started);
      return CRC_ERROR;
    }
    at = uploadClockUs();
    g_upCrc = esp_rom_crc32_le(g_upCrc, g_uploadBuffer, static_cast<uint32_t>(read));
    g_upPerf.crcCompute.add(uploadClockUs() - at);
    g_upCrcDone += static_cast<uint32_t>(read);
  }
  g_upPerf.crcStep.add(uploadClockUs() - started);
  return g_upCrcDone >= g_upSize ? CRC_DONE : CRC_MORE;
}

// Общие заголовки: токен и то, что называет часть. Нужны и вопросу о
// докачке, и самой отправке.
void appendPartHeaders(String &req) {
  char buf[16];
  req += "Host: ";
  req += g_upHost;
  if (g_upPort != 443) {
    req += ":";
    req += g_upPort;
  }
  req += "\r\nAuthorization: Bearer ";
  req += g_deviceToken;
  req += "\r\nX-Device-Id: ";
  req += g_deviceId;
  snprintf(buf, sizeof(buf), "%u", g_upSession);
  req += "\r\nX-Session: ";
  req += buf;
  snprintf(buf, sizeof(buf), "%u", g_upPart);
  req += "\r\nX-Part: ";
  req += buf;
  snprintf(buf, sizeof(buf), "%08lx", static_cast<unsigned long>(g_upCrc));
  req += "\r\nX-Crc32: ";
  req += buf;
  req += "\r\nX-Total: ";
  req += g_upSize;
  req += "\r\n";
}

// Шаг 3: TLS-соединение и вопрос серверу, сколько байт этой части у него уже
// есть (докачка после обрыва). Ответ читает UP_PROBE, тело уходит потом по
// тому же соединению.
SendResult openConnection() {
  g_client.stop();
  g_client.setCACert(ISRG_ROOT_X1);
  g_client.setHandshakeTimeout(upl::HANDSHAKE_TIMEOUT_S);
  // DNS + TCP + TLS измеряются вместе: публичный connect() не разделяет их.
  const uint64_t at = uploadClockUs();
  const bool connected = g_client.connect(g_upHost, g_upPort, upl::SOCKET_TIMEOUT_MS);
  g_upPerf.connect.add(uploadClockUs() - at);
  if (!connected) {
    char err[96];
    g_client.lastError(err, sizeof(err));
    Serial.printf("[up] Нет соединения с %s:%u: %s\n", g_upHost, g_upPort, err);
    return SEND_RETRY;
  }
  String req;
  req.reserve(384);
  req += "GET ";
  req += g_upPrefix;
  req += "/api/v1/upload/offset HTTP/1.1\r\n";
  appendPartHeaders(req);
  req += "Connection: keep-alive\r\n\r\n";
  const uint64_t headerAt = uploadClockUs();
  const size_t headerWritten = g_client.print(req);
  g_upPerf.headers.add(uploadClockUs() - headerAt);
  if (headerWritten != req.length()) {
    Serial.println("[up] Вопрос о докачке не ушёл.");
    g_client.stop();
    return SEND_RETRY;
  }
  g_reply = "";
  g_replyStartedMs = millis();
  g_upPerf.probeStarted = uploadClockUs();
  g_upPerf.probing = true;
  return SEND_OK;
}

// Шаг 4: заголовки отправки с байта offset. Тело — остаток файла.
SendResult sendUploadHeaders(uint32_t offset) {
  if (!g_upFile.seek(offset)) {
    Serial.println("[up] Файл не перемотался.");
    return SEND_RETRY;
  }
  const String marks = marksHeaderFor(g_upName);
  const uint16_t declared = g_upRecovered ? 0 : declaredPartsFor(g_upSession);
  // Заряд — только с платы, которая умеет его измерить: на Sense заголовка
  // нет, и сервер честно показывает «диктофон не сообщал».
  const battery::Status batt = readBattery();

  char buf[16];
  String req;
  req.reserve(512 + marks.length());
  req += "POST ";
  req += g_upPrefix;
  req += "/api/v1/upload HTTP/1.1\r\n";
  appendPartHeaders(req);
  req += "Content-Type: application/octet-stream\r\nContent-Length: ";
  req += g_upSize - offset;
  req += "\r\nX-Offset: ";
  req += offset;
  req += "\r\nX-Recovered: ";
  req += g_upRecovered ? "1" : "0";
  if (!marks.isEmpty()) {
    req += "\r\nX-Marks: ";
    req += marks;
  }
  if (declared > 0) {
    snprintf(buf, sizeof(buf), "%u", declared);
    req += "\r\nX-Session-Parts: ";
    req += buf;
  }
  if (batt.supported) {
    snprintf(buf, sizeof(buf), "%u", batt.percent);
    req += "\r\nX-Battery: ";
    req += buf;
  }
  // Версия плюс профиль платы: "0.4.0+xiao-sense". Разрешённые сервером
  // символы — буквы, цифры и .-_+ (shared/protocol.md).
  req += "\r\nX-Firmware: ";
  req += cfg::FIRMWARE_VERSION;
  req += "+";
  req += board::ID;
  req += "\r\nConnection: close\r\n\r\n";

  const uint64_t headerAt = uploadClockUs();
  const size_t headerWritten = g_client.print(req);
  g_upPerf.headers.add(uploadClockUs() - headerAt);
  if (headerWritten != req.length()) {
    Serial.println("[up] Заголовки не ушли.");
    g_client.stop();
    return SEND_RETRY;
  }
  g_upSent = offset;
  g_reply = "";

  Serial.printf("[up] %s -> сервер, %lu байт%s, crc %08lx, меток %s, частей %u\n",
                g_upName.c_str(), static_cast<unsigned long>(g_upSize),
                offset ? (String(", докачка с ") + offset).c_str() : "",
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
  const uint32_t before = g_upPerf.body.accepted;
  const uploadpump::Result result = uploadpump::sendStep(
      g_upFile, g_client, g_uploadBuffer, sizeof(g_uploadBuffer), g_upSize, g_upSent,
      static_cast<unsigned>(chunks),
      g_state == ST_RECORDING ? upl::STEP_BUDGET_US_RECORDING : upl::STEP_BUDGET_US_IDLE,
      g_upPerf.body, uploadClockUs, uploadButtonPending);
  g_diag.upload_bytes += g_upPerf.body.accepted - before;
  if (uploadpump::progressDue(g_upPerf.body.accepted, g_upPerf.lastReported)) {
    Serial.printf("[up] %lu из %lu байт, тело %llu КиБ/с, sd_us=%llu write_us=%llu\n",
                  static_cast<unsigned long>(g_upSent), static_cast<unsigned long>(g_upSize),
                  static_cast<unsigned long long>(uploadpump::kibPerSecond(
                      g_upPerf.body.accepted, uploadClockUs() - g_upPerf.bodyStarted)),
                  static_cast<unsigned long long>(g_upPerf.body.read.us),
                  static_cast<unsigned long long>(g_upPerf.body.write.us));
  }
  if (result == uploadpump::READ_ERROR || result == uploadpump::WRITE_ERROR) {
    Serial.printf("[up] Обрыв (%s) на %lu из %lu байт; следующая попытка спросит у сервера, откуда продолжать.\n",
                  result == uploadpump::READ_ERROR ? "чтение карты" : "запись в сокет",
                  static_cast<unsigned long>(g_upSent), static_cast<unsigned long>(g_upSize));
    return SEND_RETRY;
  }
  return SEND_OK;
}

// Ответы короткие, но побайтовый read делал сотни вызовов mbedTLS. Читаем
// только уже доступные байты, не используем блокирующий readBytes().
void readUploadReply() {
  uint8_t buffer[256];
  while (g_reply.length() < upl::REPLY_MAX_BYTES) {
    const int available = g_client.available();
    if (available <= 0) break;
    size_t count = static_cast<size_t>(available);
    if (count > sizeof(buffer)) count = sizeof(buffer);
    const size_t room = upl::REPLY_MAX_BYTES - g_reply.length();
    if (count > room) count = room;
    const int got = g_client.read(buffer, count);
    if (got <= 0) break;
    g_reply.concat(reinterpret_cast<const char *>(buffer), static_cast<unsigned int>(got));
  }
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
  // Число целиком: "bytes":32044 без этой проверки нашлось бы и в "bytes":320440.
  const String sizeKey = "\"bytes\":" + String(static_cast<unsigned long>(g_upSize));
  const int sizeAt = body.indexOf(sizeKey);
  const unsigned int sizeEnd = static_cast<unsigned int>(sizeAt) + sizeKey.length();
  const bool sizeEchoed = sizeAt >= 0 && (sizeEnd >= body.length() || !isDigit(body[sizeEnd]));
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

// Отказ, не связанный с сетью: часть переименовывается и очередь идёт дальше;
// 401 и кривой адрес останавливают всё. Возвращает false, если очередь стоит.
bool handlePartVerdict(SendResult result, const String &name) {
  switch (result) {
    case SEND_CONFLICT:
      renamePart(name, ".CONFLICT");
      return true;
    case SEND_PART_FATAL:
      renamePart(name, ".REJECT");
      return true;
    case SEND_CONFIG_FATAL:
      g_uploadBlocked = true;
      Serial.println("[up] Отправка остановлена до смены настроек.");
      return false;
    default:
      return false;
  }
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
  const SendResult prepared = prepareTransfer(name);
  if (prepared == SEND_OK) {
    g_upState = UP_CRC;
  } else if (prepared == SEND_RETRY) {
    scheduleRetry();
    g_upState = UP_CLOSE;
  } else if (!handlePartVerdict(prepared, name)) {
    g_upState = UP_CLOSE;
  }
  // Переименованная часть: остаёмся в UP_WIFI, следующий вызов возьмёт другую.
}

void finishTransfer(SendResult result) {
  printUploadPerf(result == SEND_OK ? "ok" : "failed");
  closeTransfer();
  g_diag.upload_duration_ms += millis() - g_transferStartedMs;
  if (result == SEND_OK) {
    ++g_sentParts;
    g_uploadFailures = 0;
    g_upState = markDelivered(g_upName) ? UP_WIFI : UP_CLOSE;  // без пометки файл шёл бы по кругу
    return;
  }
  if (result == SEND_RETRY) {
    scheduleRetry();
    g_upState = UP_CLOSE;
    return;
  }
  g_upState = handlePartVerdict(result, g_upName) ? UP_WIFI : UP_CLOSE;
}

// Вызывается после каждой остановки записи и при старте: пробуем отдать всё.
void armUpload() {
  g_uploadArmed = true;
}

// START: закрываем соединение; позже сервер сообщит, откуда докачивать.
void preemptUpload() {
  if (g_upState == UP_IDLE) {
    return;
  }
  Serial.printf("[up] Запись началась, отправка %s прервана.\n", g_upName.c_str());
  // Заморозить числа сейчас, распечатать после STOP: не задерживать START
  // дополнительными строками Serial.
  freezeUploadPerf();
  closeTransfer();
  disconnectWifi();
  g_upState = UP_IDLE;
  ++g_diag.upload_preempted;
  g_nextUploadAttemptMs = millis();
}

void serviceUpload() {
  if (g_upPerf.active && g_upPerf.ended) printUploadPerf("preempted");
  const uint32_t now = millis();
  const bool recording = g_state == ST_RECORDING;
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
      pickWifiFrom(g_wifiIdx);
      g_wifiReason = 0;
      WiFi.mode(WIFI_STA);
      // Без сна радио на время отправки: подтверждения сервера не ждут
      // пробуждения. Wi-Fi включён только пока идёт передача.
      WiFi.setSleep(false);
      WiFi.begin(g_wifiSsids[g_wifiIdx].c_str(), g_wifiPasses[g_wifiIdx].c_str());
      g_wifiStartedMs = now;
      g_ntpRequested = false;
      g_upState = UP_WIFI;
      Serial.printf("[net] Подключение к %s...\n", g_wifiSsids[g_wifiIdx].c_str());
      return;
    }
    case UP_WIFI: {
      if (WiFi.status() != WL_CONNECTED) {
        if (now - g_wifiStartedMs >= upl::WIFI_CONNECT_TIMEOUT_MS) {
          // Причина отличает антенну и 5 ГГц (сеть не видна) от пароля.
          const wl_status_t st = WiFi.status();
          const uint8_t why = g_wifiReason;
          Serial.printf("[net] Сеть недоступна: %s (status %d, reason %u).\n",
                        st == WL_NO_SSID_AVAIL || why == 201 ? "сеть не видна — антенна, 5 ГГц или имя"
                        : st == WL_CONNECT_FAILED || why == 15 || why == 204 || why == 2
                            ? "сеть видна, не пускает — пароль"
                            : "нет ответа",
                        static_cast<int>(st), static_cast<unsigned>(why));
          pickWifiFrom(g_wifiIdx + 1);
          if (++g_wifiTried < wifiCount()) {
            g_uploadArmed = true;  // следующая сеть — сразу, без паузы
          } else {
            g_wifiTried = 0;       // не ответила ни одна: обычная пауза
            scheduleRetry();
          }
          g_upState = UP_CLOSE;
        }
        return;
      }
      if (!g_ntpRequested) {
        // Часы — только когда сеть уже есть ради отправки, никогда ради времени.
        g_wifiTried = 0;
        Serial.printf("[net] Подключено к %s, IP %s, сигнал %d dBm\n", g_wifiSsids[g_wifiIdx].c_str(),
                      WiFi.localIP().toString().c_str(), static_cast<int>(WiFi.RSSI()));
        configTime(0, 0, upl::NTP_SERVER);
        g_ntpRequested = true;
      }
      nextPartOrClose();
      return;
    }
    case UP_CRC: {
      const int chunks = recording ? upl::CRC_CHUNKS_PER_STEP_RECORDING
                                   : upl::CRC_CHUNKS_PER_STEP_IDLE;
      const CrcStep step = serviceCrc(chunks);
      if (step == CRC_MORE) {
        return;
      }
      if (step == CRC_ERROR) {
        // Часть остаётся .WAV и уйдёт заново: читать её сейчас нечем.
        finishTransfer(SEND_RETRY);
        return;
      }
      g_upPerf.crcWallUs = uploadClockUs() - g_upPerf.started;
      g_upState = UP_CONNECT;
      return;  // дать loop() обработать REC перед блокирующим connect()
    }
    case UP_CONNECT: {
      if (uploadButtonPending()) return;
      const SendResult opened = openConnection();
      if (opened == SEND_OK) {
        g_upState = UP_PROBE;
      } else {
        finishTransfer(opened);
      }
      return;
    }
    case UP_PROBE: {
      readUploadReply();
      if (!replyComplete()) {
        const bool closed = !g_client.connected() && g_client.available() == 0;
        if (closed || now - g_replyStartedMs >= upl::REPLY_TIMEOUT_MS) {
          Serial.println("[up] Сервер не ответил на вопрос о докачке.");
          finishTransfer(SEND_RETRY);
        }
        return;
      }
      // Только первый раз: после раннего выхода ради кнопки ответ уже собран.
      if (g_upPerf.probing) {
        g_upPerf.probeUs = uploadClockUs() - g_upPerf.probeStarted;
        g_upPerf.probing = false;
      }
      if (uploadButtonPending()) return;
      const int status = g_reply.startsWith("HTTP/1.") ? g_reply.substring(9, 12).toInt() : 0;
      const int split = g_reply.indexOf("\r\n\r\n");
      const String body = g_reply.substring(split + 4);
      uint32_t offset = 0;
      if (status == 200 && body.indexOf("\"stored\"") >= 0) {
        // Часть уже принята, а ответ на прошлую попытку потерялся.
        Serial.println("[up] Сервер уже принял эту часть.");
        finishTransfer(classifyReply());
        return;
      }
      if (status == 200) {
        if (!uploadpolicy::parseOffsetReply(body.c_str(), g_upSize, &offset)) {
          offset = 0;  // непонятный ответ: надёжнее с нуля
        }
      } else if (status != 404) {  // 404 — старый сервер без докачки: с нуля
        finishTransfer(classifyReply());
        return;
      }
      const SendResult sent = sendUploadHeaders(offset);
      if (sent != SEND_OK) {
        finishTransfer(sent);
        return;
      }
      g_upState = g_upSent >= g_upSize ? UP_REPLY : UP_SEND;
      g_replyStartedMs = millis();
      g_upPerf.bodyStarted = uploadClockUs();
      g_upPerf.sending = g_upState == UP_SEND;
      g_upPerf.replying = g_upState == UP_REPLY;
      g_upPerf.replyStarted = g_upPerf.bodyStarted;
      return;
    }
    case UP_SEND: {
      const int chunks = recording ? upl::SEND_CHUNKS_PER_STEP_RECORDING
                                   : upl::SEND_CHUNKS_PER_STEP_IDLE;
      if (sendBodyChunks(chunks) != SEND_OK) {
        finishTransfer(SEND_RETRY);
        return;
      }
      if (g_upSent >= g_upSize) {
        g_replyStartedMs = millis();
        g_upPerf.bodyUs = uploadClockUs() - g_upPerf.bodyStarted;
        g_upPerf.sending = false;
        g_upPerf.replying = true;
        g_upPerf.replyStarted = uploadClockUs();
        g_upState = UP_REPLY;
      }
      return;
    }
    case UP_REPLY: {
      readUploadReply();
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

// Правила и порядок — session::cleanupSent (проверено на хосте). Здесь только
// пороги и сигнал владельцу. SD.usedBytes() на FAT32 читает таблицу целиком —
// секунды на 32 ГБ, не измерено; поэтому вызывается после STOP и при старте,
// но не перед START.
void cleanupSentParts() {
  if (!g_sdReady) {
    return;
  }
  const session::CleanupResult r = session::cleanupSent(
      g_sd, cfg::CLEANUP_MIN_FREE_BYTES, cfg::CLEANUP_TARGET_FREE_BYTES, serialLog);
  g_cardLow = r.cardLow;
  if (g_cardLow) {
    feedbackEvent("error");
  }
}
