#!/usr/bin/env python3
"""Демо-данные для веб-клиента: посмотреть интерфейс без платы и без Deepgram.

Пишет настоящие WAV-файлы со слышимым тоном и заполняет базу так, как её
заполнил бы воркер. Платные вызовы не делаются.

    python tools/seed_demo.py                 # в ./demo-data
    RECORDER_DATA=/tmp/x python tools/seed_demo.py
"""
import json
import math
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

SAMPLE_RATE = 16000
DEVICE = "xiao-01"
PART_SECONDS = 40   # короткие «части», чтобы тон было слышно, а шкала — видно


def tone_second(hz, amplitude):
    """Секунда синуса. Частота целая, поэтому повтор стыкуется бесшовно."""
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * hz * i / SAMPLE_RATE)))
        for i in range(SAMPLE_RATE)
    )


def tone_wav(seconds, hz, spans=()):
    """Тихий синус: слышно, что проигрыватель работает.

    `spans` — отрезки «речи» в секундах. Внутри них тон громкий, снаружи почти
    тишина, поэтому огибающая демо-записи показывает настоящие всплески, а не
    ровную линию: сервер считает её из этого же файла и ничего не выдумывает.
    """
    loud = tone_second(hz, 6000)
    soft = tone_second(hz, 300)
    speech = set()
    for start, end in spans:
        speech.update(range(int(start), int(end) + 1))
    payload = b"".join(loud if i in speech else soft for i in range(seconds))
    if not spans:
        payload = loud * seconds
    h = bytearray(44)
    h[0:4] = b"RIFF"
    struct.pack_into("<I", h, 4, 36 + len(payload))
    h[8:12] = b"WAVE"
    h[12:16] = b"fmt "
    struct.pack_into("<IHHIIHH", h, 16, 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16)
    h[36:40] = b"data"
    struct.pack_into("<I", h, 40, len(payload))
    return bytes(h) + payload


def utt(s, e, spk, text):
    return {"s": s, "e": e, "spk": spk, "text": text}


# Секунды внутри части. Абсолютная шкала считается сервером.
PART1 = [
    utt(4, 10, 0, "Надо к пятнице выслать смету заказчику, иначе сдвинем сроки."),
    utt(11, 17, 1, "Беру на себя, пришлю в четверг вечером на проверку."),
    utt(19, 26, 0, "Бюджет по этой смете держим в пределах двух миллионов двухсот тысяч."),
    utt(27, 33, 1, "Тогда работы по фасаду выносим в отдельный этап."),
]
PART2 = [
    utt(2, 8, 0, "Ещё надо проверить договор с подрядчиком, там пункт про пени."),
    utt(9, 13, 1, "Кто смотрит, пока не решили."),
    utt(14, 21, 0, "Договорились: без правок по пени договор не подписываем."),
    utt(22, 30, 2, "Напомню, встреча со стройнадзором двадцать третьего сентября."),
    utt(31, 36, 1, "А кто вообще согласует фасад с городом?"),
]

# Время и цитаты — как их вернула бы модель по абсолютной шкале.
# Одна цитата нарочно не совпадает с транскриптом: видно пометку.
SUMMARY = {
    "topics": ["смета для заказчика", "договор с подрядчиком"],
    "summary": (
        "Обсудили смету для заказчика и договор с подрядчиком. Смету готовит "
        "Мария, срок — четверг, отправка заказчику в пятницу. Бюджет держат в "
        "пределах 2,2 млн, работы по фасаду выносят в отдельный этап. По договору "
        "с подрядчиком принято решение не подписывать без правок пункта о пени, "
        "ответственный не определён."
    ),
    "decisions": [
        {"text": "Работы по фасаду выносим в отдельный этап", "time": "0:00:27",
         "quote": "работы по фасаду выносим в отдельный этап"},
        {"text": "Договор не подписываем без правок пункта о пени", "time": "0:00:54",
         "quote": "без правок по пени договор не подписываем"},
    ],
    "facts": [
        {"text": "Предел бюджета по смете: 2 200 000", "time": "0:00:19",
         "quote": "в пределах двух миллионов двухсот тысяч"},
        {"text": "Встреча со стройнадзором 23 сентября", "time": "0:01:02",
         "quote": "встреча со стройнадзором двадцать третьего сентября"},
    ],
    "tasks": [
        {"text": "Выслать смету заказчику", "owner": "Мария", "due": "пятница",
         "time": "0:00:04", "quote": "к пятнице выслать смету заказчику"},
        {"text": "Проверить пункт о пени в договоре с подрядчиком", "owner": "",
         "due": "", "time": "0:00:42", "quote": "проверить договор с субподрядчиком"},
    ],
    "open_questions": [
        {"text": "Кто согласует фасад с городом", "time": "0:01:11",
         "quote": "кто вообще согласует фасад с городом"},
    ],
}


# Слова с уверенностью распознавания: число ниже порога попадает в спорные места.
PART2_WORDS = [
    {"w": "стройнадзором", "s": 24.0, "e": 24.8, "spk": 2, "c": 0.91},
    {"w": "23", "s": 25.0, "e": 25.4, "spk": 2, "c": 0.52},
    {"w": "сентября", "s": 25.5, "e": 26.1, "spk": 2, "c": 0.95},
]


