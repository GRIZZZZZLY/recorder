"""Обработчик очереди: расшифровка частей и саммари сессий.

Запуск под systemd, одна копия. Работа выводится из состояния базы, поэтому
перезапуск в любой момент безопасен: незавершённая часть просто снова станет
кандидатом.

    python worker.py            бесконечный цикл
    python worker.py --once     один проход, для проверки руками
"""
import argparse
import json
import logging
import sys
import time
from contextlib import closing
from pathlib import Path

import archive
import db
import stt
import summarize
import voiceprint

log = logging.getLogger("worker")

IDLE_SLEEP_SEC = 20
# Временный сбой отодвигает попытку всё дальше, но не дальше суток, и никогда
# не закрывает работу: лежащий Deepgram или пропавший интернет — не повод
# бросать часть. Навсегда закрывает только неисправимое: битое аудио, нет ключа.
RETRY_MINUTES = [1, 5, 30, 120, 600, 1440]


def backoff_minutes(attempts):
    """Пауза после сбоя по числу уже сделанных попыток: первая — минута."""
    return RETRY_MINUTES[min(attempts, len(RETRY_MINUTES) - 1)]


def run_transcription(conn):
    """Одна часть. Возвращает True, если работа была."""
    row = db.claim_part_for_transcription(conn)
    if row is None:
        return False

    key = (row["device_id"], row["session"], row["part"])
    log.info("Расшифровка %s сессия %04d часть %02d", *key)

    try:
        # Движок выбирается в stt.py: остальной воркер не знает его имени.
        result = stt.backend().transcribe(row["audio_path"])
    except stt.TranscribeError as error:
        # Аудио остаётся на месте в любом случае: неудачная расшифровка
        # не повод терять запись.
        if not error.retryable:
            db.fail_transcript(conn, key, str(error), None)
            log.error("Расшифровка невозможна, %s: %s", key, error)
        else:
            wait = error.wait_minutes or backoff_minutes(row["attempts"])
            db.fail_transcript(conn, key, str(error), wait)
            log.warning("Расшифровка отложена на %d мин, попытка %d: %s",
                        wait, row["attempts"] + 1, error)
        return True

    db.save_transcript(
        conn, key, result.text,
        json.dumps(result.words, ensure_ascii=False),
        result.utterances,
        result.duration_sec,
    )
    log.info("Готово: %d символов, %.1f с аудио", len(result.text), result.duration_sec)
    try:
        changed = voiceprint.remember_part(conn, key, row["audio_path"], result.utterances)
        if changed:
            log.info("Имена по голосу: изменено %d", changed)
    except Exception:
        # Расшифровка уже сохранена; без имён по голосу часть остаётся рабочей.
        log.exception("Отпечатки голоса не сняты, %s", key)
    return True


def run_summary(conn):
    """Одна сессия. Возвращает True, если работа была."""
    row = db.claim_session_for_summary(conn)
    if row is None:
        return False

    device_id, session = row["device_id"], row["session"]
    ready_parts = row["ready_parts"]
    log.info("Саммари %s сессия %04d, частей %d", device_id, session, ready_parts)

    text = db.session_transcript(conn, device_id, session)
    if not text.strip():
        # Расшифровать не удалось ничего. Отмечаем и не дёргаем модель впустую.
        db.save_summary(
            conn, device_id, session,
            json.dumps({"summary": "Расшифровка недоступна, саммари не построено.",
                        "topics": [], "decisions": [], "facts": [], "tasks": [],
                        "open_questions": []},
                       ensure_ascii=False),
            ready_parts,
        )
        db.sync_tasks(conn, device_id, session, [])
        return True

    try:
        result = summarize.summarize(text)
    except summarize.SummaryError as error:
        message = str(error)
        # Транскрипт остаётся доступным в любом случае: сбой саммари не должен
        # прятать уже готовый текст.
        if summarize.is_auth_failure(message):
            # Истёкший токен лечится руками. Пробуем раз в час, чтобы лог не тонул.
            db.fail_summary(conn, device_id, session, f"нужен вход: {message}", 60)
            log.error("Саммари остановлено, нужен вход заново: %s", message)
        elif not error.retryable:
            db.fail_summary(conn, device_id, session, message, 1440)
            log.error("Саммари не получилось: %s", message)
        else:
            wait = backoff_minutes(row["attempts"])
            db.fail_summary(conn, device_id, session, message, wait)
            log.warning("Саммари отложено на %d мин, попытка %d: %s",
                        wait, row["attempts"] + 1, message)
        return True

    # Каждый вывод привязывается к реплике. Неподтверждённая цитата остаётся
    # видимой с пометкой: это первый вид спорных мест для владельца.
    timeline, _ = db.session_timeline(conn, device_id, session)
    result = summarize.attach_sources(result, timeline)

    db.save_summary(conn, device_id, session,
                    json.dumps(result, ensure_ascii=False), ready_parts)
    # Задачи живут отдельно от саммари: у них ручной статус, который пересборка
    # после поздней части терять не должна.
    db.sync_tasks(conn, device_id, session, result.get("tasks"))
    unconfirmed = sum(
        1 for name in summarize.SOURCED_LISTS for item in result.get(name) or []
        if not (item.get("source") or {}).get("confirmed")
    )
    log.info("Саммари готово, задач: %d, пунктов без подтверждённого источника: %d",
             len(result.get("tasks", [])), unconfirmed)
    return True


