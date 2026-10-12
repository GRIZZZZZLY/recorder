#include <Arduino.h>
#include <ESP_I2S.h>
#include <FS.h>
#include <Preferences.h>
#include <SD.h>
#include <SPI.h>

#include "AudioFormat.h"
#include "Battery.h"
#include "BoardConfig.h"
#include "Button.h"
#include "RingBuffer.h"
#include "SdStorage.h"
#include "Session.h"
#include "Types.h"
#include "UploadPolicy.h"

/*
 * Voice recorder — firmware v0.4
 *
 * Одна прошивка, два профиля платы (BoardConfig.h, выбор при сборке):
 *   XIAO ESP32-S3 Sense     встроенный PDM-микрофон, моно        — по умолчанию
 *   Recorder PCB V1 RevA    2x IM72D128V, стерео, батарея, вибро — -DRECORDER_BOARD_REVA
 * Логика записи о плате не знает: формат звука приходит в session::Writer
 * значением, а пины живут только здесь и в Feedback.ino.
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
 * Этот файл — клей между железом и логикой. Сама логика записи (части,
 * метки, STOP с .END, восстановление, очистка) живёт в Session.h и проверена
 * на хосте с хранилищем в памяти. Здесь — только Arduino: пины, Serial, I2S,
 * FreeRTOS, NVS.
 *
 * Ничего из этого не проверено на настоящей плате. См. README_RU.md.
 */

