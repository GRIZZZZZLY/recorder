"""Схема и доступ к SQLite. Одна база на владельца, файлы аудио лежат рядом.

Очередь намеренно не хранится отдельной таблицей задач: работа выводится из
состояния строк. Зависшее задание невозможно, потому что задания как объекта
не существует — есть часть без транскрипта и сессия без свежего саммари.
"""
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import duedate
import envelope
import textnorm

# Путь приводится к абсолютному сразу: в базу попадает audio_path, а приёмник,
# воркер и systemd-юниты запускаются из разных каталогов. Относительный путь
# читался бы у одного и ломался у другого.
DATA_DIR = Path(os.environ.get("RECORDER_DATA", "./data")).resolve()
DB_PATH = DATA_DIR / "recorder.db"
AUDIO_DIR = DATA_DIR / "audio"

# Сессия считается завершённой, если новых частей не приходило столько времени.
# Больше, чем интервал повтора у диктофона (5 минут), иначе саммари уйдёт
# в работу посреди незаконченной выгрузки.
SESSION_QUIET_MINUTES = int(os.environ.get("RECORDER_QUIET_MINUTES", 12))

BYTES_PER_SECOND = 16000 * 2
WAV_HEADER_BYTES = 44

# Ниже этой уверенности распознавания число в факте показывается владельцу
# как спорное. 0,7 — начальное значение, его надо проверить на живых записях.
MIN_WORD_CONFIDENCE = float(os.environ.get("RECORDER_MIN_WORD_CONFIDENCE", 0.7))

