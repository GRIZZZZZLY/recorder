"""Архив: принятый WAV превращается в FLAC без потерь.

Зачем: PCM 16 кГц/16 бит — 115 МБ на час. FLAC хранит тот же звук примерно
вдвое компактнее и остаётся точной копией: декодированный PCM обязан совпасть
байт в байт.

Порядок шагов выбран так, чтобы ни один сбой не оставил владельца без звука:

    1. кодируем WAV -> <имя>.flac.part
    2. декодируем обе стороны обратно в PCM и сверяем MD5
    3. .part -> .flac одним atomic replace
    4. обновляем audio_path в базе
    5. и только теперь удаляем WAV

Падение на любом шаге оставляет рабочую копию. Крах между 3 и 4 оставит
осиротевший .flac — следующая попытка перезапишет его. Крах между 4 и 5
оставит лишний WAV: его подбирает sweep_stale_wavs() при старте воркера.

MD5 считает сам ffmpeg (`-f md5`) по декодированному потоку pcm_s16le. Это и
есть доказательство того, что сжатие без потерь: сравниваются не файлы, а звук.
"""
import os
import subprocess
from pathlib import Path

FFMPEG = os.environ.get("RECORDER_FFMPEG", "ffmpeg")
# Выключатель на случай, если владелец решит остаться на WAV.
ENABLED = os.environ.get("RECORDER_ARCHIVE_FLAC", "1") != "0"
# 8 — предел обычного FLAC. Считает сервер, а не диктофон, время не жалко.
COMPRESSION_LEVEL = os.environ.get("RECORDER_FLAC_LEVEL", "8")
# Десятиминутная часть кодируется секунды; минута — это уже зависший процесс.
TIMEOUT_SEC = int(os.environ.get("RECORDER_FFMPEG_TIMEOUT", 600))


class ArchiveError(Exception):
    """Сбой конвертации. retryable=False — повтор не поможет (битый файл)."""

    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


def _run(args):
    try:
        done = subprocess.run(
            [FFMPEG, "-nostdin", "-v", "error", *args],
            capture_output=True, timeout=TIMEOUT_SEC,
        )
    except FileNotFoundError:
        # ffmpeg не установлен: чинится руками, но части ждут, а не теряются.
        raise ArchiveError(f"не найден {FFMPEG}", retryable=True)
    except subprocess.TimeoutExpired:
        raise ArchiveError(f"ffmpeg не ответил за {TIMEOUT_SEC} с", retryable=True)
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip()[:300]
        raise ArchiveError(f"ffmpeg вернул {done.returncode}: {detail}", retryable=False)
    return done


def pcm_md5(path):
    """MD5 декодированного PCM. Одинаковый у WAV и его FLAC — значит без потерь."""
    done = _run(["-i", str(path), "-map", "0:a", "-c:a", "pcm_s16le", "-f", "md5", "-"])
    text = done.stdout.decode("ascii", "replace").strip()
    if not text.startswith("MD5="):
        raise ArchiveError(f"ffmpeg не выдал MD5: {text[:120]!r}", retryable=False)
    return text[4:].strip()


def encode(src, dst):
    _run(["-y", "-i", str(src), "-map", "0:a", "-c:a", "flac",
          "-compression_level", COMPRESSION_LEVEL, "-f", "flac", str(dst)])


def convert(wav_path):
    """WAV -> проверенный FLAC рядом. Возвращает путь к FLAC.

    Исходный WAV не трогает: удалять его — дело вызывающего, после того как
    новый путь записан в базу.
    """
    wav_path = Path(wav_path)
    if not wav_path.exists():
        raise ArchiveError(f"нет файла {wav_path}", retryable=False)

    flac_path = wav_path.with_suffix(".flac")
    tmp_path = wav_path.with_suffix(".flac.part")
    tmp_path.unlink(missing_ok=True)
    try:
        encode(wav_path, tmp_path)
        original = pcm_md5(wav_path)
        copy = pcm_md5(tmp_path)
        if original != copy:
            # Не бывает при исправном ffmpeg, и именно поэтому проверяется:
            # молча подменить архив испорченной копией хуже, чем не сжать.
            raise ArchiveError(
                f"FLAC не совпал с оригиналом: {copy} вместо {original}", retryable=False
            )
        if tmp_path.stat().st_size == 0:
            raise ArchiveError("FLAC пустой", retryable=False)
        os.replace(tmp_path, flac_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return flac_path
