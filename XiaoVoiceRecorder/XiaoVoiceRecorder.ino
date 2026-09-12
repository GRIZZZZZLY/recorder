#include <Arduino.h>
#include <ESP_I2S.h>
#include <FS.h>
#include <SD.h>
#include <SPI.h>

#include "Uploader.h"

/*
 * XIAO ESP32-S3 Sense voice recorder - firmware v0.1
 *
 * Hardware target:
 *   Seeed Studio XIAO ESP32-S3 Sense
 *   Camera disconnected; Sense expansion board remains installed.
 *
 * Features in v0.2:
 *   - PDM microphone capture, 16 kHz / 16-bit / mono
 *   - streaming WAV directly to microSD (no whole-file PSRAM buffer)
 *   - auto-numbered filenames: /REC0001.WAV, /REC0002.WAV, ...
 *   - serial commands: r=start/stop, s=status, l=list files, h=help
 *   - fixed SD CS on the documented GPIO21
 *   - startup repair of our own unfinished WAV files -> /RECnnnn_R.WAV
 *   - failed recordings are renamed to /RECnnnn.BAD, never reported as ready
 *   - optional future REC button on GPIO1 (disabled by default)
 */

namespace cfg {
constexpr uint32_t SERIAL_BAUD = 115200;

constexpr int PDM_CLK_PIN = 42;
constexpr int PDM_DATA_PIN = 41;
constexpr uint32_t SAMPLE_RATE = 16000;
constexpr uint16_t BITS_PER_SAMPLE = 16;
constexpr uint16_t CHANNELS = 1;
// One PCM frame. WAV data size must always be a whole number of these.
constexpr size_t FRAME_BYTES = CHANNELS * (BITS_PER_SAMPLE / 8);
constexpr size_t WAV_HEADER_BYTES = 44;

constexpr int SD_SCK_PIN = 7;
constexpr int SD_MISO_PIN = 8;
constexpr int SD_MOSI_PIN = 9;
// GPIO21 is the documented Sense microSD CS. The same pin drives the user LED,
// so the LED cannot blink independently while the card is mounted.
// Change only against a confirmed schematic for the board in hand.
constexpr int SD_CS_PIN = 21;
constexpr uint32_t SD_SPI_HZ = 10000000;

constexpr size_t AUDIO_BUFFER_BYTES = 4096;
constexpr uint32_t FILE_FLUSH_INTERVAL_MS = 1000;
constexpr uint32_t STATUS_INTERVAL_MS = 5000;

// One recording is stored as parts of this length, grouped by session number.
// Bounds the damage of a crash and keeps every file far below the 4 GB RIFF limit.
constexpr uint32_t PART_MAX_MS = 10UL * 60UL * 1000UL;
constexpr uint16_t MAX_PARTS = 99;
constexpr uint16_t MAX_SESSIONS = 9999;

// The onboard BOOT button is wired to GPIO0 with an external pull-up, so it
// works as the REC button with no extra hardware. Holding it during reset still
// enters the bootloader; a press after boot is a normal input.
// Unverified on our board: confirm the pin on first bring-up.
// GPIO1 / XIAO D0 stays free and is the candidate pad for a status LED, since
// the onboard LED on GPIO21 is occupied by the SD chip select.
constexpr bool ENABLE_REC_BUTTON = true;
constexpr int REC_BUTTON_PIN = 0;
constexpr uint32_t BUTTON_DEBOUNCE_MS = 40;
}

I2SClass g_mic;
File g_recordFile;

bool g_audioReady = false;
bool g_audioCapturing = false;
bool g_sdReady = false;
bool g_recording = false;
int g_sdCsPin = -1;
uint32_t g_dataBytes = 0;        // audio bytes in the current part
uint32_t g_sessionBytes = 0;     // audio bytes across all parts of this session
uint32_t g_recordStartedMs = 0;
uint32_t g_partStartedMs = 0;
uint32_t g_lastFlushMs = 0;
uint32_t g_lastStatusMs = 0;
uint16_t g_sessionId = 0;
uint16_t g_partIndex = 0;
String g_currentPath;

bool g_lastButtonReading = HIGH;
bool g_stableButtonState = HIGH;
uint32_t g_buttonChangedMs = 0;

String g_serialLine;

// Определены в Uploader.ino, который компилятор Arduino дописывает следом.
void loadUploadConfig();
bool setUploadConfig(const String &key, const String &value);
void printUploadConfig();
void armUpload();
void serviceUpload();

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
  writeLe32(header + 16, 16);                 // PCM fmt chunk size
  writeLe16(header + 20, 1);                  // PCM
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

