# Архитектура прошивки

```text
PDM microphone
GPIO42 CLK / GPIO41 DATA
        |
        v
    ESP_I2S
16 kHz / 16-bit / mono
        |
        v
  4096-byte buffer
        |
        v
   FAT32 microSD
        |
        v
   REC0007_P01.WAV  (part files, .TMP while open, .BAD if failed)
```

## Главный принцип

Запись идёт небольшими блоками прямо на SD. Мы не используем `recordWAV()` как основной механизм длительной записи, потому что он буферизует весь фрагмент в памяти. Для носимого диктофона потоковая схема лучше масштабируется на длительные записи.

## Состояния v0.1

```text
BOOT
  |
  +--> init PDM
  +--> init SD
  |
  v
IDLE <--------------------+
  |                        |
  | r / future REC button |
  v                        |
RECORDING -----------------+
```

## Резервируемые ресурсы

- GPIO41/42: Sense PDM microphone
- GPIO7/8/9: Sense microSD SPI
- GPIO21: Sense microSD CS (shared with the user LED)
- GPIO1: future external REC button

Камера в проекте не используется.
