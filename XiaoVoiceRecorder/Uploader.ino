/*
 * Отправка готовых частей на личный сервер. Контракт: shared/protocol.md.
 *
 * Wi-Fi поднимается только когда запись остановлена, и гасится сразу после
 * того, как очередь опустела или попытка провалилась. Отправка одной части
 * блокирующая: устройство в этот момент простаивает, а состояние с ожиданием
 * ответа по кусочкам стоило бы втрое больше кода при той же надёжности.
 */

#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_rom_crc.h>

#include "Certificates.h"
#include "Uploader.h"

namespace upl {
constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 20000;
constexpr uint32_t RETRY_INTERVAL_MS = 5UL * 60UL * 1000UL;
constexpr uint32_t HTTP_TIMEOUT_MS = 60000;
constexpr size_t SEND_CHUNK_BYTES = 4096;
constexpr const char *NVS_NAMESPACE = "recorder";
}

Preferences g_prefs;

String g_wifiSsid;
String g_wifiPass;
String g_serverUrl;
String g_deviceToken;
String g_deviceId;

bool g_uploadArmed = false;         // запись только что остановлена, пробуем сразу
uint32_t g_nextUploadAttemptMs = 0;
bool g_uploadBlocked = false;       // отказ, который повтором не лечится
uint32_t g_sentParts = 0;
uint32_t g_failedParts = 0;

void loadUploadConfig() {
  g_prefs.begin(upl::NVS_NAMESPACE, true);
  g_wifiSsid = g_prefs.getString("wifi_ssid", "");
  g_wifiPass = g_prefs.getString("wifi_pass", "");
  g_serverUrl = g_prefs.getString("server_url", "");
  g_deviceToken = g_prefs.getString("device_token", "");
  g_deviceId = g_prefs.getString("device_id", "xiao-01");
  g_prefs.end();
}

// Значения приходят строками вида key=value через Serial. Пароль и токен
// обратно не печатаются никогда.
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
      Serial.printf("[cfg] %s сохранено\n", name);
      return true;
    }
  }
  return false;
}

void printUploadConfig() {
  Serial.println("--- UPLOAD CONFIG ---");
  Serial.printf("device_id:    %s\n", g_deviceId.c_str());
  Serial.printf("wifi_ssid:    %s\n",
                g_wifiSsid.isEmpty() ? "(не задано)" : g_wifiSsid.c_str());
  Serial.printf("wifi_pass:    %s\n", g_wifiPass.isEmpty() ? "(не задано)" : "(задан)");
  Serial.printf("server_url:   %s\n",
                g_serverUrl.isEmpty() ? "(не задано)" : g_serverUrl.c_str());
  Serial.printf("device_token: %s\n", g_deviceToken.isEmpty() ? "(не задано)" : "(задан)");
  Serial.printf("Отправлено частей: %lu, отказов: %lu\n",
                static_cast<unsigned long>(g_sentParts),
                static_cast<unsigned long>(g_failedParts));
  Serial.printf("Состояние: %s\n",
                g_uploadBlocked ? "остановлено до смены настроек" : "готово");
  Serial.println("Задать: wifi_ssid=... wifi_pass=... server_url=... device_token=...");
  Serial.println("---------------------");
}

bool uploadConfigured() {
  return !g_wifiSsid.isEmpty() && !g_serverUrl.isEmpty() && !g_deviceToken.isEmpty();
}

// Разбирает REC0007_P01.WAV. Восстановленные части несут _R перед расширением.
bool parsePartName(const String &name, uint16_t &session, uint16_t &part,
                   bool &recovered) {
  if (!name.startsWith("REC") || !name.endsWith(".WAV")) {
    return false;
  }
  if (name.length() != 16 && name.length() != 18) {
    return false;  // RECnnnn_Pnn.WAV либо RECnnnn_Pnn_R.WAV
  }
  if (name.charAt(7) != '_' || name.charAt(8) != 'P') {
    return false;
  }
  recovered = (name.length() == 18);
  if (recovered && name.substring(11, 13) != "_R") {
    return false;
  }
  session = static_cast<uint16_t>(name.substring(3, 7).toInt());
  part = static_cast<uint16_t>(name.substring(9, 11).toInt());
  return session >= 1 && part >= 1;
}

// Старейшая неотправленная часть. Имена сортируются лексикографически, что для
// нашей схемы совпадает с хронологией.
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
      if (parsePartName(name, session, part, recovered) &&
          (best.isEmpty() || name < best)) {
        best = name;
      }
    }
    f.close();
  }
  root.close();
  return best;
}

uint32_t fileCrc32(const String &path, uint32_t &sizeOut) {
  File f = SD.open(path.c_str(), FILE_READ);
  if (!f) {
    sizeOut = 0;
    return 0;
  }
  uint8_t buffer[upl::SEND_CHUNK_BYTES];
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

bool connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(g_wifiSsid.c_str(), g_wifiPass.c_str());
  Serial.printf("[net] Подключение к %s...\n", g_wifiSsid.c_str());

  const uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED &&
         millis() - started < upl::WIFI_CONNECT_TIMEOUT_MS) {
    delay(200);
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[net] Сеть недоступна.");
    return false;
  }
  Serial.printf("[net] Подключено, IP %s\n", WiFi.localIP().toString().c_str());
  return true;
}

void disconnectWifi() {
  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_OFF);
}

