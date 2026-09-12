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


def tone_wav(seconds, hz):
    """Тихий синус: слышно, что проигрыватель работает."""
    frames = [struct.pack("<h", int(6000 * math.sin(2 * math.pi * hz * i / SAMPLE_RATE)))
              for i in range(SAMPLE_RATE * seconds)]
    payload = b"".join(frames)
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


def add_part(dbm, conn, session, part, utterances, *, recovered=0, failed=False,
             seconds=PART_SECONDS, deepgram_seconds=None, words=None):
    audio = tone_wav(seconds, 380 + part * 60)
    name = f"{DEVICE}_S{session:04d}_P{part:02d}.wav"
    path = dbm.AUDIO_DIR / name
    path.write_bytes(audio)

    conn.execute(
        "INSERT OR REPLACE INTO parts (device_id, session, part, bytes, crc32,"
        " recovered, audio_path, received_at) VALUES (?,?,?,?,?,?,?,"
        " datetime('now','-2 hours'))",
        (DEVICE, session, part, len(audio), f"{part:08x}", recovered, str(path)),
    )
    conn.execute(
        "INSERT OR IGNORE INTO transcripts (device_id, session, part) VALUES (?,?,?)",
        (DEVICE, session, part),
    )
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
        add_part(db, conn, 7, 1, PART1)
        add_part(db, conn, 7, 2, PART2, recovered=1, words=PART2_WORDS)
        db.set_speaker(conn, DEVICE, 7, 0, "Игорь", True)
        db.set_speaker(conn, DEVICE, 7, 1, "Мария", False)
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

        # Сессия 9: часть 2 не расшифровалась, часть 3 пропущена — видно на /status.
        add_part(db, conn, 9, 1, [utt(2, 7, 0, "Первая часть разобрана нормально.")])
        add_part(db, conn, 9, 2, [], failed=True)
        add_part(db, conn, 9, 4, [utt(1, 5, 0, "Четвёртая часть, третьей нет.")],
                 deepgram_seconds=25.0)   # расхождение длительности
        conn.commit()

    print("Готово: 0007 (саммари, источники, имена), 0008 (ждёт саммари),"
          " 0009 (сбой, пропуск части, расхождение длительности)")


if __name__ == "__main__":
    main()
