#include <Arduino.h>
#include <ESP_I2S.h>
#include <FS.h>
#include <Preferences.h>
#include <SD.h>
#include <SPI.h>

#include "Button.h"
#include "PartMath.h"
#include "RingBuffer.h"
#include "Types.h"
#include "UploadPolicy.h"

/*
 * XIAO ESP32-S3 Sense voice recorder — firmware v0.3
 *
 * Состояния: ARMED -> RECORDING -> ARMED, плюс FAULT.
 *
 *   ARMED      микрофон слушает, PCM идёт в кольцевой буфер в PSRAM,
 *              на карту ничего не пишется. Буфер держит последние
 *              PRE_RECORD_SECONDS звука.
 *   RECORDING  тот же поток PCM из буфера пишется на SD частями по 10 минут.
 *              Часть начинается с содержимого буфера, поэтому запись
 *              физически стартует за ~30 с ДО нажатия.
 *   FAULT      нет карты или микрофона, буфер не выделен: кнопка даёт сигнал
 *              ошибки, Serial работает, чтобы настроить и посмотреть статус.
 *
 * Кнопка одна:
 *   короткое нажатие в ARMED      -> START
 *   короткое нажатие в RECORDING  -> MARK (в REC0007_P01.MRK, секунды от части)
 *   длинное (2 с) в RECORDING     -> STOP, файл REC0007_N08.END с числом частей
 *
 * Захват микрофона живёт в своей задаче FreeRTOS: чтение DMA не ждёт ни SD,
 * ни TLS, ни кнопку. Всё остальное — кооперативно в loop(), приоритет
 * RECORDING > SD WRITE > BUTTON > UPLOAD.
 *
 * Ничего из этого не проверено на настоящей плате. Что можно, проверено на
 * хосте (host_tests/), остальное ждёт XIAO: см. README_RU.md.
 */

namespace cfg {
constexpr uint32_t SERIAL_BAUD = 115200;

constexpr int PDM_CLK_PIN = 42;
constexpr int PDM_DATA_PIN = 41;
constexpr uint32_t SAMPLE_RATE = partmath::SAMPLE_RATE;
constexpr uint16_t BITS_PER_SAMPLE = 16;
constexpr uint16_t CHANNELS = 1;
constexpr size_t FRAME_BYTES = partmath::FRAME_BYTES;
constexpr size_t WAV_HEADER_BYTES = partmath::WAV_HEADER_BYTES;

constexpr int SD_SCK_PIN = 7;
constexpr int SD_MISO_PIN = 8;
constexpr int SD_MOSI_PIN = 9;
// GPIO21 — документированный CS microSD платы Sense. Тот же пин — встроенный
// LED, поэтому он не мигает независимо, пока карта смонтирована.
constexpr int SD_CS_PIN = 21;
constexpr uint32_t SD_SPI_HZ = 10000000;

// Сколько секунд до нажатия попадает в запись. Ёмкость буфера округляется
// вверх до степени двойки (RingBuffer работает маской), поэтому фактическая
// глубина — RING_BYTES / 32000 с: при 30 с это 1 МиБ = 32,8 с.
constexpr uint32_t PRE_RECORD_SECONDS = 30;
constexpr uint32_t ringBytesFor(uint32_t seconds) {
  uint32_t need = seconds * partmath::BYTES_PER_SECOND;
  uint32_t cap = 1;
  while (cap < need) cap <<= 1;
  return cap;
}
constexpr uint32_t RING_BYTES = ringBytesFor(PRE_RECORD_SECONDS);
// Если PSRAM не дала буфер: работаем без предзаписи, но не отказываемся писать.
constexpr uint32_t RING_FALLBACK_BYTES = 64 * 1024;

// Чтение микрофона блокирует до полного куска: 1024 байта = 32 мс. DMA ядра
// держит 6 x 240 кадров = 90 мс, кусок должен быть заметно короче.
constexpr size_t MIC_READ_BYTES = 1024;
// Запись на карту из буфера кусками; больше — меньше накладных расходов FAT,
// меньше — короче каждая блокировка loop().
constexpr size_t SD_WRITE_BYTES = 8192;
constexpr int SD_WRITES_PER_LOOP = 4;
constexpr uint32_t FILE_FLUSH_INTERVAL_MS = 1000;
constexpr uint32_t STATUS_INTERVAL_MS = 5000;

constexpr uint16_t MAX_PARTS = partmath::MAX_PARTS;
constexpr uint16_t MAX_SESSIONS = 9999;

// Встроенная кнопка BOOT на GPIO0 с внешней подтяжкой: REC без пайки.
// Удержание при сбросе всё так же уводит в загрузчик.
// Не проверено на нашем экземпляре: подтвердить при первом включении.
constexpr int REC_BUTTON_PIN = 0;
constexpr uint32_t BUTTON_DEBOUNCE_MS = 40;
constexpr uint32_t LONG_PRESS_MS = 2000;

// Автоудаление: только .SENT, только когда свободного места меньше порога,
// старые первыми, пока не станет TARGET. Абсолютные байты, не проценты: цена
// дня записи известна (~1,1 ГБ за 9 ч), доля карты — нет.
constexpr uint64_t CLEANUP_MIN_FREE_BYTES = 1024ULL * 1024ULL * 1024ULL;      // 1 ГиБ
constexpr uint64_t CLEANUP_TARGET_FREE_BYTES = 2048ULL * 1024ULL * 1024ULL;   // 2 ГиБ

constexpr const char *NVS_NAMESPACE = "recorder";
}  // namespace cfg