SCHEMA = """
CREATE TABLE IF NOT EXISTS parts (
    device_id   TEXT    NOT NULL,
    session     INTEGER NOT NULL,
    part        INTEGER NOT NULL,
    bytes       INTEGER NOT NULL,
    crc32       TEXT    NOT NULL,
    recovered   INTEGER NOT NULL DEFAULT 0,
    audio_path  TEXT    NOT NULL,
    -- Огибающая громкости: JSON [0..100], одно число на две секунды звука.
    -- Считается один раз при приёме (envelope.py). NULL — не посчитана.
    envelope    TEXT,
    received_at TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Одна часть сессии одного устройства существует ровно в одном экземпляре.
    -- Это и есть идемпотентность: повтор упрётся сюда, а не создаст дубль.
    PRIMARY KEY (device_id, session, part)
);

-- Транскрипт привязан к части, а не к сессии: часть приходит и обрабатывается
-- независимо, и поздняя часть не заставляет переплачивать за уже сделанное.
-- text — чистый текст без меток; utterances — реплики с секундами от начала
-- части. Абсолютная шкала сессии и имена подставляются при чтении.
CREATE TABLE IF NOT EXISTS transcripts (
    device_id    TEXT    NOT NULL,
    session      INTEGER NOT NULL,
    part         INTEGER NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',  -- pending | done | failed
    text         TEXT,
    words        TEXT,                                -- JSON: слова со временем и говорящим
    utterances   TEXT,                                -- JSON: [{s, e, spk, text}]
    duration_sec REAL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    next_try_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (device_id, session, part)
);

CREATE TABLE IF NOT EXISTS summaries (
    device_id     TEXT    NOT NULL,
    session       INTEGER NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending',
    -- Сколько частей вошло в это саммари. Пришла ещё одна — саммари устарело.
    parts_covered INTEGER NOT NULL DEFAULT 0,
    result        TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    next_try_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (device_id, session)
);

-- Ручные имена для номеров диаризации внутри одной сессии. Исходный номер
-- в транскрипте не меняется. is_me пока ничего не считает: он накапливает
-- размеченные образцы голоса для будущего определения своего голоса.
CREATE TABLE IF NOT EXISTS speaker_names (
    device_id TEXT    NOT NULL,
    session   INTEGER NOT NULL,
    speaker   INTEGER NOT NULL,
    name      TEXT    NOT NULL DEFAULT '',
    is_me     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, session, speaker)
);

-- Задачи вынесены из саммари в таблицу: у них есть ручной статус, а саммари
-- пересобирается после поздней части. task_id считается из текста и цитаты,
-- поэтому пересборка находит ту же строку и не теряет закрытие владельца.
-- Пропавшая из нового саммари задача не удаляется, а помечается vanished.
CREATE TABLE IF NOT EXISTS tasks (
    device_id  TEXT    NOT NULL,
    session    INTEGER NOT NULL,
    task_id    TEXT    NOT NULL,
    text       TEXT    NOT NULL,
    owner      TEXT    NOT NULL DEFAULT '',
    due_said   TEXT    NOT NULL DEFAULT '',   -- срок так, как прозвучал
    due_date   TEXT,                          -- разобранная дата или NULL
    source     TEXT,                          -- JSON источника из саммари
    status     TEXT    NOT NULL DEFAULT 'open',  -- open | done | dismissed
    vanished   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    closed_at  TEXT,
    PRIMARY KEY (device_id, session, task_id)
);

-- У вопросов ручного статуса меньше: сам список читается из саммари, здесь
-- только пометка владельца. Отдельная таблица задач для них не нужна.
CREATE TABLE IF NOT EXISTS question_states (
    device_id   TEXT    NOT NULL,
    session     INTEGER NOT NULL,
    question_id TEXT    NOT NULL,
    state       TEXT    NOT NULL DEFAULT 'open',  -- open | answered | dropped
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (device_id, session, question_id)
);

-- Метки владельца: нажатие кнопки на диктофоне во время записи. offset —
-- секунды от начала своей части, как их посчитала прошивка; абсолютное время
-- сессии выводится при чтении из размеров частей, как и у реплик.
-- Метка принадлежит части, а не сессии: часть приходит отдельным запросом,
-- и повторная отправка не должна плодить дубли — отсюда ключ по offset.
CREATE TABLE IF NOT EXISTS marks (
    device_id TEXT    NOT NULL,
    session   INTEGER NOT NULL,
    part      INTEGER NOT NULL,
    offset    REAL    NOT NULL,
    PRIMARY KEY (device_id, session, part, offset)
);

-- «Пози флора» и «Posiflora» — один проект. Ключ нормализован, отображаемое
-- имя проекта хранится как написал владелец.
CREATE TABLE IF NOT EXISTS project_aliases (
    alias     TEXT PRIMARY KEY,
    canonical TEXT NOT NULL
);

-- Одна строка на реплику. Ищем по леммам: встроенный стеммер FTS5 знает
-- только английский, русские формы слова сводятся заранее в textnorm.
CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
    device_id UNINDEXED,
    session   UNINDEXED,
    part      UNINDEXED,
    idx       UNINDEXED,
    text,
    lemmas,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Приёмка не должна ждать, пока воркер читает базу.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_storage():
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as conn:
        conn.executescript(SCHEMA)
        # CREATE TABLE IF NOT EXISTS не добавляет колонки в уже существующую
        # таблицу. Единственная миграция на сегодня: огибающая у частей,
        # принятых до её появления, останется NULL — интерфейс это переживёт.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(parts)")}
        if "envelope" not in columns:
            conn.execute("ALTER TABLE parts ADD COLUMN envelope TEXT")
        conn.commit()


def format_abs(seconds):
    """Абсолютное время сессии: Ч:ММ:СС. Часы без ведущего нуля."""
    total = int(max(0, seconds))
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def parse_abs(label):
    """Обратно из Ч:ММ:СС или ММ:СС в секунды. None, если не разобрать."""
    if not label:
        return None
    pieces = str(label).strip().split(":")
    try:
        numbers = [int(p) for p in pieces]
    except ValueError:
        return None
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    return None


# --- очередь расшифровки -----------------------------------------------------

def claim_part_for_transcription(conn):
    """Часть без готового транскрипта, у которой подошло время попытки."""
    conn.execute(
        "INSERT OR IGNORE INTO transcripts (device_id, session, part)"
        " SELECT device_id, session, part FROM parts"
    )
    row = conn.execute(
        "SELECT t.device_id, t.session, t.part, t.attempts, p.audio_path"
        " FROM transcripts t JOIN parts p"
        "   ON p.device_id = t.device_id AND p.session = t.session AND p.part = t.part"
        " WHERE t.status = 'pending' AND t.next_try_at <= datetime('now')"
        " ORDER BY t.session, t.part LIMIT 1"
    ).fetchone()
    conn.commit()
    return row


def save_transcript(conn, key, text, words_json, utterances, duration_sec):
    """Сохраняет расшифровку части и перестраивает её строки в поиске."""
    device_id, session, part = key
    conn.execute(
        "UPDATE transcripts SET status='done', text=?, words=?, utterances=?,"
        " duration_sec=?, last_error=NULL, updated_at=datetime('now')"
        " WHERE device_id=? AND session=? AND part=?",
        (text, words_json, json.dumps(utterances, ensure_ascii=False),
         duration_sec, device_id, session, part),
    )
    conn.execute(
        "DELETE FROM transcript_fts WHERE device_id=? AND session=? AND part=?",
        (device_id, session, part),
    )
    conn.executemany(
        "INSERT INTO transcript_fts (device_id, session, part, idx, text, lemmas)"
        " VALUES (?,?,?,?,?,?)",
        [
            (device_id, session, part, idx, u.get("text", ""),
             textnorm.lemmas(u.get("text", "")))
            for idx, u in enumerate(utterances)
            if u.get("text")
        ],
    )
    conn.commit()


def fail_transcript(conn, key, error, retry_in_minutes):
    """Временная ошибка отодвигает попытку, окончательная закрывает строку."""
    if retry_in_minutes is None:
        conn.execute(
            "UPDATE transcripts SET status='failed', last_error=?, attempts=attempts+1,"
            " updated_at=datetime('now') WHERE device_id=? AND session=? AND part=?",
            (error, *key),
        )
    else:
        conn.execute(
            "UPDATE transcripts SET last_error=?, attempts=attempts+1,"
            " next_try_at=datetime('now', ?), updated_at=datetime('now')"
            " WHERE device_id=? AND session=? AND part=?",
            (error, f"+{retry_in_minutes} minutes", *key),
        )
    conn.commit()


# --- говорящие ----------------------------------------------------------------

def speaker_names(conn, device_id, session):
    """{номер: {'name': ..., 'is_me': bool}} для сессии."""
    rows = conn.execute(
        "SELECT speaker, name, is_me FROM speaker_names WHERE device_id=? AND session=?",
        (device_id, session),
    ).fetchall()
    return {r["speaker"]: {"name": r["name"], "is_me": bool(r["is_me"])} for r in rows}


def set_speaker(conn, device_id, session, speaker, name, is_me):
    conn.execute(
        "INSERT INTO speaker_names (device_id, session, speaker, name, is_me)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT(device_id, session, speaker) DO UPDATE SET name=excluded.name,"
        " is_me=excluded.is_me",
        (device_id, session, speaker, name.strip(), 1 if is_me else 0),
    )
    conn.commit()


def speaker_label(names, speaker):
    """Имя, если задано, иначе технический номер. Номер не является личностью."""
    if speaker is None:
        return "Говорящий ?"
    entry = names.get(speaker)
    if entry and entry["name"]:
        return entry["name"]
    return f"Говорящий {speaker + 1}"


# --- шкала сессии -------------------------------------------------------------

def part_offsets(conn, device_id, session):
    """{часть: смещение в секундах}. Из размера файла: он есть у любой части,
    включая нерасшифрованные, поэтому шкала не едет от ошибок Deepgram."""
    rows = conn.execute(
        "SELECT part, bytes FROM parts WHERE device_id=? AND session=? ORDER BY part",
        (device_id, session),
    ).fetchall()
    offsets, cursor = {}, 0.0
    for row in rows:
        offsets[row["part"]] = cursor
        cursor += max(0, row["bytes"] - WAV_HEADER_BYTES) / BYTES_PER_SECOND
    return offsets


def session_timeline(conn, device_id, session):
    """Реплики сессии на абсолютной шкале с подставленными именами.

    Возвращает (utterances, failed_parts). Каждая реплика: part, idx, start,
    end, start_abs, speaker, label, text. Нерасшифрованные части перечислены
    отдельно, чтобы рендер честно показал дыру.
    """
    offsets = part_offsets(conn, device_id, session)
    names = speaker_names(conn, device_id, session)
    rows = conn.execute(
        "SELECT part, status, utterances FROM transcripts"
        " WHERE device_id=? AND session=? ORDER BY part",
        (device_id, session),
    ).fetchall()

    timeline, failed = [], []
    for row in rows:
        if row["status"] == "failed":
            failed.append(row["part"])
            continue
        if row["status"] != "done" or not row["utterances"]:
            continue
        base = offsets.get(row["part"], 0.0)
        for idx, u in enumerate(json.loads(row["utterances"])):
            if not u.get("text"):
                continue
            timeline.append({
                "part": row["part"],
                "idx": idx,
                "start": float(u.get("s") or 0.0),
                "end": float(u.get("e") or 0.0),
                "start_abs": base + float(u.get("s") or 0.0),
                "speaker": u.get("spk"),
                "label": speaker_label(names, u.get("spk")),
                "text": u["text"],
            })
    return timeline, failed


def session_transcript(conn, device_id, session):
    """Текст для модели и для страницы: абсолютное время, имена, границы частей.

    Метки владельца вставлены отдельными строками на своём месте в разговоре:
    модель должна видеть, что человек нажал кнопку именно здесь.
    """
    timeline, failed = session_timeline(conn, device_id, session)
    marks = session_marks(conn, device_id, session)
    parts = sorted({u["part"] for u in timeline} | set(failed))
    blocks = []
    for part in parts:
        if part in failed:
            blocks.append(f"[часть {part:02d}: расшифровка не удалась]")
            continue
        rows = [(u["start_abs"], f"[{format_abs(u['start_abs'])}] {u['label']}: {u['text']}")
                for u in timeline if u["part"] == part]
        rows += [(m["start_abs"], f"[{m['time']}] [метка владельца]")
                 for m in marks if m["part"] == part]
        rows.sort(key=lambda row: row[0])
        blocks.append("\n".join([f"[часть {part:02d}]"] + [line for _, line in rows]))
    return "\n\n".join(blocks)


def save_marks(conn, device_id, session, part, offsets):
    """Метки одной части. Повтор той же части ничего не дублирует."""
    conn.executemany(
        "INSERT OR IGNORE INTO marks (device_id, session, part, offset) VALUES (?,?,?,?)",
        [(device_id, session, part, round(float(offset), 2)) for offset in offsets],
    )


def session_envelopes(conn, buckets):
    """{(устройство, сессия): [0..100]} — громкость сессии целиком.

    Части склеиваются по порядку и сжимаются до `buckets` столбиков: списку и
    ленте дня нужна форма, а не все девятьсот чисел получасовой части.
    Непосчитанная часть (NULL) заменяется ровной линией своей длины — дыра в
    середине выглядела бы как тишина, которой не было.
    """
    found = {}
    for row in conn.execute(
        "SELECT device_id, session, part, bytes, envelope FROM parts"
        " ORDER BY device_id, session, part"
    ):
        key = (row["device_id"], row["session"])
        if row["envelope"]:
            values = json.loads(row["envelope"])
        else:
            seconds = max(0, row["bytes"] - WAV_HEADER_BYTES) / BYTES_PER_SECOND
            values = [envelope.FLOOR] * max(1, round(seconds / envelope.BUCKET_SECONDS))
        found.setdefault(key, []).extend(values)
    return {key: envelope.resample(values, buckets) for key, values in found.items()}


def mark_offsets(conn):
    """{(устройство, сессия): [секунды от начала сессии]} — для списков.

    Двумя запросами и без разбора транскриптов: списку сессий нужны только
    места меток на шкале, а session_marks ради текста реплики читает и парсит
    все реплики сессии — на списке это была бы работа за весь архив сразу.
    """
    bases = {}
    cursor = {}
    for row in conn.execute(
        "SELECT device_id, session, part, bytes FROM parts"
        " ORDER BY device_id, session, part"
    ):
        key = (row["device_id"], row["session"])
        bases[(*key, row["part"])] = cursor.get(key, 0.0)
        cursor[key] = cursor.get(key, 0.0) + max(0, row["bytes"] - WAV_HEADER_BYTES) / BYTES_PER_SECOND

    found = {}
    for row in conn.execute(
        "SELECT device_id, session, part, offset FROM marks"
        " ORDER BY device_id, session, part, offset"
    ):
        key = (row["device_id"], row["session"])
        base = bases.get((*key, row["part"]))
        if base is None:      # метка без своей части: аудио удалили руками
            continue
        found.setdefault(key, []).append(base + row["offset"])
    return found


def session_marks(conn, device_id, session):
    """Метки сессии на абсолютной шкале, с ближайшей репликой как источником.

    Ближайшая — последняя начавшаяся до метки внутри той же части: кнопку жмут
    после того, как прозвучало важное, а не до. Соседняя часть не годится даже
    если её реплика ближе по времени: смещение метки посчитала прошивка от
    начала своей части, и выйти за её пределы оно может только по ошибке.
    Реплики может не быть (часть не расшифрована или упала) — тогда источник
    пустой, и метка всё равно показывается.
    """
    offsets = part_offsets(conn, device_id, session)
    rows = conn.execute(
        "SELECT part, offset FROM marks WHERE device_id=? AND session=?"
        " ORDER BY part, offset", (device_id, session),
    ).fetchall()
    if not rows:
        return []

    timeline, _ = session_timeline(conn, device_id, session)
    marks = []
    for row in rows:
        start_abs = offsets.get(row["part"], 0.0) + row["offset"]
        before = [u for u in timeline
                  if u["part"] == row["part"] and u["start_abs"] <= start_abs + 0.5]
        near = before[-1] if before else None
        marks.append({
            "part": row["part"],
            "offset": row["offset"],
            "start_abs": start_abs,
            "time": format_abs(start_abs),
            "source": {"part": near["part"], "idx": near["idx"],
                       "start": near["start"], "start_abs": near["start_abs"]} if near else None,
            "text": near["text"] if near else "",
            "label": near["label"] if near else "",
        })
    return marks


# --- поиск --------------------------------------------------------------------

def search(conn, query, limit=50):
    """Реплики, где встречаются леммы запроса. Свежие сессии первыми."""
    match = textnorm.fts_query(query)
    if not match:
        return []
    rows = conn.execute(
        "SELECT f.device_id, f.session, f.part, f.idx, f.text"
        " FROM transcript_fts f WHERE f.lemmas MATCH ?"
        " ORDER BY f.session DESC, f.part, f.idx LIMIT ?",
        (match, limit),
    ).fetchall()

    results, cache = [], {}
    for row in rows:
        key = (row["device_id"], row["session"])
        if key not in cache:
            timeline, _ = session_timeline(conn, *key)
            cache[key] = {(u["part"], u["idx"]): u for u in timeline}
        hit = cache[key].get((row["part"], row["idx"]))
        if hit:
            results.append({"device_id": row["device_id"], "session": row["session"], **hit})
    return results


def search_segments(conn, query, limit=50):
    """Попадания поиска с готовой разбивкой текста на совпавшие куски."""
    query_lemmas = {textnorm.lemma(token) for token in textnorm.tokens(query)}
    hits = search(conn, query, limit)
    for hit in hits:
        hit["segments"] = textnorm.segments(hit["text"], query_lemmas)
    return hits


# --- саммари -------------------------------------------------------------------

def claim_session_for_summary(conn):
    """Сессия, готовая к саммари: все части расшифрованы или окончательно
    провалены, новых частей давно не было, саммари нет или оно устарело."""
    conn.execute(
        "INSERT OR IGNORE INTO summaries (device_id, session)"
        " SELECT DISTINCT device_id, session FROM parts"
    )
    row = conn.execute(
        "SELECT s.device_id, s.session, s.parts_covered, s.attempts,"
        "       COUNT(t.part) AS ready_parts"
        " FROM summaries s"
        " JOIN transcripts t ON t.device_id = s.device_id AND t.session = s.session"
        " WHERE s.next_try_at <= datetime('now')"
        "   AND NOT EXISTS (SELECT 1 FROM transcripts u"
        "                   WHERE u.device_id = s.device_id AND u.session = s.session"
        "                     AND u.status = 'pending')"
        "   AND NOT EXISTS (SELECT 1 FROM parts p"
        "                   WHERE p.device_id = s.device_id AND p.session = s.session"
        f"                    AND p.received_at > datetime('now', '-{SESSION_QUIET_MINUTES} minutes'))"
        " GROUP BY s.device_id, s.session"
        " HAVING ready_parts > s.parts_covered"
        " ORDER BY s.session LIMIT 1"
    ).fetchone()
    conn.commit()
    return row


def save_summary(conn, device_id, session, result_json, parts_covered):
    conn.execute(
        "UPDATE summaries SET status='done', result=?, parts_covered=?,"
        " last_error=NULL, updated_at=datetime('now')"
        " WHERE device_id=? AND session=?",
        (result_json, parts_covered, device_id, session),
    )
    conn.commit()


def fail_summary(conn, device_id, session, error, retry_in_minutes):
    conn.execute(
        "UPDATE summaries SET last_error=?, attempts=attempts+1,"
        " next_try_at=datetime('now', ?), updated_at=datetime('now')"
        " WHERE device_id=? AND session=?",
        (error, f"+{retry_in_minutes} minutes", device_id, session),
    )
    conn.commit()


# --- задачи, вопросы, проекты ---------------------------------------------------

SOURCED_LISTS = ("decisions", "facts", "tasks", "open_questions")


def item_id(item):
    """Устойчивый идентификатор пункта саммари.

    Считается из цитаты и текста без морфологии: идентификатор переживает
    пересборку саммари и не меняется от того, установлен ли pymorphy3.
    """
    base = textnorm.normalize(item.get("quote")) + "|" + textnorm.normalize(item.get("text"))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def done_summaries(conn):
    """[(device_id, session, результат)] по готовым саммари, свежие первыми."""
    out = []
    for row in conn.execute(
        "SELECT device_id, session, result FROM summaries"
        " WHERE status='done' AND result IS NOT NULL ORDER BY session DESC"
    ).fetchall():
        try:
            out.append((row["device_id"], row["session"], json.loads(row["result"])))
        except json.JSONDecodeError:
            continue
    return out


def session_days(conn):
    """{(устройство, сессия): дата разговора}.

    Дата берётся из времени выгрузки первой части: часы диктофона пока не
    синхронизированы, настоящего времени записи в данных нет.
    """
    days = {}
    for row in conn.execute(
        "SELECT device_id, session, MIN(received_at) AS first FROM parts"
        " GROUP BY device_id, session"
    ).fetchall():
        if row["first"]:
            days[(row["device_id"], row["session"])] = date.fromisoformat(row["first"][:10])
    return days


def session_day(conn, device_id, session):
    row = conn.execute(
        "SELECT MIN(received_at) AS first FROM parts WHERE device_id=? AND session=?",
        (device_id, session),
    ).fetchone()
    return date.fromisoformat(row["first"][:10]) if row and row["first"] else date.today()


def sync_tasks(conn, device_id, session, tasks):
    """Переносит задачи саммари в таблицу, сохраняя ручной статус.

    Задача, пропавшая из пересобранного саммари, остаётся видимой с пометкой:
    молча убрать то, что владелец уже читал, — потеря.
    """
    day = session_day(conn, device_id, session)
    seen = []
    for item in tasks or []:
        task_id = item_id(item)
        seen.append(task_id)
        due_said = (item.get("due") or "").strip()
        due = duedate.parse_due(due_said, day)
        conn.execute(
            "INSERT INTO tasks (device_id, session, task_id, text, owner, due_said,"
            " due_date, source, vanished) VALUES (?,?,?,?,?,?,?,?,0)"
            " ON CONFLICT(device_id, session, task_id) DO UPDATE SET"
            "   text=excluded.text, owner=excluded.owner, due_said=excluded.due_said,"
            "   due_date=excluded.due_date, source=excluded.source, vanished=0",
            (device_id, session, task_id, (item.get("text") or "").strip(),
             (item.get("owner") or "").strip(), due_said,
             due.isoformat() if due else None,
             json.dumps(item.get("source"), ensure_ascii=False)),
        )
    marks = ",".join("?" for _ in seen) or "''"
    conn.execute(
        "UPDATE tasks SET vanished=1 WHERE device_id=? AND session=?"
        " AND task_id NOT IN (" + marks + ")",
        (device_id, session, *seen),
    )
    conn.commit()


def my_names(conn):
    """Нормализованные имена, отмеченные «это я». Пусто, пока владелец не отметил."""
    return {
        textnorm.normalize(row["name"])
        for row in conn.execute(
            "SELECT name FROM speaker_names WHERE is_me=1 AND name != ''").fetchall()
        if row["name"].strip()
    }


def task_rows(conn, only_open=True, mine=False, overdue=False, session=None):
    """Задачи с признаками «моя» и «просрочена». Свежие сессии первыми."""
    sql = "SELECT * FROM tasks WHERE 1=1"
    args = []
    if only_open:
        sql += " AND status='open'"
    if session is not None:
        sql += " AND session=?"
        args.append(session)
    sql += " ORDER BY session DESC, created_at"

    mine_set = my_names(conn)
    today = date.today().isoformat()
    rows = []
    for row in conn.execute(sql, args).fetchall():
        item = dict(row)
        item["mine"] = bool(mine_set) and textnorm.normalize(item["owner"]) in mine_set
        item["overdue"] = bool(item["due_date"]) and item["due_date"] < today \
            and item["status"] == "open"
        if mine and not item["mine"]:
            continue
        if overdue and not item["overdue"]:
            continue
        rows.append(item)
    return rows


def set_task_status(conn, device_id, session, task_id, status):
    if status not in ("open", "done", "dismissed"):
        return
    conn.execute(
        "UPDATE tasks SET status=?, closed_at=CASE WHEN ?='open' THEN NULL"
        " ELSE datetime('now') END WHERE device_id=? AND session=? AND task_id=?",
        (status, status, device_id, session, task_id),
    )
    conn.commit()


def question_rows(conn, only_open=True):
    """Вопросы без ответа из всех саммари с ручной пометкой владельца."""
    states = {
        (row["device_id"], row["session"], row["question_id"]): row["state"]
        for row in conn.execute("SELECT * FROM question_states").fetchall()
    }
    days = session_days(conn)
    rows = []
    for device_id, session, result in done_summaries(conn):
        for item in result.get("open_questions") or []:
            question_id = item_id(item)
            state = states.get((device_id, session, question_id), "open")
            if only_open and state != "open":
                continue
            rows.append({
                "device_id": device_id, "session": session, "question_id": question_id,
                "state": state, "text": item.get("text") or "",
                "time": item.get("time") or "", "source": item.get("source"),
                "day": days.get((device_id, session)),
            })
    return rows


def set_question_state(conn, device_id, session, question_id, state):
    if state not in ("open", "answered", "dropped"):
        return
    conn.execute(
        "INSERT INTO question_states (device_id, session, question_id, state)"
        " VALUES (?,?,?,?) ON CONFLICT(device_id, session, question_id)"
        " DO UPDATE SET state=excluded.state, updated_at=datetime('now')",
        (device_id, session, question_id, state),
    )
    conn.commit()


def alias_map(conn):
    return {row["alias"]: row["canonical"]
            for row in conn.execute("SELECT alias, canonical FROM project_aliases").fetchall()}


def set_alias(conn, alias, canonical):
    alias, canonical = textnorm.normalize(alias), canonical.strip()
    if not alias or not canonical:
        return
    conn.execute(
        "INSERT INTO project_aliases (alias, canonical) VALUES (?,?)"
        " ON CONFLICT(alias) DO UPDATE SET canonical=excluded.canonical",
        (alias, canonical),
    )
    conn.commit()


def canonical_topic(aliases, topic):
    """Название проекта после сведения псевдонимов. Без псевдонима — как сказано."""
    return aliases.get(textnorm.normalize(topic), topic.strip())


def project_sessions(conn):
    """{проект: [сессии по порядку разговора]}. Порядок — по дате разговора."""
    aliases = alias_map(conn)
    days = session_days(conn)
    projects = {}
    for device_id, session, result in done_summaries(conn):
        for topic in result.get("topics") or []:
            if not str(topic).strip():
                continue
            name = canonical_topic(aliases, str(topic))
            projects.setdefault(name, []).append({
                "device_id": device_id, "session": session,
                "day": days.get((device_id, session)), "result": result,
            })
    for items in projects.values():
        items.sort(key=lambda i: (i["day"] or date.min, i["session"]))
    return projects


# --- спорные места ---------------------------------------------------------------

def uncertain_numbers(conn, device_id, session, threshold=None):
    """{(часть, реплика): числа, распознанные ниже порога уверенности}.

    Уверенность слова начали сохранять в этом этапе: у старых расшифровок её
    нет, и там ничего не помечается. Молчать честнее, чем выдумывать флаг.
    """
    threshold = MIN_WORD_CONFIDENCE if threshold is None else threshold
    by_part = {}
    for row in conn.execute(
        "SELECT part, words FROM transcripts"
        " WHERE device_id=? AND session=? AND status='done' AND words IS NOT NULL",
        (device_id, session),
    ).fetchall():
        try:
            by_part[row["part"]] = json.loads(row["words"])
        except json.JSONDecodeError:
            continue

    timeline, _ = session_timeline(conn, device_id, session)
    hits = {}
    for u in timeline:
        shaky = [
            str(word.get("w"))
            for word in by_part.get(u["part"]) or []
            if word.get("c") is not None and float(word["c"]) < threshold
            and any(ch.isdigit() for ch in str(word.get("w") or ""))
            and u["start"] <= float(word.get("s") or 0.0) <= u["end"]
        ]
        if shaky:
            hits[(u["part"], u["idx"])] = shaky
    return hits


def review_items(conn):
    """Только то, что требует владельца. Подсказка, не блокировка обработки."""
    days = session_days(conn)
    items = []

    for device_id, session, result in done_summaries(conn):
        numbers = uncertain_numbers(conn, device_id, session)
        for name in SOURCED_LISTS:
            for item in result.get(name) or []:
                source = item.get("source")
                if not source or not source.get("confirmed"):
                    items.append({
                        "kind": "источник не подтверждён",
                        "device_id": device_id, "session": session,
                        "day": days.get((device_id, session)),
                        "text": item.get("text") or "",
                        "detail": f"цитата: {item.get('quote') or '—'}",
                        "source": source,
                    })
                if name == "facts" and source:
                    shaky = numbers.get((source.get("part"), source.get("idx")))
                    if shaky:
                        items.append({
                            "kind": "число распознано неуверенно",
                            "device_id": device_id, "session": session,
                            "day": days.get((device_id, session)),
                            "text": item.get("text") or "",
                            "detail": "в реплике: " + ", ".join(shaky),
                            "source": source,
                        })

    for task in task_rows(conn, only_open=True):
        source = json.loads(task["source"]) if task["source"] else None
        common = {"device_id": task["device_id"], "session": task["session"],
                  "day": days.get((task["device_id"], task["session"])),
                  "text": task["text"], "source": source}
        if not task["owner"]:
            items.append({"kind": "задача без исполнителя",
                          "detail": "исполнитель не прозвучал", **common})
        if task["due_said"] and not task["due_date"]:
            items.append({"kind": "срок не разобран",
                          "detail": f"сказано: {task['due_said']}", **common})

    for task in conn.execute(
        "SELECT * FROM tasks WHERE vanished=1 AND status='open'").fetchall():
        items.append({
            "kind": "исчезла из саммари", "device_id": task["device_id"],
            "session": task["session"], "day": days.get((task["device_id"], task["session"])),
            "text": task["text"], "detail": "была в прошлой версии саммари",
            "source": json.loads(task["source"]) if task["source"] else None,
        })

    items.sort(key=lambda i: (-(i["session"] or 0), i["kind"]))
    return items


def session_rows(conn, limit=None):
    """Список сессий для интерфейса: свежие первыми.

    SQL жил в странице списка; вынесен сюда, потому что тот же список нужен
    сводке. Дублировать запрос в двух местах — способ получить две правды.
    """
    sql = (
        "SELECT p.device_id, p.session, COUNT(*) AS parts, SUM(p.bytes) AS bytes,"
        # Клэмп по части, а не по сумме: часть короче заголовка (битая, обрезанная)
        # иначе съедала бы чужие байты у соседней части. part_offsets (шкала
        # реплик) считает так же поэлементно — список сессий обязан совпадать.
        "       SUM(MAX(p.bytes - ?, 0)) AS clamped_bytes,"
        "       MIN(p.received_at) AS first_at, MAX(p.received_at) AS last_at,"
        "       (SELECT status FROM summaries s"
        "         WHERE s.device_id=p.device_id AND s.session=p.session) AS summary_status,"
        # Первая тема саммари — имя разговора в списке: «Смета по офису»
        # читается, «сессия 0042» нет. Достаём через json_extract, а не тянем
        # весь result: саммари весит килобайты, а нужна одна строка.
        "       (SELECT json_extract(s.result, '$.topics[0]') FROM summaries s"
        "         WHERE s.device_id=p.device_id AND s.session=p.session) AS topic,"
        "       (SELECT COUNT(*) FROM transcripts t"
        "         WHERE t.device_id=p.device_id AND t.session=p.session"
        "           AND t.status='done') AS done_parts"
        " FROM parts p GROUP BY p.device_id, p.session"
        " ORDER BY p.received_at DESC"
    )
    args = [WAV_HEADER_BYTES]
    if limit is not None:
        sql += " LIMIT ?"
        args.append(int(limit))
    marks = mark_offsets(conn)
    # 60 столбиков: больше не разглядеть ни в строке списка, ни в блоке ленты.
    envelopes = session_envelopes(conn, 60)
    rows = []
    for row in conn.execute(sql, args).fetchall():
        item = dict(row)
        item["day"] = item["first_at"][:10] if item["first_at"] else None
        item["seconds"] = (item.pop("clamped_bytes") or 0) / BYTES_PER_SECOND
        item["envelope"] = envelopes.get((item["device_id"], item["session"]), [])
        # Метки отдаются секундами от начала сессии: списку нужно их число,
        # ленте дня — засечки на своих местах.
        item["marks"] = marks.get((item["device_id"], item["session"]), [])
        rows.append(item)
    return rows


# --- дайджест ---------------------------------------------------------------------

def digest(conn, days):
    """Числа за последние N суток. Только запросы к базе, без вызова модели."""
    window = f"-{days} days"
    one = lambda sql, *args: conn.execute(sql, args).fetchone()[0]

    recorded = one(
        "SELECT COALESCE(SUM(MAX(bytes - ?, 0)), 0) FROM parts"
        " WHERE received_at >= datetime('now', ?)", WAV_HEADER_BYTES, window)
    tasks = task_rows(conn, only_open=True)
    return {
        "days": days,
        "minutes": recorded / BYTES_PER_SECOND / 60.0,
        "sessions": one(
            "SELECT COUNT(*) FROM (SELECT 1 FROM parts"
            " WHERE received_at >= datetime('now', ?)"
            " GROUP BY device_id, session)", window),
        "new_tasks": one(
            "SELECT COUNT(*) FROM tasks WHERE created_at >= datetime('now', ?)", window),
        "my_open_tasks": sum(1 for t in tasks if t["mine"]),
        "open_tasks": len(tasks),
        "overdue_tasks": sum(1 for t in tasks if t["overdue"]),
        "open_questions": len(question_rows(conn)),
        "review": len(review_items(conn)),
        "failed_parts": one("SELECT COUNT(*) FROM transcripts WHERE status='failed'"),
        "failed_summaries": one(
            "SELECT COUNT(*) FROM summaries WHERE status!='done' AND last_error IS NOT NULL"),
    }


# --- состояние обработки -------------------------------------------------------

def processing_status(conn):
    """Всё, что нужно странице /status, одним словарём."""
    one = lambda sql, *args: conn.execute(sql, args).fetchone()[0]

    status = {
        "transcripts_pending": one(
            "SELECT COUNT(*) FROM parts p LEFT JOIN transcripts t"
            " ON t.device_id=p.device_id AND t.session=p.session AND t.part=p.part"
            " WHERE t.status IS NULL OR t.status='pending'"),
        "summaries_pending": one(
            "SELECT COUNT(*) FROM summaries WHERE status!='done'"),
        "deepgram_minutes": (one(
            "SELECT COALESCE(SUM(duration_sec),0) FROM transcripts WHERE status='done'")
            or 0) / 60.0,
        "failed_parts": [dict(r) for r in conn.execute(
            "SELECT device_id, session, part, last_error, attempts FROM transcripts"
            " WHERE status='failed' ORDER BY session, part").fetchall()],
        "stuck_summaries": [dict(r) for r in conn.execute(
            "SELECT device_id, session, last_error, attempts FROM summaries"
            " WHERE status!='done' AND last_error IS NOT NULL ORDER BY session").fetchall()],
        "recovered_parts": [dict(r) for r in conn.execute(
            "SELECT device_id, session, part FROM parts WHERE recovered=1"
            " ORDER BY session, part").fetchall()],
        "last_upload": [dict(r) for r in conn.execute(
            "SELECT device_id, MAX(received_at) AS at, COUNT(*) AS parts FROM parts"
            " GROUP BY device_id").fetchall()],
        "gaps": [],
        "duration_mismatch": [],
    }

    # Пропуск номера части: запись между ними потеряна или ещё не выгружена.
    for row in conn.execute(
        "SELECT device_id, session, GROUP_CONCAT(part) AS parts FROM parts"
        " GROUP BY device_id, session"
    ).fetchall():
        have = sorted(int(p) for p in row["parts"].split(","))
        missing = [p for p in range(1, have[-1] + 1) if p not in have]
        if missing:
            status["gaps"].append({"device_id": row["device_id"],
                                   "session": row["session"], "missing": missing})

    # Deepgram насчитал заметно другую длительность, чем размер файла: часть
    # аудио могла не обработаться или файл битый.
    for row in conn.execute(
        "SELECT p.device_id, p.session, p.part, p.bytes, t.duration_sec"
        " FROM parts p JOIN transcripts t"
        "   ON t.device_id=p.device_id AND t.session=p.session AND t.part=p.part"
        " WHERE t.status='done' AND t.duration_sec > 0"
    ).fetchall():
        by_size = max(0, row["bytes"] - WAV_HEADER_BYTES) / BYTES_PER_SECOND
        if by_size and abs(by_size - row["duration_sec"]) / by_size > 0.02:
            status["duration_mismatch"].append({
                "device_id": row["device_id"], "session": row["session"],
                "part": row["part"], "by_size": by_size,
                "by_deepgram": row["duration_sec"],
            })
    return status