def run_archive(conn):
    """Одна часть: принятый WAV -> проверенный FLAC. True, если работа была.

    Порядок шагов держит archive.py; здесь только очередь и удаление WAV,
    которое делается последним — после того, как путь к проверенной копии
    уже записан в базу."""
    if not archive.ENABLED:
        return False
    row = db.claim_part_for_archive(conn)
    if row is None:
        return False

    key = (row["device_id"], row["session"], row["part"])
    wav = Path(row["audio_path"])
    log.info("Архив %s сессия %04d часть %02d", *key)

    try:
        flac = archive.convert(wav)
    except archive.ArchiveError as error:
        if error.retryable:
            wait = backoff_minutes(row["archive_attempts"])
            db.fail_archive(conn, key, str(error), wait)
            log.warning("Сжатие отложено на %d мин, попытка %d: %s",
                        wait, row["archive_attempts"] + 1, error)
        else:
            # Аудио остаётся в WAV и продолжает работать: не сжали — не беда.
            db.fail_archive(conn, key, str(error), None)
            log.error("Сжать не удалось, часть остаётся в WAV, %s: %s", key, error)
        return True

    if not db.finish_archive(conn, key, flac):
        # Сессию удалили, пока шло сжатие: свежий FLAC — лишняя копия удалённого.
        flac.unlink(missing_ok=True)
        wav.unlink(missing_ok=True)
        return True
    try:
        wav.unlink()
    except OSError as error:
        # Проверенный FLAC уже в базе, WAV лишний. Подберёт sweep при старте.
        log.warning("WAV не удалился, %s: %s", wav, error)
    # bytes — размер принятого WAV; самого файла уже нет, а строка осталась.
    log.info("Сжато: %s -> %s, %.0f%% от исходного", wav.name, flac.name,
             100.0 * flac.stat().st_size / max(1, row["bytes"]))
    return True


def tick(conn):
    """Один проход. True, если что-то сделали."""
    did_work = run_transcription(conn)
    # Саммари после расшифровки: иначе сессия ждала бы лишний цикл.
    did_work = run_summary(conn) or did_work
    # Сжатие последним: оно ничего не задерживает и может ждать сколько угодно.
    did_work = run_archive(conn) or did_work
    return did_work


def main(argv=None):
    parser = argparse.ArgumentParser(description="Очередь расшифровки и саммари")
    parser.add_argument("--once", action="store_true", help="один проход и выход")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    db.init_storage()

    with closing(db.connect()) as conn:
        # Крах между записью пути в базу и удалением WAV оставляет лишний файл.
        # Проверенный FLAC на него уже заменил, поэтому WAV можно убрать.
        for leftover in db.stale_wavs(conn):
            leftover.unlink(missing_ok=True)
            log.info("Убран WAV, оставшийся от прошлого запуска: %s", leftover.name)

        if args.once:
            return 0 if tick(conn) else 0
        log.info("Воркер запущен.")
        while True:
            try:
                if not tick(conn):
                    time.sleep(IDLE_SLEEP_SEC)
            except KeyboardInterrupt:
                log.info("Остановка по запросу.")
                return 0
            except Exception:
                # Падение воркера остановило бы всю обработку. Ошибка уже
                # записана в строку задачи, здесь только не даём умереть циклу.
                log.exception("Непредвиденная ошибка в цикле")
                time.sleep(IDLE_SLEEP_SEC)


if __name__ == "__main__":
    sys.exit(main())