// Отправка закрытых частей, пока пишется следующая. Экспериментально: TLS и
// чтение с той же карты конкурируют с записью. Пока не измерено на плате —
// выключено; включать только после теста dropped_samples под нагрузкой.
constexpr bool UPLOAD_DURING_RECORDING = false;

// Диагностика для дня приезда платы. Не показатели продукта: числа для
// измерений, печатаются по команде d.
struct Diag {
  uint32_t captured_samples = 0;
  uint32_t written_samples = 0;
  uint32_t dropped_samples = 0;
  uint32_t mic_short_reads = 0;   // микрофон вернул меньше, чем просили
  uint32_t sd_write_max_ms = 0;
  uint32_t sd_write_errors = 0;
  uint32_t buffer_high_watermark = 0;
  uint32_t part_switch_max_ms = 0;
  uint32_t upload_bytes = 0;
  uint32_t upload_duration_ms = 0;
  uint32_t upload_preempted = 0;
};
Diag g_diag;

I2SClass g_mic;
RingBuffer g_ring;
uint8_t *g_ringStorage = nullptr;
bool g_ringInPsram = false;
TaskHandle_t g_captureTask = nullptr;
volatile bool g_captureRunning = false;
volatile bool g_micFault = false;

File g_recordFile;
DeviceState g_state = ST_FAULT;
bool g_audioReady = false;
bool g_sdReady = false;
int g_sdCsPin = -1;
uint32_t g_dataBytes = 0;        // байт PCM в текущей части
uint32_t g_sessionBytes = 0;     // байт PCM, записанных за сессию (все части)
uint32_t g_recordStartedMs = 0;
uint32_t g_lastFlushMs = 0;
uint32_t g_lastStatusMs = 0;
uint16_t g_sessionId = 0;
uint16_t g_partIndex = 0;
uint16_t g_nextSessionId = 1;    // посчитан заранее, START не ходит по каталогу
uint16_t g_marksInSession = 0;
String g_currentPath;
Preferences g_sessionPrefs;

ButtonDecoder g_button(cfg::BUTTON_DEBOUNCE_MS, cfg::LONG_PRESS_MS);
String g_serialLine;

// Определены в Uploader.ino и Feedback.ino; компилятор Arduino дописывает их
// следом за этим файлом.
void loadUploadConfig();
bool setUploadConfig(const String &key, const String &value);
void printUploadConfig();
void armUpload();
void serviceUpload();
void preemptUpload();
bool uploadBusy();
void cleanupSentParts();
void feedbackSetup();
void feedbackService(DeviceState state);
void feedbackEvent(const char *name);
bool setFeedbackPin(const String &key, const String &value);

// --- WAV ------------------------------------------------------------------------