bool startAudioCapture() {
  if (g_audioCapturing) {
    return true;
  }
  g_mic.setPinsPdmRx(cfg::PDM_CLK_PIN, cfg::PDM_DATA_PIN);
  if (!g_mic.begin(I2S_MODE_PDM_RX,
                   cfg::SAMPLE_RATE,
                   I2S_DATA_BIT_WIDTH_16BIT,
                   I2S_SLOT_MODE_MONO)) {
    Serial.println("[audio] ERROR: I2S/PDM start failed.");
    return false;
  }
  g_audioCapturing = true;

  // Drop whatever the DMA buffers hold from before this recording.
  uint8_t scratch[cfg::AUDIO_BUFFER_BYTES];
  g_mic.readBytes(reinterpret_cast<char *>(scratch), sizeof(scratch));
  return true;
}

void stopAudioCapture() {
  if (!g_audioCapturing) {
    return;
  }
  g_mic.end();
  g_audioCapturing = false;
}

// Probe the microphone once at boot, then release it. Keeping the PDM clock
// running while idle burns current for nothing.
bool initAudio() {
  Serial.println("[audio] Probing PDM microphone...");
  if (!startAudioCapture()) {
    return false;
  }
  stopAudioCapture();

  Serial.printf("[audio] Ready: %lu Hz, 16-bit, mono, CLK=%d DATA=%d\n",
                static_cast<unsigned long>(cfg::SAMPLE_RATE),
                cfg::PDM_CLK_PIN,
                cfg::PDM_DATA_PIN);
  return true;
}

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

// Accepts only a file this firmware wrote: exact 44-byte canonical layout and
// our own PCM format. Returns the data size the header claims.
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

// File::name() returns a bare name on some cores and a full path on others.
String baseName(File &file) {
  const String name = file.name();
  const int slash = name.lastIndexOf('/');
  return slash >= 0 ? name.substring(slash + 1) : name;
}

// /REC0007_P03.WAV is session 7, part 3. A part is written as .TMP and renamed
// to .WAV only after a clean close, so an open file never looks ready to the
// uploader. A failed part becomes .BAD, a repaired one _R.WAV.
String partPath(uint16_t session, uint16_t part, const char *ext) {
  char buf[32];
  snprintf(buf, sizeof(buf), "/REC%04u_P%02u.%s", session, part, ext);
  return String(buf);
}

