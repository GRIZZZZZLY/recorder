#!/usr/bin/env python3
"""Проверка записей диктофона: заголовок против реального размера, сессии из частей.

Повторяет правило прошивки (XiaoVoiceRecorder.ino):
здоровый файл — тот, у которого declared == floor((size - 44) / frame) * frame.

Схема имён на карте:
  REC0007_P01.WAV     готовая часть 1 сессии 7
  REC0007_P02.TMP     часть не закрыта, питание пропало во время записи
  REC0007_P03.BAD     часть с ошибкой записи, готовой не считается
  REC0007_P02_R.WAV   часть восстановлена при старте прошивки

  python tools/check_wav.py /path/REC0007_P01.WAV ...   проверить файлы
  python tools/check_wav.py --dir E:/                    обойти карту по сессиям
  python tools/check_wav.py --selftest                   проверить сам расчёт
"""
import os
import re
import struct
import sys

HEADER_BYTES = 44
SAMPLE_RATE = 16000
CHANNELS = 1
BITS = 16
FRAME_BYTES = CHANNELS * BITS // 8
BYTES_PER_SECOND = SAMPLE_RATE * FRAME_BYTES

# REC0007_P02_R.WAV -> сессия 7, часть 2, восстановлена
PART_RE = re.compile(r"^REC(\d{4})_P(\d{2})(_R)?\.(WAV|TMP|BAD)$", re.IGNORECASE)


def expected_data_bytes(total_size):
    """Сколько целых PCM-кадров реально лежит в файле."""
    if total_size <= HEADER_BYTES:
        return 0
    return (total_size - HEADER_BYTES) // FRAME_BYTES * FRAME_BYTES


def parse_header(head):
    """Разбор нашего канонического 44-байтного заголовка. None — чужой формат."""
    if len(head) < HEADER_BYTES:
        return None
    if head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None
    if head[12:16] != b"fmt " or head[36:40] != b"data":
        return None
    fmt, channels, rate = struct.unpack_from("<HHI", head, 20)
    bits = struct.unpack_from("<H", head, 34)[0]
    declared = struct.unpack_from("<I", head, 40)[0]
    if (fmt, channels, rate, bits) != (1, CHANNELS, SAMPLE_RATE, BITS):
        return None
    return declared


def check(path):
    """Возвращает (ok, секунды, сообщение)."""
    with open(path, "rb") as f:
        head = f.read(HEADER_BYTES)
        f.seek(0, 2)
        total = f.tell()

    declared = parse_header(head)
    if declared is None:
        return False, 0.0, "не наш формат: ожидается PCM 16 кГц / 16 бит / моно"

    expected = expected_data_bytes(total)
    seconds = expected / BYTES_PER_SECOND
    if path.upper().endswith(".TMP"):
        return False, seconds, f"часть не закрыта, на карте {seconds:.1f} с"
    if path.upper().endswith(".BAD"):
        return False, seconds, f"часть помечена сбойной, {seconds:.1f} с"
    if declared != expected:
        if declared == 0:
            return False, seconds, f"заголовок не закрыт, на карте {expected} байт"
        return False, seconds, f"заголовок обещает {declared} байт, в файле {expected}"
    return True, seconds, f"ок, {expected} байт аудио, {seconds:.1f} с"


def scan_dir(root):
    """Обходит карту и печатает сводку по сессиям. Возвращает число проблем."""
    sessions = {}
    for name in sorted(os.listdir(root)):
        m = PART_RE.match(name)
        if m:
            sessions.setdefault(int(m.group(1)), []).append((int(m.group(2)), name, m))

    if not sessions:
        print("Записей не найдено.")
        return 0

    problems = 0
    for session, parts in sorted(sessions.items()):
        total_seconds = 0.0
        lines = []
        for index, name, m in sorted(parts):
            ok, seconds, message = check(os.path.join(root, name))
            recovered = " (восстановлена)" if m.group(3) else ""
            lines.append(f"    {'OK  ' if ok else 'FAIL'} {name}{recovered}: {message}")
            total_seconds += seconds
            problems += not ok

        indexes = sorted(i for i, _, _ in parts)
        gaps = [i for i in range(1, max(indexes) + 1) if i not in indexes]
        print(f"Сессия {session:04d}: частей {len(parts)}, всего {total_seconds / 60:.1f} мин")
        print("\n".join(lines))
        if gaps:
            print(f"    ПРОПУСК частей: {gaps} — записи не хватает")
            problems += 1
    return problems


def selftest():
    frame = FRAME_BYTES
    assert expected_data_bytes(HEADER_BYTES) == 0
    assert expected_data_bytes(0) == 0
    assert expected_data_bytes(HEADER_BYTES + 100) == 100
    # нечётный хвост отбрасывается, кадр не режется пополам
    assert expected_data_bytes(HEADER_BYTES + 101) == 100
    assert expected_data_bytes(HEADER_BYTES + frame - 1) == 0
    # час записи
    assert expected_data_bytes(HEADER_BYTES + 3600 * BYTES_PER_SECOND) == 115_200_000
    # разбор имён частей
    assert PART_RE.match("REC0007_P01.WAV").groups() == ("0007", "01", None, "WAV")
    assert PART_RE.match("REC0007_P02_R.WAV").group(3) == "_R"
    assert PART_RE.match("REC0007.WAV") is None
    print("selftest ok")


def main(argv):
    # Консоль Windows по умолчанию не в UTF-8, иначе вывод нечитаем.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not argv or argv[0] == "--selftest":
        selftest()
        return 0

    if argv[0] == "--dir":
        if len(argv) < 2:
            print("Укажите каталог: --dir E:/")
            return 2
        return 1 if scan_dir(argv[1]) else 0

    failed = 0
    for path in argv:
        try:
            ok, _, message = check(path)
        except OSError as exc:
            ok, message = False, f"не читается: {exc}"
        print(f"{'OK  ' if ok else 'FAIL'} {path}: {message}")
        failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