void writeLe16(uint8_t *dst, uint16_t value) {
  dst[0] = static_cast<uint8_t>(value & 0xFF);
  dst[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
}

void writeLe32(uint8_t *dst, uint32_t value) {
  dst[0] = static_cast<uint8_t>(value & 0xFF);
  dst[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
  dst[2] = static_cast<uint8_t>((value >> 16) & 0xFF);
  dst[3] = static_cast<uint8_t>((value >> 24) & 0xFF);
}

uint16_t readLe16(const uint8_t *src) {
  return static_cast<uint16_t>(src[0]) | (static_cast<uint16_t>(src[1]) << 8);
}

uint32_t readLe32(const uint8_t *src) {
  return static_cast<uint32_t>(src[0]) |
         (static_cast<uint32_t>(src[1]) << 8) |
         (static_cast<uint32_t>(src[2]) << 16) |
         (static_cast<uint32_t>(src[3]) << 24);
}

void buildWavHeader(uint8_t header[44], uint32_t pcmDataBytes) {
  memset(header, 0, 44);
  const uint32_t byteRate = cfg::SAMPLE_RATE * cfg::CHANNELS * (cfg::BITS_PER_SAMPLE / 8);
  const uint16_t blockAlign = cfg::CHANNELS * (cfg::BITS_PER_SAMPLE / 8);

  memcpy(header + 0, "RIFF", 4);
  writeLe32(header + 4, 36 + pcmDataBytes);
  memcpy(header + 8, "WAVE", 4);
  memcpy(header + 12, "fmt ", 4);
  writeLe32(header + 16, 16);
  writeLe16(header + 20, 1);
  writeLe16(header + 22, cfg::CHANNELS);
  writeLe32(header + 24, cfg::SAMPLE_RATE);
  writeLe32(header + 28, byteRate);
  writeLe16(header + 32, blockAlign);
  writeLe16(header + 34, cfg::BITS_PER_SAMPLE);
  memcpy(header + 36, "data", 4);
  writeLe32(header + 40, pcmDataBytes);
}

bool writeWavHeader(File &file, uint32_t pcmDataBytes) {
  uint8_t header[44];
  buildWavHeader(header, pcmDataBytes);
  if (!file.seek(0)) {
    return false;
  }
  return file.write(header, sizeof(header)) == sizeof(header);
}

// Принимает только файл, который писала эта прошивка: канонические 44 байта
// и наш формат PCM. Возвращает размер данных, заявленный в заголовке.
bool readOurWavHeader(File &file, uint32_t &declaredDataBytes) {
  uint8_t h[cfg::WAV_HEADER_BYTES];
  if (!file.seek(0)) {
    return false;
  }
  if (file.read(h, sizeof(h)) != static_cast<int>(sizeof(h))) {
    return false;
  }
  if (memcmp(h + 0, "RIFF", 4) != 0 || memcmp(h + 8, "WAVE", 4) != 0 ||
      memcmp(h + 12, "fmt ", 4) != 0 || memcmp(h + 36, "data", 4) != 0) {
    return false;
  }
  if (readLe16(h + 20) != 1 ||
      readLe16(h + 22) != cfg::CHANNELS ||
      readLe32(h + 24) != cfg::SAMPLE_RATE ||
      readLe16(h + 34) != cfg::BITS_PER_SAMPLE) {
    return false;
  }
  declaredDataBytes = readLe32(h + 40);
  return true;
}

// --- микрофон и кольцевой буфер ------------------------------------------------------

// Задача захвата: единственный писатель буфера. Ничего не решает, только
// переливает DMA -> кольцо. Приоритет выше loop(), чтобы SD и TLS не
// задерживали чтение DMA.
void captureTask(void *) {
  static uint8_t chunk[cfg::MIC_READ_BYTES];
  while (g_captureRunning) {
    size_t got = g_mic.readBytes(reinterpret_cast<char *>(chunk), sizeof(chunk));
    got = partmath::wholeFrames(static_cast<uint32_t>(got));
    if (got == 0) {
      // Таймаут чтения (1 с в ядре): микрофон молчит. Не крутим ядро вхолостую.
      g_micFault = true;
      ++g_diag.mic_short_reads;
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    if (got < sizeof(chunk)) {
      ++g_diag.mic_short_reads;
    }
    g_ring.push(chunk, static_cast<uint32_t>(got));
    g_diag.captured_samples += static_cast<uint32_t>(got / cfg::FRAME_BYTES);
  }
  g_captureTask = nullptr;
  vTaskDelete(nullptr);
}

bool allocateRing() {
  g_ringStorage = static_cast<uint8_t *>(ps_malloc(cfg::RING_BYTES));
  g_ringInPsram = g_ringStorage != nullptr;
  uint32_t bytes = cfg::RING_BYTES;
  if (g_ringStorage == nullptr) {
    Serial.printf("[ring] PSRAM не дала %lu байт, буфер во внутренней памяти %lu байт.\n",
                  static_cast<unsigned long>(cfg::RING_BYTES),
                  static_cast<unsigned long>(cfg::RING_FALLBACK_BYTES));
    g_ringStorage = static_cast<uint8_t *>(malloc(cfg::RING_FALLBACK_BYTES));
    bytes = cfg::RING_FALLBACK_BYTES;
  }
  if (g_ringStorage == nullptr || !g_ring.init(g_ringStorage, bytes)) {
    Serial.println("[ring] ERROR: буфер не выделен.");
    return false;
  }
  Serial.printf("[ring] %lu байт (%s), предзапись %.1f с\n",
                static_cast<unsigned long>(bytes), g_ringInPsram ? "PSRAM" : "heap",
                partmath::seconds(bytes));
  return true;
}

bool startCapture() {
  if (g_captureRunning) {
    return true;
  }
  g_mic.setPinsPdmRx(cfg::PDM_CLK_PIN, cfg::PDM_DATA_PIN);
  if (!g_mic.begin(I2S_MODE_PDM_RX, cfg::SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT,
                   I2S_SLOT_MODE_MONO)) {
    Serial.println("[audio] ERROR: I2S/PDM start failed.");
    return false;
  }
  g_micFault = false;
  g_captureRunning = true;
  // Ядро 1 — то же, что у loop(): Wi-Fi живёт на ядре 0 и не отнимает такты.
  const BaseType_t ok = xTaskCreatePinnedToCore(captureTask, "capture", 4096, nullptr,
                                                configMAX_PRIORITIES - 3, &g_captureTask, 1);
  if (ok != pdPASS) {
    g_captureRunning = false;
    g_mic.end();
    Serial.println("[audio] ERROR: задача захвата не создана.");
    return false;
  }
  return true;
}

void stopCapture() {
  if (!g_captureRunning) {
    return;
  }
  g_captureRunning = false;
  // Задача выходит после текущего readBytes (не дольше таймаута ядра, 1 с).
  for (int i = 0; i < 150 && g_captureTask != nullptr; ++i) {
    delay(10);
  }
  g_mic.end();
}

// --- microSD -----------------------------------------------------------------------------

bool tryMountSd(int csPin) {
  SD.end();
  SPI.end();
  delay(20);
  SPI.begin(cfg::SD_SCK_PIN, cfg::SD_MISO_PIN, cfg::SD_MOSI_PIN, csPin);
  if (!SD.begin(csPin, SPI, cfg::SD_SPI_HZ)) {
    return false;
  }
  if (SD.cardType() == CARD_NONE) {
    SD.end();
    return false;
  }
  return true;
}

bool initSd() {
  Serial.printf("[sd] Mounting microSD, CS=GPIO%d...\n", cfg::SD_CS_PIN);
  if (tryMountSd(cfg::SD_CS_PIN)) {
    g_sdCsPin = cfg::SD_CS_PIN;
    const uint64_t sizeMb = SD.cardSize() / (1024ULL * 1024ULL);
    Serial.printf("[sd] Ready. CS=GPIO%d, card=%llu MB\n",
                  cfg::SD_CS_PIN, static_cast<unsigned long long>(sizeMb));
    return true;
  }
  Serial.printf("[sd] ERROR: card not mounted with CS=GPIO%d.\n", cfg::SD_CS_PIN);
  Serial.println("[sd] Check that the Sense expansion board is installed and card is FAT32.");
  return false;
}

// File::name() returns a bare name on some cores and a full path on others.
String baseName(File &file) {
  const String name = file.name();
  const int slash = name.lastIndexOf('/');
  return slash >= 0 ? name.substring(slash + 1) : name;
}

// /REC0007_P03.WAV — сессия 7, часть 3. Часть пишется как .TMP и получает
// .WAV только после чистого закрытия; сбой — .BAD; восстановленная — _R.WAV.
String partPath(uint16_t session, uint16_t part, const char *ext) {
  char buf[32];
  snprintf(buf, sizeof(buf), "/REC%04u_P%02u.%s", session, part, ext);
  return String(buf);
}

String endPath(uint16_t session, uint16_t parts) {
  char buf[32];
  snprintf(buf, sizeof(buf), "/REC%04u_N%02u.END", session, parts);
  return String(buf);
}

// Наибольший номер сессии на карте. Один проход по каталогу вместо 9999 exists().
uint16_t maxSessionOnCard() {
  uint16_t maxSeen = 0;
  File root = SD.open("/");
  if (!root || !root.isDirectory()) {
    return 0;
  }
  for (File f = root.openNextFile(); f; f = root.openNextFile()) {
    const String name = baseName(f);
    if (name.startsWith("REC") && name.length() >= 7) {
      const uint16_t n = static_cast<uint16_t>(name.substring(3, 7).toInt());
      if (n > maxSeen) {
        maxSeen = n;
      }
    }
    f.close();
  }
  root.close();
  return maxSeen;
}

// Номер следующей сессии: больше всего на карте и всего, что уже выдавали
// (NVS). Иначе после автоудаления старых .SENT номер мог бы повториться, а
// сервер ключует части по (устройство, сессия, часть) и ответил бы 409.
void refreshNextSessionId() {
  g_sessionPrefs.begin(cfg::NVS_NAMESPACE, true);
  const uint16_t issued = static_cast<uint16_t>(g_sessionPrefs.getUShort("last_session", 0));
  g_sessionPrefs.end();
  const uint16_t onCard = g_sdReady ? maxSessionOnCard() : 0;
  g_nextSessionId = (issued > onCard ? issued : onCard) + 1;
}

void rememberSessionId(uint16_t session) {
  g_sessionPrefs.begin(cfg::NVS_NAMESPACE, false);
  g_sessionPrefs.putUShort("last_session", session);
  g_sessionPrefs.end();
}

// Оставшийся .TMP значит, что питание пропало во время записи: заголовок
// заявляет ноль байт. Чиним заголовок по реальному размеру и помечаем часть
// восстановленной. Выживает только то, что дошло до карты.
void recoverUnfinishedRecordings() {
  File root = SD.open("/");
  if (!root || !root.isDirectory()) {
    Serial.println("[recover] Cannot open root directory.");
    return;
  }

  // Переименовывать во время обхода каталога небезопасно: сначала список.
  constexpr int MAX_REPAIRS = 16;
  String names[MAX_REPAIRS];
  uint32_t sizes[MAX_REPAIRS];
  int found = 0;

  for (File f = root.openNextFile(); f; f = root.openNextFile()) {
    if (f.isDirectory()) {
      f.close();
      continue;
    }
    const String name = baseName(f);
    if (name.startsWith("REC") && name.endsWith(".TMP") && found < MAX_REPAIRS) {
      const uint32_t total = static_cast<uint32_t>(f.size());
      uint32_t audio = total > cfg::WAV_HEADER_BYTES ? total - cfg::WAV_HEADER_BYTES : 0;
      audio -= audio % cfg::FRAME_BYTES;
      names[found] = name;
      sizes[found] = audio;
      ++found;
    }
    f.close();
  }
  root.close();

  if (found == 0) {
    Serial.println("[recover] No unfinished parts.");
    return;
  }

  for (int i = 0; i < found; ++i) {
    const String tmpPath = "/" + names[i];
    File f = SD.open(tmpPath.c_str(), "r+");  // FILE_WRITE обнулил бы файл
    if (!f) {
      Serial.printf("[recover] ERROR: cannot reopen %s\n", tmpPath.c_str());
      continue;
    }
    uint32_t declared = 0;
    const bool ours = readOurWavHeader(f, declared);
    const bool ok = ours && writeWavHeader(f, sizes[i]);
    f.flush();
    f.close();

    if (!ok) {
      Serial.printf("[recover] SKIP %s (%s)\n", tmpPath.c_str(),
                    ours ? "header rewrite failed" : "not our PCM format");
      continue;
    }

    String recoveredPath = tmpPath;
    recoveredPath.replace(".TMP", "_R.WAV");
    const bool renamed = SD.rename(tmpPath.c_str(), recoveredPath.c_str());
    Serial.printf("[recover] %s -> %s | %lu audio bytes | renamed=%s\n",
                  tmpPath.c_str(), recoveredPath.c_str(),
                  static_cast<unsigned long>(sizes[i]), renamed ? "yes" : "NO");
  }
}

// --- запись -------------------------------------------------------------------------------

bool openPart() {
  g_currentPath = partPath(g_sessionId, g_partIndex, "TMP");
  g_recordFile = SD.open(g_currentPath.c_str(), FILE_WRITE);
  if (!g_recordFile) {
    Serial.printf("[rec] ERROR: cannot create %s\n", g_currentPath.c_str());
    return false;
  }
  uint8_t header[cfg::WAV_HEADER_BYTES];
  buildWavHeader(header, 0);
  if (g_recordFile.write(header, sizeof(header)) != sizeof(header)) {
    Serial.println("[rec] ERROR: cannot write WAV header.");
    g_recordFile.close();
    return false;
  }
  g_dataBytes = 0;
  g_lastFlushMs = millis();
  return true;
}

// Закрывает открытую часть. Только чистое закрытие даёт имя .WAV; всё
// остальное — .BAD, чтобы битая часть никогда не выглядела готовой.
bool closePart(bool healthy) {
  g_recordFile.flush();
  const bool headerOk = writeWavHeader(g_recordFile, g_dataBytes);
  g_recordFile.flush();
  g_recordFile.close();

  const bool ok = healthy && headerOk;
  String finalPath = g_currentPath;
  finalPath.replace(".TMP", ok ? ".WAV" : ".BAD");
  const bool renamed = SD.rename(g_currentPath.c_str(), finalPath.c_str());
  g_sessionBytes += g_dataBytes;

  Serial.printf("[rec] %s %s | %lu audio bytes | header=%s | renamed=%s\n",
                ok ? "PART OK" : "PART FAILED",
                renamed ? finalPath.c_str() : g_currentPath.c_str(),
                static_cast<unsigned long>(g_dataBytes),
                headerOk ? "ok" : "BAD",
                renamed ? "yes" : "NO");
  return ok && renamed;
}

// Метка: смещение в секундах от начала своей части. Позиция «сейчас» —
// всё, что записано за сессию, плюс то, что ещё лежит в буфере и будет
// записано следом. Если это уже за границей части, метка достанется
// следующей, куда и попадёт сам звук.
bool writeMark() {
  // Части вращаются ровно на PART_MAX_BYTES, поэтому номер части — чистая
  // арифметика от позиции в сессии.
  const uint32_t pos = g_sessionBytes + g_dataBytes + g_ring.available();
  const uint16_t part = partmath::partForPosition(pos);
  const uint32_t offsetBytes = partmath::offsetInPart(pos);

  char text[24];
  const int len = partmath::formatMark(text, sizeof(text) - 1, offsetBytes);
  text[len] = '\n';

  const String path = partPath(g_sessionId, part, "MRK");
  File f = SD.open(path.c_str(), FILE_APPEND);
  if (!f) {
    Serial.printf("[mark] ERROR: cannot open %s\n", path.c_str());
    return false;
  }
  const bool ok = f.write(reinterpret_cast<const uint8_t *>(text), len + 1) ==
                  static_cast<size_t>(len + 1);
  f.flush();  // нажатие должно пережить пропадание питания сразу после него
  f.close();
  text[len] = '\0';
  ++g_marksInSession;
  Serial.printf("[mark] %s <- %s (%u в сессии)\n", path.c_str(), text, g_marksInSession);
  return ok;
}

bool startRecording() {
  if (g_state == ST_RECORDING) {
    return true;
  }
  if (g_state != ST_ARMED || !g_sdReady) {
    Serial.println("[rec] Cannot start: device is not armed.");
    feedbackEvent("error");
    return false;
  }
  if (g_nextSessionId > cfg::MAX_SESSIONS) {
    Serial.println("[rec] ERROR: session numbers exhausted. Clear the card.");
    feedbackEvent("error");
    return false;
  }

  // Отправка мешала бы записи — обрывается сразу, часть уйдёт заново позже.
  if (!UPLOAD_DURING_RECORDING && uploadBusy()) {
    preemptUpload();
  }

  g_sessionId = g_nextSessionId;
  g_partIndex = 1;
  g_sessionBytes = 0;
  g_marksInSession = 0;
  g_ring.resetStats();
  // Всё, что буфер держит сейчас, — предзапись: становится началом части 1.
  g_ring.resync();
  const uint32_t preRoll = g_ring.available();

  if (!openPart()) {
    feedbackEvent("error");
    return false;
  }
  rememberSessionId(g_sessionId);

  g_recordStartedMs = millis();
  g_lastStatusMs = g_recordStartedMs;
  g_state = ST_RECORDING;
  feedbackEvent("start");
  Serial.printf("[rec] START session %04u -> %s | предзапись %.1f с\n",
                g_sessionId, g_currentPath.c_str(), partmath::seconds(preRoll));
  return true;
}

// Одна запись из буфера на карту: не больше SD_WRITE_BYTES и не дальше
// границы части. Возвращает false при ошибке карты.
bool writeChunkFromRing(uint32_t limit) {
  static uint8_t chunk[cfg::SD_WRITE_BYTES];
  uint32_t want = limit < sizeof(chunk) ? limit : sizeof(chunk);
  const uint32_t room = partmath::roomInPart(g_dataBytes);
  if (want > room) {
    want = room;
  }
  want = partmath::wholeFrames(want);
  if (want == 0) {
    return true;
  }
  const uint32_t got = g_ring.pop(chunk, want);
  if (got == 0) {
    return true;
  }

  const uint32_t t0 = millis();
  const size_t written = g_recordFile.write(chunk, got);
  const uint32_t dt = millis() - t0;
  if (dt > g_diag.sd_write_max_ms) {
    g_diag.sd_write_max_ms = dt;
  }

  // Считаем то, что реально дошло до карты, чтобы заголовок описывал файл.
  const uint32_t kept = partmath::wholeFrames(static_cast<uint32_t>(written));
  g_dataBytes += kept;
  g_diag.written_samples += kept / cfg::FRAME_BYTES;
  if (written != got) {
    ++g_diag.sd_write_errors;
    Serial.printf("[rec] ERROR: SD write incomplete (%u of %u bytes).\n",
                  static_cast<unsigned>(written), static_cast<unsigned>(got));
    return false;
  }
  return true;
}

bool rotatePart() {
  const uint32_t t0 = millis();
  if (!closePart(true)) {
    Serial.println("[rec] ERROR: could not finish part.");
    return false;
  }
  ++g_partIndex;
  if (!openPart()) {
    Serial.println("[rec] ERROR: could not open next part.");
    return false;
  }
  const uint32_t dt = millis() - t0;
  if (dt > g_diag.part_switch_max_ms) {
    g_diag.part_switch_max_ms = dt;
  }
  Serial.printf("[rec] NEXT PART -> %s (%lu ms)\n", g_currentPath.c_str(),
                static_cast<unsigned long>(dt));
  return true;
}

void stopRecording(StopReason reason) {
  if (g_state != ST_RECORDING) {
    return;
  }
  bool healthy = reason == STOP_NORMAL;

  // Дописываем ровно то, что накопилось к моменту нажатия. Задача захвата
  // продолжает писать в буфер: это уже предзапись для следующей сессии.
  uint32_t pending = healthy ? g_ring.available() : 0;
  while (pending > 0 && healthy) {
    if (partmath::roomInPart(g_dataBytes) == 0) {
      if (g_partIndex >= cfg::MAX_PARTS) {
        Serial.println("[rec] Part limit: tail of the buffer is not stored.");
        break;
      }
      if (!rotatePart()) {
        healthy = false;
        break;
      }
    }
    const uint32_t before = g_dataBytes;
    if (!writeChunkFromRing(pending)) {
      healthy = false;
      break;
    }
    if (g_dataBytes == before) {
      break;  // буфер не отдал ни байта: не крутимся вечно
    }
    pending -= (g_dataBytes - before);
  }

  closePart(healthy);
  g_state = ST_ARMED;

  if (healthy) {
    // Число частей должно пережить перезагрузку до отправки: пустой файл с
    // числом в имени, содержимое не читается (shared/protocol.md).
    const String end = endPath(g_sessionId, g_partIndex);
    File f = SD.open(end.c_str(), FILE_WRITE);
    if (f) {
      f.close();
    } else {
      Serial.printf("[rec] WARNING: cannot create %s\n", end.c_str());
    }
    feedbackEvent("stop");
  } else {
    feedbackEvent("error");
  }

  const uint32_t elapsedSec = (millis() - g_recordStartedMs) / 1000;
  Serial.printf("[rec] STOP session %04u | %u parts | %lu s | %lu audio bytes | marks %u | dropped %lu samples\n",
                g_sessionId, g_partIndex,
                static_cast<unsigned long>(elapsedSec),
                static_cast<unsigned long>(g_sessionBytes),
                g_marksInSession,
                static_cast<unsigned long>(g_ring.dropped() / cfg::FRAME_BYTES));

  g_nextSessionId = g_sessionId + 1;
  cleanupSentParts();
  armUpload();
}

// Переливает буфер на карту ограниченными порциями и вращает части ровно
// на границе PART_MAX_BYTES. Вызывается из loop() каждый оборот.
void serviceRecording() {
  if (g_state != ST_RECORDING) {
    return;
  }
  if (g_micFault) {
    Serial.println("[rec] ERROR: microphone stopped delivering samples.");
    stopRecording(STOP_WRITE_ERROR);
    g_state = ST_FAULT;
    return;
  }

  for (int i = 0; i < cfg::SD_WRITES_PER_LOOP && g_ring.available() >= cfg::SD_WRITE_BYTES; ++i) {
    if (partmath::roomInPart(g_dataBytes) == 0) {
      if (g_partIndex >= cfg::MAX_PARTS) {
        Serial.println("[rec] Part limit reached for this session. Stopping cleanly.");
        stopRecording(STOP_NORMAL);
        return;
      }
      if (!rotatePart()) {
        stopRecording(STOP_WRITE_ERROR);
        return;
      }
    }
    if (!writeChunkFromRing(cfg::SD_WRITE_BYTES)) {
      stopRecording(STOP_WRITE_ERROR);
      return;
    }
  }

  const uint32_t now = millis();
  if (now - g_lastFlushMs >= cfg::FILE_FLUSH_INTERVAL_MS) {
    g_recordFile.flush();
    g_lastFlushMs = now;
  }

  const uint32_t hwm = g_ring.highWatermark();
  if (hwm > g_diag.buffer_high_watermark) {
    g_diag.buffer_high_watermark = hwm;
  }
  g_diag.dropped_samples = g_ring.dropped() / cfg::FRAME_BYTES;

  if (now - g_lastStatusMs >= cfg::STATUS_INTERVAL_MS) {
    const uint32_t sec = (now - g_recordStartedMs) / 1000;
    Serial.printf("[rec] session %04u part %02u | %lu s | %.2f MB | ring %lu B | dropped %lu\n",
                  g_sessionId, g_partIndex, static_cast<unsigned long>(sec),
                  (g_sessionBytes + g_dataBytes) / (1024.0f * 1024.0f),
                  static_cast<unsigned long>(g_ring.available()),
                  static_cast<unsigned long>(g_diag.dropped_samples));
    g_lastStatusMs = now;
  }
}

// Микрофон замолчал вне записи: ARMED без звука — ложное спокойствие.
void serviceHealth() {
  if (g_micFault && g_state == ST_ARMED) {
    Serial.println("[audio] ERROR: microphone stopped delivering samples. FAULT.");
    g_state = ST_FAULT;
    feedbackEvent("error");
  }
}

// --- команды и кнопка ----------------------------------------------------------------------

void onShortPress() {
  switch (g_state) {
    case ST_ARMED:
      startRecording();
      break;
    case ST_RECORDING:
      if (writeMark()) {
        feedbackEvent("mark");
      } else {
        feedbackEvent("error");
      }
      break;
    case ST_FAULT:
      feedbackEvent("error");
      break;
  }
}

void onLongPress() {
  if (g_state == ST_RECORDING) {
    stopRecording(STOP_NORMAL);
  } else {
    Serial.println("[btn] Long press outside RECORDING: ignored.");
  }
}

void handleButton() {
  const bool pressed = digitalRead(cfg::REC_BUTTON_PIN) == LOW;
  switch (g_button.feed(pressed, millis())) {
    case ButtonDecoder::SHORT:
      onShortPress();
      break;
    case ButtonDecoder::LONG:
      onLongPress();
      break;
    default:
      break;
  }
}

void printCardFiles() {
  if (!g_sdReady) {
    Serial.println("[sd] Card is not mounted.");
    return;
  }
  if (g_state == ST_RECORDING) {
    Serial.println("[sd] Not while recording.");
    return;
  }
  File root = SD.open("/");
  if (!root || !root.isDirectory()) {
    Serial.println("[sd] Cannot open root directory.");
    return;
  }
  Serial.println("[sd] Files:");
  for (File file = root.openNextFile(); file; file = root.openNextFile()) {
    if (!file.isDirectory()) {
      Serial.printf("  %-24s %10llu bytes\n", file.name(),
                    static_cast<unsigned long long>(file.size()));
    }
    file.close();
  }
  root.close();
  Serial.printf("[sd] total %llu MB, used %llu MB\n",
                static_cast<unsigned long long>(SD.totalBytes() / (1024ULL * 1024ULL)),
                static_cast<unsigned long long>(SD.usedBytes() / (1024ULL * 1024ULL)));
}

const char *stateName(DeviceState s) {
  switch (s) {
    case ST_ARMED: return "ARMED";
    case ST_RECORDING: return "RECORDING";
    default: return "FAULT";
  }
}

void printStatus() {
  Serial.println("--- STATUS ---");
  Serial.printf("State: %s\n", stateName(g_state));
  Serial.printf("Audio: %s, capture %s\n", g_audioReady ? "ready" : "ERROR",
                g_captureRunning ? "running" : "off");
  Serial.printf("SD:    %s", g_sdReady ? "ready" : "ERROR");
  if (g_sdReady) {
    Serial.printf(" (CS=GPIO%d)", g_sdCsPin);
  }
  Serial.println();
  Serial.printf("Ring:  %lu / %lu bytes (%s), pre-record %.1f s\n",
                static_cast<unsigned long>(g_ring.available()),
                static_cast<unsigned long>(g_ring.capacity()),
                g_ringInPsram ? "PSRAM" : "heap",
                partmath::seconds(g_ring.capacity()));
  if (g_state == ST_RECORDING) {
    Serial.printf("Sess:  %04u, part %02u, marks %u\n", g_sessionId, g_partIndex, g_marksInSession);
    Serial.printf("File:  %s\n", g_currentPath.c_str());
  } else {
    Serial.printf("Next:  session %04u\n", g_nextSessionId);
  }
  time_t now = time(nullptr);
  if (now > 1700000000) {
    struct tm tmNow;
    localtime_r(&now, &tmNow);
    char buf[32];
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S UTC", &tmNow);
    Serial.printf("Time:  %s (NTP)\n", buf);
  } else {
    Serial.println("Time:  unknown (no NTP sync yet)");
  }
  Serial.println("Batt:  unknown (no gauge connected yet)");
  Serial.printf("Heap:  %lu bytes free\n", static_cast<unsigned long>(ESP.getFreeHeap()));
  Serial.printf("PSRAM: %lu / %lu bytes free\n",
                static_cast<unsigned long>(ESP.getFreePsram()),
                static_cast<unsigned long>(ESP.getPsramSize()));
  Serial.println("--------------");
}

void printDiag() {
  Serial.println("--- DIAG (числа для измерений, не показатели продукта) ---");
  Serial.printf("captured_samples      %lu\n", static_cast<unsigned long>(g_diag.captured_samples));
  Serial.printf("written_samples       %lu\n", static_cast<unsigned long>(g_diag.written_samples));
  Serial.printf("dropped_samples       %lu\n", static_cast<unsigned long>(g_ring.dropped() / cfg::FRAME_BYTES));
  Serial.printf("mic_short_reads       %lu\n", static_cast<unsigned long>(g_diag.mic_short_reads));
  Serial.printf("sd_write_max_ms       %lu\n", static_cast<unsigned long>(g_diag.sd_write_max_ms));
  Serial.printf("sd_write_errors       %lu\n", static_cast<unsigned long>(g_diag.sd_write_errors));
  Serial.printf("part_switch_max_ms    %lu\n", static_cast<unsigned long>(g_diag.part_switch_max_ms));
  Serial.printf("buffer_high_watermark %lu of %lu\n",
                static_cast<unsigned long>(g_ring.highWatermark()),
                static_cast<unsigned long>(g_ring.capacity()));
  Serial.printf("upload_bytes          %lu\n", static_cast<unsigned long>(g_diag.upload_bytes));
  Serial.printf("upload_duration_ms    %lu\n", static_cast<unsigned long>(g_diag.upload_duration_ms));
  Serial.printf("upload_preempted      %lu\n", static_cast<unsigned long>(g_diag.upload_preempted));
  Serial.printf("uptime_ms             %lu\n", static_cast<unsigned long>(millis()));
  // Ожидание по часам против фактически захваченного: разница — оценка
  // потерь между DMA и задачей захвата, которых буфер не видит.
  Serial.printf("expected_by_clock     %lu samples\n",
                static_cast<unsigned long>((millis() / 1000UL) * cfg::SAMPLE_RATE));
  Serial.println("------------------------------------------------------------");
}

void printHelp() {
  Serial.println();
  Serial.println("XIAO Voice Recorder v0.3. Команда вводится строкой, затем Enter.");
  Serial.println("  r  START (в ARMED) или STOP (в RECORDING)");
  Serial.println("  m  метка (только в RECORDING)");
  Serial.println("  s  состояние");
  Serial.println("  d  диагностические счётчики");
  Serial.println("  l  файлы на карте (не во время записи)");
  Serial.println("  c  настройки отправки");
  Serial.println("  u  отправить готовые части сейчас");
  Serial.println("  k  очистка .SENT по порогу места");
  Serial.println("  p  микрофон вкл/выкл в ARMED (замер тока без захвата)");
  Serial.println("  h  справка");
  Serial.println("Настройка: wifi_ssid=... wifi_pass=... server_url=...");
  Serial.println("           device_token=... device_id=... led_pin=N vibro_pin=N");
  Serial.println();
}

// Строка key=value настраивает, одиночная буква — команда. Разделение по
// строкам, а не по символам: иначе server_url= сработал бы как команда s.
void processSerialLine(const String &line) {
  const int eq = line.indexOf('=');
  if (eq > 0) {
    const String key = line.substring(0, eq);
    const String value = line.substring(eq + 1);
    if (!setUploadConfig(key, value) && !setFeedbackPin(key, value)) {
      Serial.printf("[cfg] Неизвестный параметр: %s\n", key.c_str());
    }
    return;
  }
  if (line.length() != 1) {
    Serial.println("Неизвестная команда. h — справка.");
    return;
  }
  switch (line.charAt(0)) {
    case 'r': case 'R':
      if (g_state == ST_RECORDING) stopRecording(STOP_NORMAL);
      else startRecording();
      break;
    case 'm': case 'M':
      if (g_state == ST_RECORDING) onShortPress();
      else Serial.println("[mark] Только во время записи.");
      break;
    case 's': case 'S':
      printStatus();
      break;
    case 'd': case 'D':
      printDiag();
      break;
    case 'l': case 'L':
      printCardFiles();
      break;
    case 'c': case 'C':
      printUploadConfig();
      break;
    case 'u': case 'U':
      armUpload();
      Serial.println("[up] Отправка запрошена.");
      break;
    case 'k': case 'K':
      if (g_state == ST_RECORDING) Serial.println("[clean] Не во время записи.");
      else cleanupSentParts();
      break;
    case 'p': case 'P':
      // Только для измерений: ARMED без микрофона — нижняя граница тока.
      if (g_state == ST_RECORDING) {
        Serial.println("[audio] Не во время записи.");
      } else if (g_captureRunning) {
        stopCapture();
        g_ring.discardAll();
        Serial.println("[audio] Захват остановлен, предзаписи нет.");
      } else if (startCapture()) {
        Serial.println("[audio] Захват запущен.");
      }
      break;
    case 'h': case 'H': case '?':
      printHelp();
      break;
    default:
      Serial.println("Неизвестная команда. h — справка.");
      break;
  }
}

void handleSerial() {
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      if (!g_serialLine.isEmpty()) {
        const String line = g_serialLine;
        g_serialLine = "";
        processSerialLine(line);
      }
      continue;
    }
    if (g_serialLine.length() < 160) {
      g_serialLine += c;
    }
  }
}

// --- setup / loop ---------------------------------------------------------------------------

void setup() {
  Serial.begin(cfg::SERIAL_BAUD);
  delay(1200);

  Serial.println();
  Serial.println("====================================");
  Serial.println(" XIAO ESP32-S3 Sense Voice Recorder");
  Serial.println(" Firmware v0.3 (untested on hardware)");
  Serial.println("====================================");

  pinMode(cfg::REC_BUTTON_PIN, INPUT_PULLUP);
  feedbackSetup();

  Serial.printf("[sys] Chip: %s\n", ESP.getChipModel());
  Serial.printf("[sys] PSRAM: %lu bytes\n", static_cast<unsigned long>(ESP.getPsramSize()));

  loadUploadConfig();
  const bool ringOk = allocateRing();
  g_sdReady = initSd();
  if (g_sdReady) {
    recoverUnfinishedRecordings();
    cleanupSentParts();
  }
  refreshNextSessionId();
  g_audioReady = ringOk && startCapture();

  if (g_audioReady && g_sdReady) {
    g_state = ST_ARMED;
    Serial.println("[sys] ARMED: микрофон слушает, буфер копит предзапись.");
  } else {
    g_state = ST_FAULT;
    Serial.println("[sys] FAULT: запись невозможна. Проверьте карту и микрофон.");
    feedbackEvent("error");
  }

  printStatus();
  printHelp();
  armUpload();
}

void loop() {
  handleSerial();
  handleButton();
  serviceHealth();
  serviceRecording();
  if (UPLOAD_DURING_RECORDING || g_state != ST_RECORDING) {
    serviceUpload();
  }
  feedbackService(g_state);
  // Отдаём такт планировщику: задача захвата и idle-задача (watchdog) должны
  // получать время. В записи с полным буфером не спим, чтобы догнать.
  if (g_state != ST_RECORDING || g_ring.available() < cfg::SD_WRITE_BYTES) {
    delay(1);
  }
}