namespace cfg {
// Уходит серверу в X-Firmware вместе с профилем платы: "0.4.0+xiao-sense".
constexpr const char *FIRMWARE_VERSION = "0.4.0";
constexpr uint32_t SERIAL_BAUD = 115200;

// Пины и формат звука — в BoardConfig.h. Здесь только то, что от платы не зависит.
constexpr audio::Format AUDIO = board::AUDIO;
constexpr uint32_t SD_SPI_HZ = 10000000;

// Сколько секунд до нажатия попадает в запись. Ёмкость буфера округляется
// вверх до степени двойки (RingBuffer работает маской), поэтому фактическая
// глубина — RING_BYTES / bytesPerSecond: при 30 с это 1 МиБ = 32,8 с в моно
// и 2 МиБ = 32,8 с в стерео.
constexpr uint32_t PRE_RECORD_SECONDS = 30;
constexpr uint32_t RING_BYTES = AUDIO.ringBytesFor(PRE_RECORD_SECONDS);
// Если PSRAM не дала буфер: работаем без предзаписи, но не отказываемся писать.
constexpr uint32_t RING_FALLBACK_BYTES = 64 * 1024;

// Чтение микрофона блокирует до полного куска: 32 мс звука — 1024 байта в
// моно, 2048 в стерео. DMA ядра держит 6 x 240 кадров = 90 мс, кусок должен
// быть заметно короче.
constexpr size_t MIC_READ_BYTES = AUDIO.wholeFrames(AUDIO.bytesPerSecond() * 32 / 1000);
constexpr uint32_t STATUS_INTERVAL_MS = 5000;

// Сколько чтений АЦП усредняется на одно показание заряда: АЦП ESP32-S3 шумный.
constexpr int BATTERY_SAMPLES = 16;

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
// измерений, печатаются по команде d. Счётчики записи живут в Writer.
// Всё в кадрах: кадр — отсчёт по всем каналам, в моно равен сэмплу.
struct Diag {
  uint32_t captured_frames = 0;
  uint32_t mic_short_reads = 0;   // микрофон вернул меньше, чем просили
  uint32_t buffer_high_watermark = 0;
  uint32_t upload_bytes = 0;
  uint32_t upload_duration_ms = 0;
  uint32_t upload_preempted = 0;
  uint32_t loop_gap_max_us = 0;
};
Diag g_diag;

static uint32_t nowMs() { return static_cast<uint32_t>(millis()); }
static void serialLog(const char *line) { Serial.println(line); }

I2SClass g_mic;
RingBuffer g_ring;
uint8_t *g_ringStorage = nullptr;
bool g_ringInPsram = false;
TaskHandle_t g_captureTask = nullptr;
volatile bool g_captureRunning = false;
volatile bool g_micFault = false;

SdStorage g_sd;
// Формат — из профиля платы; Writer дальше о плате не знает.
session::Writer g_writer(g_sd, g_ring, nowMs, serialLog, cfg::AUDIO);

DeviceState g_state = ST_FAULT;
bool g_audioReady = false;
bool g_sdReady = false;
int g_sdCsPin = -1;
uint32_t g_recordStartedMs = 0;
uint32_t g_lastStatusMs = 0;
uint16_t g_nextSessionId = 1;    // посчитан заранее, START не ходит по каталогу
Preferences g_sessionPrefs;

ButtonDecoder g_button(cfg::BUTTON_DEBOUNCE_MS, cfg::LONG_PRESS_MS);
String g_serialLine;

// Фронты кнопки защёлкиваются прерыванием. Опрос только из loop() терял бы
// нажатие целиком: TLS-рукопожатие блокирует цикл на секунду-две, и короткое
// нажатие умещается внутри этого окна. Нажал — значит запись начнётся, пусть
// и на секунду позже; предзапись эту секунду держит.
struct ButtonEdge {
  uint32_t atMs;
  uint32_t ringHead;  // сколько звука произведено к этому мгновению
  bool pressed;
};
constexpr uint8_t BUTTON_QUEUE = 8;
volatile ButtonEdge g_btnQueue[BUTTON_QUEUE];
volatile uint8_t g_btnHead = 0;
volatile uint8_t g_btnTail = 0;
volatile uint32_t g_btnLastEdgeMs = 0;
volatile bool g_btnLastLevel = false;
volatile uint32_t g_btnLostEdges = 0;
uint32_t g_btnPressHead = 0;   // положение потока в момент последнего нажатия

// Определены в Uploader.ino и Feedback.ino; компилятор Arduino дописывает их
// следом за этим файлом.
void loadUploadConfig();
bool setUploadConfig(const String &key, const String &value);
void printUploadConfig();
void armUpload();
void serviceUpload();
void preemptUpload();
bool uploadBusy();
void scanWifi();
void watchWifiReason();
void cleanupSentParts();
void continueInNewSession();
void feedbackSetup();
void feedbackService(DeviceState state);
void feedbackEvent(const char *name);
bool setFeedbackPin(const String &key, const String &value);

// --- микрофон и кольцевой буфер ------------------------------------------------------

// Задача захвата: единственный писатель буфера. Ничего не решает, только
// переливает DMA -> кольцо. Приоритет выше loop(), чтобы SD и TLS не
// задерживали чтение DMA.
void captureTask(void *) {
  static uint8_t chunk[cfg::MIC_READ_BYTES];
  while (g_captureRunning) {
    size_t got = g_mic.readBytes(reinterpret_cast<char *>(chunk), sizeof(chunk));
    got = cfg::AUDIO.wholeFrames(static_cast<uint32_t>(got));
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
    g_diag.captured_frames += cfg::AUDIO.frames(static_cast<uint32_t>(got));
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
                cfg::AUDIO.seconds(bytes));
  return true;
}

bool startCapture() {
  if (g_captureRunning) {
    return true;
  }
  g_mic.setPinsPdmRx(board::PDM_CLK_PIN, board::PDM_DATA_PIN);
  // Стерео: два PDM-микрофона на одной DATA, каждый в своём полупериоде CLK
  // (SELECT на GND и на 3V3). Аппаратный PDM RX ESP32-S3 раскладывает их по
  // двум слотам, кадр — L затем R. На железе не проверено ни для одной платы.
  const i2s_slot_mode_t slots = cfg::AUDIO.channels == 2 ? I2S_SLOT_MODE_STEREO : I2S_SLOT_MODE_MONO;
  if (!g_mic.begin(I2S_MODE_PDM_RX, cfg::AUDIO.sampleRate, I2S_DATA_BIT_WIDTH_16BIT, slots)) {
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
  SPI.begin(board::SD_SCK_PIN, board::SD_MISO_PIN, board::SD_MOSI_PIN, csPin);
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
  Serial.printf("[sd] Mounting microSD, CS=GPIO%d...\n", board::SD_CS_PIN);
  if (tryMountSd(board::SD_CS_PIN)) {
    g_sdCsPin = board::SD_CS_PIN;
    const uint64_t sizeMb = SD.cardSize() / (1024ULL * 1024ULL);
    Serial.printf("[sd] Ready. CS=GPIO%d, card=%llu MB\n",
                  board::SD_CS_PIN, static_cast<unsigned long long>(sizeMb));
    return true;
  }
  Serial.printf("[sd] ERROR: card not mounted with CS=GPIO%d (%s).\n", board::SD_CS_PIN, board::NAME);
  Serial.println("[sd] Check the card slot wiring for this board and that the card is FAT32.");
  return false;
}

// --- батарея -----------------------------------------------------------------------------

// На Sense честно «не поддерживается»: у платы нет ни делителя, ни газометра.
// На RevA — усреднённое напряжение с делителя на GPIO1 и процент по кривой
// Li-Po из Battery.h. Не вызывается в записи: только статус и заголовки отправки.
battery::Status readBattery() {
  battery::Status s;
  s.supported = board::HAS_BATTERY_ADC;
  if (!s.supported) {
    return s;
  }
  uint32_t sum = 0;
  for (int i = 0; i < cfg::BATTERY_SAMPLES; ++i) {
    sum += analogReadMilliVolts(static_cast<uint8_t>(board::BAT_ADC_PIN));
  }
  const uint32_t mv = battery::packMillivolts(sum / cfg::BATTERY_SAMPLES, board::BAT_DIVIDER);
  s.millivolts = static_cast<uint16_t>(mv > 0xFFFF ? 0xFFFF : mv);
  s.percent = battery::percentFromMillivolts(mv);
  return s;
}

// Номер следующей сессии: больше всего на карте и всего, что уже выдавали
// (NVS). Иначе после автоудаления старых .SENT номер мог бы повториться, а
// сервер ключует части по (устройство, сессия, часть) и ответил бы 409.
void refreshNextSessionId() {
  g_sessionPrefs.begin(cfg::NVS_NAMESPACE, true);
  const uint16_t issued = static_cast<uint16_t>(g_sessionPrefs.getUShort("last_session", 0));
  g_sessionPrefs.end();
  const uint16_t onCard = g_sdReady ? session::maxSessionOnCard(g_sd) : 0;
  g_nextSessionId = session::nextSessionId(issued, onCard);
}

void rememberSessionId(uint16_t sessionId) {
  g_sessionPrefs.begin(cfg::NVS_NAMESPACE, false);
  g_sessionPrefs.putUShort("last_session", sessionId);
  g_sessionPrefs.end();
}

// --- запись: клей вокруг session::Writer -------------------------------------------------

bool startRecording() {
  if (g_state == ST_RECORDING) {
    return true;
  }
  if (g_state != ST_ARMED || !g_sdReady) {
    Serial.println("[rec] Cannot start: device is not armed.");
    feedbackEvent("error");
    return false;
  }
  if (g_nextSessionId == 0) {
    Serial.println("[rec] ERROR: session numbers exhausted. Clear the card.");
    feedbackEvent("error");
    return false;
  }

  // Отправка мешала бы записи — обрывается сразу, часть уйдёт заново позже.
  if (!UPLOAD_DURING_RECORDING && uploadBusy()) {
    preemptUpload();
  }

  if (!g_writer.start(g_nextSessionId)) {
    feedbackEvent("error");
    return false;
  }
  rememberSessionId(g_writer.sessionId());

  g_recordStartedMs = millis();
  g_lastStatusMs = g_recordStartedMs;
  g_state = ST_RECORDING;
  feedbackEvent("start");
  Serial.printf("[rec] START session %04u -> %s | предзапись %.1f с\n",
                g_writer.sessionId(), g_writer.currentPath(),
                cfg::AUDIO.seconds(g_writer.preRollBytes()));
  return true;
}

void stopRecording(StopReason reason) {
  if (g_state != ST_RECORDING) {
    return;
  }
  // .END появляется только если каждая часть закрылась чисто: ошибка на
  // последней делает её .BAD, и сервер не получит ложного «сессия закончена».
  const bool clean = g_writer.stop(reason == STOP_NORMAL);
  g_state = ST_ARMED;
  feedbackEvent(clean ? "stop" : "error");

  const uint32_t elapsedSec = (millis() - g_recordStartedMs) / 1000;
  Serial.printf("[rec] STOP session %04u | %u parts | %lu s | %lu audio bytes | marks %u | "
                "clean=%s end=%s | dropped %lu frames\n",
                g_writer.sessionId(), g_writer.partIndex(),
                static_cast<unsigned long>(elapsedSec),
                static_cast<unsigned long>(g_writer.sessionBytes()),
                g_writer.marks(), clean ? "yes" : "NO", g_writer.endCreated() ? "yes" : "no",
                static_cast<unsigned long>(cfg::AUDIO.frames(g_ring.dropped())));

  // Через тот же helper, а не просто +1: после сессии 9999 номера кончились,
  // и следующий START обязан отказать, а не сделать пятизначное имя.
  g_nextSessionId = session::nextSessionId(g_writer.sessionId(), g_writer.sessionId());
  cleanupSentParts();
  armUpload();
}

// Вызывается из loop() каждый оборот: слив буфера, ротация, ошибки карты.
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
  if (!g_writer.service()) {
    stopRecording(STOP_WRITE_ERROR);
    return;
  }
  if (g_writer.partLimitReached()) {
    continueInNewSession();
    return;
  }

  const uint32_t hwm = g_ring.highWatermark();
  if (hwm > g_diag.buffer_high_watermark) {
    g_diag.buffer_high_watermark = hwm;
  }

  const uint32_t now = millis();
  if (now - g_lastStatusMs >= cfg::STATUS_INTERVAL_MS) {
    const uint32_t sec = (now - g_recordStartedMs) / 1000;
    Serial.printf("[rec] session %04u part %02u | %lu s | %.2f MB | ring %lu B | dropped %lu\n",
                  g_writer.sessionId(), g_writer.partIndex(), static_cast<unsigned long>(sec),
                  (g_writer.sessionBytes() + g_writer.partBytes()) / (1024.0f * 1024.0f),
                  static_cast<unsigned long>(g_ring.available()),
                  static_cast<unsigned long>(cfg::AUDIO.frames(g_ring.dropped())));
    g_lastStatusMs = now;
  }
}

// 99 частей — это 16,5 часа. Хвост, который уже не влезает, не выбрасывается:
// сессия закрывается чисто, и тут же начинается следующая. Буфер при этом не
// трогаем, поэтому звук между двумя сессиями не теряется — он становится
// предзаписью новой. Очистка карты сюда не лезет: она ждёт настоящего STOP,
// потому что чтение таблицы FAT занимает секунды.
void continueInNewSession() {
  const uint16_t finished = g_writer.sessionId();
  const bool clean = g_writer.stop(true);
  Serial.printf("[rec] Предел частей в сессии %04u (clean=%s). Продолжаю новой сессией.\n",
                finished, clean ? "yes" : "NO");
  g_nextSessionId = session::nextSessionId(finished, finished);
  if (!clean || g_nextSessionId == 0 || !g_writer.start(g_nextSessionId)) {
    g_state = ST_ARMED;
    feedbackEvent("error");
    Serial.println("[rec] Новая сессия не открылась. Запись остановлена.");
    armUpload();
    return;
  }
  rememberSessionId(g_writer.sessionId());
  g_recordStartedMs = millis();
  g_lastStatusMs = g_recordStartedMs;
  armUpload();
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

// ringHead — положение потока в момент нажатия; 0 означает «неизвестно»
// (команда с Serial), тогда берётся текущее.
void onShortPress(uint32_t ringHead) {
  switch (g_state) {
    case ST_ARMED:
      startRecording();
      break;
    case ST_RECORDING:
      feedbackEvent((ringHead ? g_writer.markAt(ringHead) : g_writer.mark()) ? "mark" : "error");
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

void dispatchButton(ButtonDecoder::Event event, uint32_t ringHead) {
  switch (event) {
    case ButtonDecoder::SHORT:
      onShortPress(ringHead);
      break;
    case ButtonDecoder::LONG:
      onLongPress();
      break;
    default:
      break;
  }
}

// Прерывание только запоминает фронт: ни карты, ни сети, ни вибрации здесь
// быть не может. Дребезг гасится по времени прямо тут, поэтому очередь из
// восьми фронтов не переполняется от одного нажатия.
// Нажата любая из двух кнопок: BOOT или внешняя. Без второй — только первая.
bool IRAM_ATTR recPressed() {
  return digitalRead(board::REC_BUTTON_PIN) == LOW ||
         (board::REC_BUTTON2_PIN >= 0 && digitalRead(board::REC_BUTTON2_PIN) == LOW);
}

void IRAM_ATTR buttonIsr() {
  const uint32_t now = millis();
  const bool pressed = recPressed();
  if (pressed == g_btnLastLevel || now - g_btnLastEdgeMs < cfg::BUTTON_DEBOUNCE_MS) {
    return;
  }
  g_btnLastEdgeMs = now;
  g_btnLastLevel = pressed;
  const uint8_t next = static_cast<uint8_t>((g_btnHead + 1) % BUTTON_QUEUE);
  if (next == g_btnTail) {
    // Очередь полна: видно в диагностике, а не молча. Инкремент volatile
    // устарел в C++20, поэтому явное присваивание.
    g_btnLostEdges = g_btnLostEdges + 1;
    return;
  }
  g_btnQueue[g_btnHead].atMs = now;
  // Положение потока в момент нажатия: обычная атомарная загрузка, ничего
  // блокирующего. По нему метка попадает туда, где человек нажал, а не туда,
  // где цикл об этом узнал.
  g_btnQueue[g_btnHead].ringHead = g_ring.head();
  g_btnQueue[g_btnHead].pressed = pressed;
  g_btnHead = next;
}

void handleButton() {
  // Сначала фронты, случившиеся пока цикл был занят: у них своё, настоящее
  // время, поэтому короткое нажатие не превратится в длинное.
  while (g_btnTail != g_btnHead) {
    const uint8_t tail = g_btnTail;
    const uint32_t atMs = g_btnQueue[tail].atMs;
    const bool pressed = g_btnQueue[tail].pressed;
    g_btnTail = static_cast<uint8_t>((tail + 1) % BUTTON_QUEUE);
    // Метка ставится по положению потока в момент НАЖАТИЯ, а событие SHORT
    // приходит на отпускании: поэтому запоминаем head нажатия.
    if (pressed) {
      g_btnPressHead = g_btnQueue[tail].ringHead;
    }
    // Два отсчёта на фронт: второй переводит декодер в устойчивое состояние,
    // при этом отсчёт удержания идёт от самого фронта.
    dispatchButton(g_button.feed(pressed, atMs), g_btnPressHead);
    dispatchButton(g_button.feed(pressed, atMs + cfg::BUTTON_DEBOUNCE_MS), g_btnPressHead);
  }
  // Затем обычный опрос: он даёт длинному нажатию сработать без новых фронтов
  // и остаётся страховкой, если прерывание почему-то не встало. Пока фронт
  // свежее антидребезга, опрос молчит: иначе он подал бы декодеру время
  // раньше уже скормленного.
  const uint32_t now = millis();
  if (now - g_btnLastEdgeMs >= cfg::BUTTON_DEBOUNCE_MS) {
    dispatchButton(g_button.feed(recPressed(), now), g_btnPressHead);
  }
}

static void printFileEntry(const char *name, uint32_t size, void *) {
  Serial.printf("  %-24s %10lu bytes\n", name, static_cast<unsigned long>(size));
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
  Serial.println("[sd] Files:");
  g_sd.listRoot(printFileEntry, nullptr);
  Serial.printf("[sd] total %llu MB, used %llu MB\n",
                static_cast<unsigned long long>(g_sd.totalBytes() >> 20),
                static_cast<unsigned long long>(g_sd.usedBytes() >> 20));
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
  Serial.printf("Board: %s (%s), hardware unverified\n", board::NAME, board::ID);
  Serial.printf("Audio: %lu Hz / %u bit / %u ch, %lu bytes/s\n",
                static_cast<unsigned long>(cfg::AUDIO.sampleRate), cfg::AUDIO.bitsPerSample,
                cfg::AUDIO.channels, static_cast<unsigned long>(cfg::AUDIO.bytesPerSecond()));
  Serial.printf("Ring:  %lu / %lu bytes (%s), pre-record %.1f s\n",
                static_cast<unsigned long>(g_ring.available()),
                static_cast<unsigned long>(g_ring.capacity()),
                g_ringInPsram ? "PSRAM" : "heap",
                cfg::AUDIO.seconds(g_ring.capacity()));
  if (g_state == ST_RECORDING) {
    Serial.printf("Sess:  %04u, part %02u, marks %u\n", g_writer.sessionId(),
                  g_writer.partIndex(), g_writer.marks());
    Serial.printf("File:  %s\n", g_writer.currentPath());
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
  const battery::Status batt = readBattery();
  if (batt.supported) {
    Serial.printf("Batt:  %u.%03u V, ~%u%% (ADC GPIO%d, divider %.2f unverified)\n",
                  batt.millivolts / 1000, batt.millivolts % 1000, batt.percent,
                  board::BAT_ADC_PIN, static_cast<double>(board::BAT_DIVIDER));
  } else {
    Serial.println("Batt:  unknown (this board has no battery ADC)");
  }
  Serial.printf("Heap:  %lu bytes free\n", static_cast<unsigned long>(ESP.getFreeHeap()));
  Serial.printf("PSRAM: %lu / %lu bytes free\n",
                static_cast<unsigned long>(ESP.getFreePsram()),
                static_cast<unsigned long>(ESP.getPsramSize()));
  Serial.println("--------------");
}

void printDiag() {
  const session::WriterStats &w = g_writer.stats();
  Serial.println("--- DIAG (числа для измерений, не показатели продукта) ---");
  Serial.printf("frame = %u ch x %u bit = %lu bytes; в моно кадр равен сэмплу\n",
                cfg::AUDIO.channels, cfg::AUDIO.bitsPerSample,
                static_cast<unsigned long>(cfg::AUDIO.frameBytes()));
  Serial.printf("captured_frames       %lu\n", static_cast<unsigned long>(g_diag.captured_frames));
  Serial.printf("written_frames        %lu\n", static_cast<unsigned long>(w.written_frames));
  Serial.printf("dropped_frames        %lu\n", static_cast<unsigned long>(cfg::AUDIO.frames(g_ring.dropped())));
  Serial.printf("mic_short_reads       %lu\n", static_cast<unsigned long>(g_diag.mic_short_reads));
  Serial.printf("sd_write_max_ms       %lu\n", static_cast<unsigned long>(w.sd_write_max_ms));
  Serial.printf("sd_write_errors       %lu\n", static_cast<unsigned long>(w.sd_write_errors));
  Serial.printf("part_switch_max_ms    %lu\n", static_cast<unsigned long>(w.part_switch_max_ms));
  Serial.printf("buffer_high_watermark %lu of %lu\n",
                static_cast<unsigned long>(g_ring.highWatermark()),
                static_cast<unsigned long>(g_ring.capacity()));
  Serial.printf("upload_bytes          %lu\n", static_cast<unsigned long>(g_diag.upload_bytes));
  Serial.printf("upload_duration_ms    %lu\n", static_cast<unsigned long>(g_diag.upload_duration_ms));
  Serial.printf("upload_preempted      %lu\n", static_cast<unsigned long>(g_diag.upload_preempted));
  Serial.printf("loop_gap_max_us      %lu\n", static_cast<unsigned long>(g_diag.loop_gap_max_us));
  Serial.printf("button_lost_edges     %lu\n", static_cast<unsigned long>(g_btnLostEdges));
  Serial.printf("uptime_ms             %lu\n", static_cast<unsigned long>(millis()));
  // Ожидание по часам против фактически захваченного: разница — оценка
  // потерь между DMA и задачей захвата, которых буфер не видит.
  Serial.printf("expected_by_clock     %lu frames\n",
                static_cast<unsigned long>((millis() / 1000UL) * cfg::AUDIO.sampleRate));
  Serial.println("------------------------------------------------------------");
}

void printHelp() {
  Serial.println();
  Serial.printf("Voice Recorder v%s, плата %s. Команда вводится строкой, затем Enter.\n",
                cfg::FIRMWARE_VERSION, board::NAME);
  Serial.println("  r  START (в ARMED) или STOP (в RECORDING)");
  Serial.println("  m  метка (только в RECORDING)");
  Serial.println("  s  состояние");
  Serial.println("  d  диагностические счётчики");
  Serial.println("  l  файлы на карте (не во время записи)");
  Serial.println("  c  настройки отправки");
  Serial.println("  u  отправить готовые части сейчас");
  Serial.println("  w  какие сети Wi-Fi видно и сигнал");
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
      if (g_state == ST_RECORDING) onShortPress(0);
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
    case 'w': case 'W':
      if (g_state == ST_RECORDING || uploadBusy()) Serial.println("[net] Не во время записи и отправки.");
      else scanWifi();
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
  Serial.printf(" Voice Recorder — %s\n", board::NAME);
  Serial.printf(" Firmware v%s+%s (hardware unverified)\n", cfg::FIRMWARE_VERSION, board::ID);
  Serial.printf(" %u ch x %u bit @ %lu Hz, PDM CLK GPIO%d DATA GPIO%d, SD CS GPIO%d, REC GPIO%d\n",
                cfg::AUDIO.channels, cfg::AUDIO.bitsPerSample,
                static_cast<unsigned long>(cfg::AUDIO.sampleRate), board::PDM_CLK_PIN,
                board::PDM_DATA_PIN, board::SD_CS_PIN, board::REC_BUTTON_PIN);
  Serial.println("====================================");

  pinMode(board::REC_BUTTON_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(board::REC_BUTTON_PIN), buttonIsr, CHANGE);
  if (board::REC_BUTTON2_PIN >= 0) {
    pinMode(board::REC_BUTTON2_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(board::REC_BUTTON2_PIN), buttonIsr, CHANGE);
  }
  g_btnLastLevel = recPressed();
  feedbackSetup();

  Serial.printf("[sys] Chip: %s\n", ESP.getChipModel());
  Serial.printf("[sys] PSRAM: %lu bytes\n", static_cast<unsigned long>(ESP.getPsramSize()));

  loadUploadConfig();
  watchWifiReason();
  const bool ringOk = allocateRing();
  g_sdReady = initSd();
  if (g_sdReady) {
    session::recoverUnfinished(g_sd, serialLog);
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
  static uint32_t previousUs = 0;
  const uint32_t nowUs = micros();
  if (previousUs != 0 && nowUs - previousUs > g_diag.loop_gap_max_us) {
    g_diag.loop_gap_max_us = nowUs - previousUs;
  }
  previousUs = nowUs;
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
  if (g_state != ST_RECORDING || g_ring.available() < session::Writer::CHUNK_CAP) {
    delay(1);
  }
}