SendResult sendPart(const String &name) {
  uint16_t session = 0, part = 0;
  bool recovered = false;
  if (!parsePartName(name, session, part, recovered)) {
    return SEND_FATAL;
  }

  const String path = "/" + name;
  uint32_t size = 0;
  const uint32_t crc = fileCrc32(path, size);
  if (size == 0) {
    Serial.printf("[up] %s пустой или не читается\n", name.c_str());
    return SEND_FATAL;
  }

  File f = SD.open(path.c_str(), FILE_READ);
  if (!f) {
    return SEND_RETRY;
  }

  WiFiClientSecure client;
  client.setCACert(ISRG_ROOT_X1);
  client.setTimeout(upl::HTTP_TIMEOUT_MS / 1000);

  HTTPClient http;
  http.setTimeout(upl::HTTP_TIMEOUT_MS);
  if (!http.begin(client, g_serverUrl + "/api/v1/upload")) {
    f.close();
    Serial.println("[up] Некорректный server_url.");
    return SEND_FATAL;
  }

  char buf[16];
  http.addHeader("Authorization", "Bearer " + g_deviceToken);
  http.addHeader("Content-Type", "application/octet-stream");
  http.addHeader("X-Device-Id", g_deviceId);
  snprintf(buf, sizeof(buf), "%u", session);
  http.addHeader("X-Session", buf);
  snprintf(buf, sizeof(buf), "%u", part);
  http.addHeader("X-Part", buf);
  http.addHeader("X-Recovered", recovered ? "1" : "0");
  snprintf(buf, sizeof(buf), "%08lx", static_cast<unsigned long>(crc));
  http.addHeader("X-Crc32", buf);

  Serial.printf("[up] %s -> сервер, %lu байт, crc %08lx\n", name.c_str(),
                static_cast<unsigned long>(size),
                static_cast<unsigned long>(crc));

  const int status = http.sendRequest("POST", &f, size);
  const String reply = http.getString();
  http.end();
  f.close();

  if (status == 201 || status == 200) {
    // Сервер обязан вернуть размер и сумму того, что у него лежит. Без сверки
    // пометка о доставке была бы верой на слово.
    snprintf(buf, sizeof(buf), "%08lx", static_cast<unsigned long>(crc));
    const bool crcEchoed = reply.indexOf(String("\"crc32\":\"") + buf + "\"") >= 0;
    const bool sizeEchoed =
        reply.indexOf("\"bytes\":" + String(static_cast<unsigned long>(size))) >= 0;
    if (!crcEchoed || !sizeEchoed) {
      Serial.printf("[up] Сервер подтвердил не то: %s\n", reply.c_str());
      return SEND_RETRY;
    }
    return SEND_OK;
  }

  if (status == 401 || status == 400 || status == 409 || status == 413) {
    Serial.printf("[up] Отказ %d, повтор не поможет: %s\n", status, reply.c_str());
    return SEND_FATAL;
  }

  Serial.printf("[up] Ошибка %d, попробуем позже\n", status);
  return SEND_RETRY;
}

bool markDelivered(const String &name) {
  const String from = "/" + name;
  String to = from;
  to.replace(".WAV", ".SENT");
  if (!SD.rename(from.c_str(), to.c_str())) {
    // Без пометки та же часть уйдёт снова. Сервер её опознает как дубликат и
    // не создаст второй записи, но трафик будет потрачен впустую.
    Serial.printf("[up] ВНИМАНИЕ: не удалось переименовать %s\n", name.c_str());
    return false;
  }
  Serial.printf("[up] Доставлено: %s -> %s\n", name.c_str(), to.c_str());
  return true;
}

// Вызывается после каждой остановки записи: пробуем отдать всё сразу.
void armUpload() {
  g_uploadArmed = true;
  g_nextUploadAttemptMs = millis();
}

void serviceUpload() {
  if (g_recording || !g_sdReady || g_uploadBlocked) {
    return;
  }
  if (!uploadConfigured()) {
    return;
  }
  if (!g_uploadArmed && static_cast<int32_t>(millis() - g_nextUploadAttemptMs) < 0) {
    return;
  }
  g_uploadArmed = false;

  if (findOldestReadyPart().isEmpty()) {
    g_nextUploadAttemptMs = millis() + upl::RETRY_INTERVAL_MS;
    return;
  }

  if (!connectWifi()) {
    disconnectWifi();
    g_nextUploadAttemptMs = millis() + upl::RETRY_INTERVAL_MS;
    return;
  }

  while (true) {
    // Нажатие REC важнее очереди отправки: пользователь ждать не должен.
    handleSerial();
    handleOptionalButton();
    if (g_recording) {
      Serial.println("[up] Запись началась, отправка отложена.");
      break;
    }

    const String name = findOldestReadyPart();
    if (name.isEmpty()) {
      Serial.println("[up] Очередь пуста.");
      break;
    }

    const SendResult result = sendPart(name);
    if (result == SEND_OK) {
      ++g_sentParts;
      if (!markDelivered(name)) {
        break;  // иначе тот же файл пойдёт по кругу
      }
      continue;
    }

    ++g_failedParts;
    if (result == SEND_FATAL) {
      Serial.println("[up] Отправка остановлена до вмешательства владельца.");
      g_uploadBlocked = true;
    }
    break;
  }

  disconnectWifi();
  g_nextUploadAttemptMs = millis() + upl::RETRY_INTERVAL_MS;
}