// One directory pass beats up to 9999 SD.exists() probes.
uint16_t nextSessionId() {
  uint16_t maxSeen = 0;
  File root = SD.open("/");
  if (!root || !root.isDirectory()) {
    return 1;
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
  return maxSeen + 1;
}

// A leftover .TMP part means power was lost while writing: its header still
// claims zero bytes. Rebuild the header from the real file size and mark the
// part recovered. Only audio that reached the card survives; unflushed samples
// are gone, and a damaged FAT is not repaired here.
void recoverUnfinishedRecordings() {
  File root = SD.open("/");
  if (!root || !root.isDirectory()) {
    Serial.println("[recover] Cannot open root directory.");
    return;
  }

  // Renaming while iterating a directory is unsafe, so collect names first.
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
      uint32_t audio = total > cfg::WAV_HEADER_BYTES
                           ? total - cfg::WAV_HEADER_BYTES
                           : 0;
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
    // "r+" keeps the audio; FILE_WRITE would truncate the file to zero.
    File f = SD.open(tmpPath.c_str(), "r+");
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
  g_partStartedMs = millis();
  g_lastFlushMs = g_partStartedMs;
  return true;
}

// Finishes the open part. Only a clean close earns the .WAV name; anything else
// becomes .BAD so a broken part is never offered as ready audio.
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

bool startRecording() {
  if (g_recording) {
    return true;
  }
  if (!g_audioReady) {
    Serial.println("[rec] Cannot start: microphone is not initialized.");
    return false;
  }
  if (!g_sdReady) {
    Serial.println("[rec] Cannot start: microSD is not initialized.");
    return false;
  }

  const uint16_t session = nextSessionId();
  if (session > cfg::MAX_SESSIONS) {
    Serial.println("[rec] ERROR: session numbers exhausted. Clear the card.");
    return false;
  }
  if (!startAudioCapture()) {
    return false;
  }

  g_sessionId = session;
  g_partIndex = 1;
  g_sessionBytes = 0;
  if (!openPart()) {
    stopAudioCapture();
    return false;
  }

  g_recordStartedMs = millis();
  g_lastStatusMs = g_recordStartedMs;
  g_recording = true;

  Serial.printf("[rec] START session %04u -> %s\n", g_sessionId, g_currentPath.c_str());
  return true;
}

enum StopReason { STOP_NORMAL, STOP_WRITE_ERROR };

void stopRecording(StopReason reason = STOP_NORMAL) {
  if (!g_recording) {
    return;
  }

  g_recording = false;
  closePart(reason == STOP_NORMAL);
  stopAudioCapture();

  const uint32_t elapsedSec = (millis() - g_recordStartedMs) / 1000;
  Serial.printf("[rec] STOP session %04u | %u parts | %lu s | %lu audio bytes\n",
                g_sessionId,
                g_partIndex,
                static_cast<unsigned long>(elapsedSec),
                static_cast<unsigned long>(g_sessionBytes));

  // Wi-Fi поднимается только после остановки, никогда во время записи.
  armUpload();
}

// ponytail: capture pauses for one close+open at every part boundary. The I2S
// DMA buffers should cover that gap, but it is unmeasured on real hardware.
// Upgrade path: move capture into its own FreeRTOS task with a ring buffer if
// the boundary turns out to drop audio.
void rotatePart() {
  if (g_partIndex >= cfg::MAX_PARTS) {
    Serial.println("[rec] Part limit reached for this session. Stopping.");
    stopRecording();
    return;
  }

  if (!closePart(true)) {
    Serial.println("[rec] ERROR: could not finish part. Stopping.");
    g_recording = false;
    stopAudioCapture();
    return;
  }

  ++g_partIndex;
  if (!openPart()) {
    Serial.println("[rec] ERROR: could not open next part. Stopping.");
    g_recording = false;
    stopAudioCapture();
    return;
  }
  Serial.printf("[rec] NEXT PART -> %s\n", g_currentPath.c_str());
}

void serviceRecording() {
  if (!g_recording) {
    return;
  }

  static uint8_t audioBuffer[cfg::AUDIO_BUFFER_BYTES];
  size_t bytesRead = g_mic.readBytes(
      reinterpret_cast<char *>(audioBuffer), sizeof(audioBuffer));

  // Never write a partial frame: a stray odd byte shifts every later sample.
  bytesRead -= bytesRead % cfg::FRAME_BYTES;

  if (bytesRead == 0) {
    Serial.println("[rec] WARNING: microphone returned no whole samples.");
    return;
  }

  const size_t bytesWritten = g_recordFile.write(audioBuffer, bytesRead);

  // Count what actually reached the card before deciding to stop, so a partial
  // write is still described by the header instead of being silently dropped.
  g_dataBytes += static_cast<uint32_t>(bytesWritten - (bytesWritten % cfg::FRAME_BYTES));

  if (bytesWritten != bytesRead) {
    Serial.printf("[rec] ERROR: SD write incomplete (%u of %u bytes). Stopping.\n",
                  static_cast<unsigned>(bytesWritten), static_cast<unsigned>(bytesRead));
    stopRecording(STOP_WRITE_ERROR);
    return;
  }

  const uint32_t now = millis();
  if (now - g_lastFlushMs >= cfg::FILE_FLUSH_INTERVAL_MS) {
    g_recordFile.flush();
    g_lastFlushMs = now;
  }

  if (now - g_lastStatusMs >= cfg::STATUS_INTERVAL_MS) {
    const uint32_t sec = (now - g_recordStartedMs) / 1000;
    Serial.printf("[rec] session %04u part %02u | %lu s | %.2f MB total\n",
                  g_sessionId, g_partIndex,
                  static_cast<unsigned long>(sec),
                  (g_sessionBytes + g_dataBytes) / (1024.0f * 1024.0f));
    g_lastStatusMs = now;
  }

  if (now - g_partStartedMs >= cfg::PART_MAX_MS) {
    rotatePart();
  }
}

void printCardFiles() {
  if (!g_sdReady) {
    Serial.println("[sd] Card is not mounted.");
    return;
  }
  // Walking the card competes with the recording for the SPI bus and can
  // starve the capture loop, so refuse while recording.
  if (g_recording) {
    Serial.println("[sd] Not while recording. Stop with 'r' first.");
    return;
  }

  File root = SD.open("/");
  if (!root || !root.isDirectory()) {
    Serial.println("[sd] Cannot open root directory.");
    return;
  }

  Serial.println("[sd] Files:");
  File file = root.openNextFile();
  while (file) {
    if (!file.isDirectory()) {
      Serial.printf("  %-24s %10llu bytes\n", file.name(),
                    static_cast<unsigned long long>(file.size()));
    }
    file = root.openNextFile();
  }
}

void printStatus() {
  Serial.println("--- STATUS ---");
  Serial.printf("Audio: %s\n", g_audioReady ? "ready" : "ERROR");
  Serial.printf("SD:    %s", g_sdReady ? "ready" : "ERROR");
  if (g_sdReady) {
    Serial.printf(" (CS=GPIO%d)", g_sdCsPin);
  }
  Serial.println();
  Serial.printf("Mic:   %s\n", g_audioCapturing ? "capturing" : "off");
  Serial.printf("State: %s\n", g_recording ? "RECORDING" : "idle");
  if (g_recording) {
    Serial.printf("Sess:  %04u, part %02u\n", g_sessionId, g_partIndex);
    Serial.printf("File:  %s\n", g_currentPath.c_str());
  }
  Serial.println("Time:  unknown (no RTC, no time sync yet)");
  Serial.println("Batt:  unknown (no gauge connected yet)");
  Serial.printf("Heap:  %lu bytes free\n",
                static_cast<unsigned long>(ESP.getFreeHeap()));
  Serial.printf("PSRAM: %lu / %lu bytes free\n",
                static_cast<unsigned long>(ESP.getFreePsram()),
                static_cast<unsigned long>(ESP.getPsramSize()));
  Serial.println("--------------");
}

void printHelp() {
  Serial.println();
  Serial.println("XIAO Voice Recorder v0.2. Команда вводится строкой, затем Enter.");
  Serial.println("  r  начать или остановить запись");
  Serial.println("  s  состояние");
  Serial.println("  l  файлы на карте (не во время записи)");
  Serial.println("  c  настройки отправки");
  Serial.println("  u  отправить готовые части сейчас");
  Serial.println("  h  справка");
  Serial.println("Настройка: wifi_ssid=... wifi_pass=... server_url=...");
  Serial.println("           device_token=... device_id=...");
  Serial.println();
}

// Строка вида key=value настраивает отправку, одиночная буква — команда.
// Разделение по строкам, а не по символам: иначе server_url= сработал бы как
// команда 's'.
void processSerialLine(const String &line) {
  const int eq = line.indexOf('=');
  if (eq > 0) {
    if (!setUploadConfig(line.substring(0, eq), line.substring(eq + 1))) {
      Serial.printf("[cfg] Неизвестный параметр: %s\n", line.substring(0, eq).c_str());
    }
    return;
  }

  if (line.length() != 1) {
    Serial.println("Неизвестная команда. h — справка.");
    return;
  }

  switch (line.charAt(0)) {
    case 'r':
    case 'R':
      if (g_recording) stopRecording();
      else startRecording();
      break;
    case 's':
    case 'S':
      printStatus();
      break;
    case 'l':
    case 'L':
      printCardFiles();
      break;
    case 'c':
    case 'C':
      printUploadConfig();
      break;
    case 'u':
    case 'U':
      armUpload();
      Serial.println("[up] Отправка запрошена.");
      break;
    case 'h':
    case 'H':
    case '?':
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

void handleOptionalButton() {
  if (!cfg::ENABLE_REC_BUTTON) {
    return;
  }

  const bool reading = digitalRead(cfg::REC_BUTTON_PIN);
  const uint32_t now = millis();

  if (reading != g_lastButtonReading) {
    g_lastButtonReading = reading;
    g_buttonChangedMs = now;
  }

  if ((now - g_buttonChangedMs) >= cfg::BUTTON_DEBOUNCE_MS &&
      reading != g_stableButtonState) {
    g_stableButtonState = reading;
    if (g_stableButtonState == LOW) {
      if (g_recording) stopRecording();
      else startRecording();
    }
  }
}

void setup() {
  Serial.begin(cfg::SERIAL_BAUD);
  delay(1200);

  Serial.println();
  Serial.println("====================================");
  Serial.println(" XIAO ESP32-S3 Sense Voice Recorder");
  Serial.println(" Firmware v0.1");
  Serial.println("====================================");

  if (cfg::ENABLE_REC_BUTTON) {
    pinMode(cfg::REC_BUTTON_PIN, INPUT_PULLUP);
  }

  Serial.printf("[sys] Chip: %s\n", ESP.getChipModel());
  Serial.printf("[sys] PSRAM: %lu bytes\n",
                static_cast<unsigned long>(ESP.getPsramSize()));

  loadUploadConfig();
  g_audioReady = initAudio();
  g_sdReady = initSd();
  if (g_sdReady) {
    recoverUnfinishedRecordings();
  }

  printStatus();
  printHelp();
}

void loop() {
  handleSerial();
  handleOptionalButton();
  serviceRecording();
  serviceUpload();
  delay(1);
}