# Время выгрузки по сессиям — сегодня по местным часам, чтобы лента дня на
# сводке была не пустой и блоки не лежали друг на друге. received_at хранится
# в UTC, как пишет сервер, поэтому местное время переводится перед записью.
SESSION_TIMES = {7: (9, 40), 8: (12, 15), 9: (15, 30), 10: (13, 5)}


def received_at(session, part):
    from datetime import datetime, timedelta, timezone
    hour, minute = SESSION_TIMES.get(session, (10, 0))
    local = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    local += timedelta(seconds=PART_SECONDS * (part - 1))
    return local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def add_part(dbm, conn, session, part, utterances, *, recovered=0, failed=False,
             seconds=PART_SECONDS, deepgram_seconds=None, words=None, marks=()):
    audio = tone_wav(seconds, 380 + part * 60,
                     [(u["s"], u["e"]) for u in utterances])
    name = f"{DEVICE}_S{session:04d}_P{part:02d}.wav"
    path = dbm.AUDIO_DIR / name
    path.write_bytes(audio)

    conn.execute(
        "INSERT OR REPLACE INTO parts (device_id, session, part, bytes, crc32,"
        " recovered, audio_path, received_at) VALUES (?,?,?,?,?,?,?,?)",
        (DEVICE, session, part, len(audio), f"{part:08x}", recovered, str(path),
         received_at(session, part)),
    )
    conn.execute(
        "INSERT OR IGNORE INTO transcripts (device_id, session, part) VALUES (?,?,?)",
        (DEVICE, session, part),
    )
    dbm.save_marks(conn, DEVICE, session, part, marks)
    # Та же огибающая, что посчитал бы приёмник: тон ровный, поэтому волна
    # демо-записи ровная — это честно, громкость в ней и правда не меняется.
    import envelope
    envelope.store(conn, DEVICE, session, part, path)
    conn.commit()
    if failed:
        dbm.fail_transcript(conn, (DEVICE, session, part),
                            "Deepgram отказал 400: corrupt or unsupported data", None)
    else:
        text = " ".join(u["text"] for u in utterances)
        dbm.save_transcript(conn, (DEVICE, session, part), text,
                            json.dumps(words or [], ensure_ascii=False), utterances,
                            deepgram_seconds if deepgram_seconds is not None else float(seconds))


def main():
    os.environ.setdefault("RECORDER_DATA", "./demo-data")
    import db
    import summarize

    db.init_storage()
    print("Данные:", db.DATA_DIR)

    with db.connect() as conn:
        for table in ("parts", "transcripts", "summaries", "speaker_names",
                      "transcript_fts", "tasks", "question_states", "project_aliases"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()

        # Сессия 7: две части, имена заданы, саммари с источниками.
        # Метки — нажатия кнопки на диктофоне: решение по бюджету и дата
        # встречи. Смещения — секунды от начала своей части.
        add_part(db, conn, 7, 1, PART1, marks=[21.0])
        add_part(db, conn, 7, 2, PART2, recovered=1, words=PART2_WORDS, marks=[16.0, 24.0])
        # Имена задаются для каждой части: номер говорящего живёт внутри своей
        # части, потому что части распознаются отдельными запросами.
        for part in (1, 2):
            db.set_speaker(conn, DEVICE, 7, part, 0, "Игорь", True)
            db.set_speaker(conn, DEVICE, 7, part, 1, "Мария", False)
        timeline, _ = db.session_timeline(conn, DEVICE, 7)
        result = summarize.attach_sources(json.loads(json.dumps(SUMMARY)), timeline)
        conn.execute(
            "INSERT OR REPLACE INTO summaries (device_id, session, status,"
            " parts_covered, result) VALUES (?,?, 'done', 2, ?)",
            (DEVICE, 7, json.dumps(result, ensure_ascii=False)),
        )
        conn.commit()
        db.sync_tasks(conn, DEVICE, 7, result["tasks"])

        # Сессия 8: расшифровано, саммари ещё нет.
        add_part(db, conn, 8, 1, [utt(3, 8, 0, "Короткая заметка на утро, проверить почту.")])

        # Сессия 10: полчаса разговора двумя частями. Нужна ленте дня: на ней
        # видно настоящий блок в 40 px на час, а не минимальную заглушку, как у
        # коротких демо-сессий выше. Аудио настоящее, 28,8 МБ тона.
        add_part(db, conn, 10, 1, [
            utt(30, 90, 0, "Начали планёрку, прошлись по статусам."),
            utt(240, 300, 1, "По интеграции всё готово, ждём доступы."),
        ], seconds=900, marks=[255.0])
        add_part(db, conn, 10, 2, [
            utt(60, 120, 0, "Решили: релиз переносим на понедельник."),
        ], seconds=900, marks=[70.0])

        # Сессия 9: часть 2 не расшифровалась, часть 3 пропущена — видно на /status.
        add_part(db, conn, 9, 1, [utt(2, 7, 0, "Первая часть разобрана нормально.")])
        add_part(db, conn, 9, 2, [], failed=True)
        add_part(db, conn, 9, 4, [utt(1, 5, 0, "Четвёртая часть, третьей нет.")],
                 deepgram_seconds=25.0)   # расхождение длительности
        conn.commit()

    print("Готово: 0007 (саммари, источники, имена, метки), 0008 (ждёт саммари),"
          " 0009 (сбой, пропуск части, расхождение длительности),"
          " 0010 (полчаса на ленте дня, метки)")


if __name__ == "__main__":
    main()
