"""Схема и доступ к SQLite. Одна база на владельца, файлы аудио лежат рядом.

Очередь намеренно не хранится отдельной таблицей задач: работа выводится из
состояния строк. Зависшее задание невозможно, потому что задания как объекта
не существует — есть часть без транскрипта и сессия без свежего саммари.
"""
import hashlib
import json
import re
import os
import secrets
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

# Формат прототипа на XIAO Sense: 16 кГц, 16 бит, моно. Остаётся запасным
# значением для частей, принятых до появления колонки bytes_per_second, и для
# файлов с нечитаемым заголовком: все они моно, других диктофон не слал.
# Настоящий множитель части лежит в parts.bytes_per_second (wavfmt.py).
BYTES_PER_SECOND = 16000 * 2
WAV_HEADER_BYTES = 44


def part_seconds(part_bytes, bytes_per_second=None):
    """Длительность части: её размер, делённый на её собственный формат.

    Клэмп по части, а не по сумме: часть короче заголовка (битая, обрезанная)
    иначе съедала бы чужие байты у соседней. NULL в колонке — часть принята
    до появления колонки, то есть моно.
    """
    audio = max(0, (part_bytes or 0) - WAV_HEADER_BYTES)
    return audio / (bytes_per_second or BYTES_PER_SECOND)

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
    -- Байт PCM в секунде звука у ЭТОЙ части: 32000 у моно с XIAO Sense,
    -- 64000 у стерео с Recorder RevA. Читается из заголовка файла при приёмке
    -- (wavfmt.py). NULL — часть принята до появления колонки или заголовок не
    -- прочитался; тогда считаем моно, потому что других частей и не было.
    bytes_per_second INTEGER,
    -- Сколько частей диктофон закрыл в этой сессии при STOP (X-Session-Parts).
    -- NULL — не объявлено: часть восстановлена после сбоя или прошивка старая.
    -- Когда пришло столько частей, саммари не ждёт тишины.
    expected_parts INTEGER,
    -- Перевод принятого WAV в FLAC без потерь (archive.py). pending | done |
    -- failed. bytes и crc32 остаются от WAV: это контракт приёмки, по ним же
    -- считается шкала времени, и переписывать их под сжатый файл нельзя.
    archive_status TEXT NOT NULL DEFAULT 'pending',
    archive_attempts INTEGER NOT NULL DEFAULT 0,
    archive_error TEXT,
    archive_next_try_at TEXT NOT NULL DEFAULT (datetime('now')),
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

-- Ручные имена для номеров диаризации. Исходный номер в транскрипте не
-- меняется. is_me пока ничего не считает: он накапливает размеченные образцы
-- голоса для будущего определения своего голоса.
--
-- Ключ включает часть намеренно. Deepgram обрабатывает каждую часть отдельным
-- запросом, поэтому «говорящий 0» в части 1 и «говорящий 0» в части 2 — это
-- два разных номера, а не один человек. Считать их одним человеком значит
-- приписать слова тому, кто их не говорил.
--
-- auto=1 — имя поставил сервер по сходству голоса (voiceprint.py), а не
-- владелец. Такую строку сервер переписывает и удаляет сам; строку владельца
-- (auto=0) — никогда.
CREATE TABLE IF NOT EXISTS speaker_names (
    device_id TEXT    NOT NULL,
    session   INTEGER NOT NULL,
    part      INTEGER NOT NULL,
    speaker   INTEGER NOT NULL,
    name      TEXT    NOT NULL DEFAULT '',
    is_me     INTEGER NOT NULL DEFAULT 0,
    auto      INTEGER NOT NULL DEFAULT 0,
    -- Версия расшифровки части (transcripts.updated_at), под которую имя
    -- поставлено. После повторной расшифровки номера другие: по голосу
    -- переезжают только имена старой версии, а то, что владелец вписал уже
    -- под новую, остаётся на месте.
    transcript_at TEXT,
    PRIMARY KEY (device_id, session, part, speaker)
);

-- Отпечаток голоса номера говорящего: 192 числа float32 от модели CAM++.
-- Биометрия: удаляется вместе с сессией (SESSION_TABLES).
CREATE TABLE IF NOT EXISTS voice_prints (
    device_id TEXT    NOT NULL,
    session   INTEGER NOT NULL,
    part      INTEGER NOT NULL,
    speaker   INTEGER NOT NULL,
    seconds   REAL    NOT NULL,
    vec       BLOB    NOT NULL,
    -- Версия расшифровки, с реплик которой снят отпечаток. Не совпадает с
    -- текущей — часть расшифрована заново, номера уже чужие: такой отпечаток
    -- не участвует ни в профилях, ни в подписях, пока его не снимут заново
    -- (воркер или --backfill). Сравнение на равенство, а не по времени: часы
    -- и значения по умолчанию при миграции здесь ни при чём.
    transcript_at TEXT,
    PRIMARY KEY (device_id, session, part, speaker)
);

-- Отпечаток голоса одной фразы (реплики длиннее 2 с). Gemini иногда кладёт
-- двух людей под один номер: имя номера тогда стоит и на чужих фразах.
-- name — человек, чей голос у фразы явно другой, чем у имени её номера;
-- NULL — фраза подписана как её номер. Версия расшифровки — как у
-- voice_prints. Биометрия: удаляется вместе с сессией.
CREATE TABLE IF NOT EXISTS utterance_prints (
    device_id     TEXT    NOT NULL,
    session       INTEGER NOT NULL,
    part          INTEGER NOT NULL,
    idx           INTEGER NOT NULL,
    speaker       INTEGER NOT NULL,
    vec           BLOB    NOT NULL,
    transcript_at TEXT,
    name          TEXT,
    PRIMARY KEY (device_id, session, part, idx)
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

-- Что диктофон рассказал о себе при последней выгрузке: заряд и версия
-- прошивки. Одна строка на устройство, перезаписывается. Прошивка шлёт это
-- заголовками и может не слать вовсе — тогда NULL, и интерфейс честно
-- показывает «нет данных», а не выдуманный процент.
CREATE TABLE IF NOT EXISTS device_state (
    device_id  TEXT PRIMARY KEY,
    battery    INTEGER,
    firmware   TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Настоящий день записи, если владелец его уточнил. По умолчанию днём
-- разговора считается день выгрузки первой части: часы диктофона не
-- синхронизированы, и другого источника даты в данных нет. Записал в
-- понедельник, выгрузил в среду — «сделаю завтра» уехало на четверг, а от
-- дня разговора считаются все сроки задач. Строка появляется только после
-- правки руками: пустая таблица означает «дату не уточняли».
CREATE TABLE IF NOT EXISTS recording_days (
    device_id TEXT    NOT NULL,
    session   INTEGER NOT NULL,
    day       TEXT    NOT NULL,          -- YYYY-MM-DD
    PRIMARY KEY (device_id, session)
);

-- «Пози флора» и «Posiflora» — один проект. Ключ нормализован, отображаемое
-- имя проекта хранится как написал владелец.
CREATE TABLE IF NOT EXISTS project_aliases (
    alias     TEXT PRIMARY KEY,
    canonical TEXT NOT NULL
);

-- Ссылки на одну запись для другого человека (share.py). Закрыть доступ —
-- удалить строку. Истёкшие отсекает сравнение с datetime('now') в каждом
-- запросе, фоновой уборки нет: строка на выданную ссылку — не тот мусор.
CREATE TABLE IF NOT EXISTS shares (
    token      TEXT PRIMARY KEY,
    device_id  TEXT NOT NULL,
    session    INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
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


# CREATE TABLE IF NOT EXISTS не добавляет колонки в уже существующую таблицу.
# У частей, принятых до появления колонки, останется NULL — интерфейс и
# очередь это переживают.
_PART_COLUMNS = (
    ("envelope", "TEXT"),
    ("expected_parts", "INTEGER"),
    # Старым частям остаётся NULL, и это верно: до Recorder RevA диктофон
    # писал только моно, а NULL и означает «моно».
    ("bytes_per_second", "INTEGER"),
    # Части, принятые до появления архива, становятся кандидатами на сжатие:
    # значение по умолчанию у них такое же, как у новых. datetime('now') в
    # ALTER TABLE не примут: значение по умолчанию для добавляемой колонки
    # обязано быть константой.
    ("archive_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("archive_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("archive_error", "TEXT"),
    ("archive_next_try_at", "TEXT NOT NULL DEFAULT '1970-01-01 00:00:00'"),
)


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrations_needed(conn):
    parts, speakers = _columns(conn, "parts"), _columns(conn, "speaker_names")
    return (any(name not in parts for name, _ in _PART_COLUMNS)
            or not {"part", "auto", "transcript_at"} <= speakers
            or "transcript_at" not in _columns(conn, "voice_prints"))


def _migrate(conn):
    """Переделки старой базы. Каждая сама проверяет, нужна ли она: вызывается
    под блокировкой, а другая служба могла успеть всё сделать раньше."""
    parts = _columns(conn, "parts")
    for name, kind in _PART_COLUMNS:
        if name not in parts:
            conn.execute(f"ALTER TABLE parts ADD COLUMN {name} {kind}")

    # Имена говорящих переехали с ключа (устройство, сессия, говорящий) на
    # ключ с частью. Ключ в SQLite не меняется на месте, поэтому таблица
    # пересобирается. Каждая старая строка размножается на все части своей
    # сессии: ровно это владелец и видел на странице до переезда, так что
    # разметка не пропадает и смысла себе не меняет.
    if "part" not in _columns(conn, "speaker_names"):
        # Внутри общей транзакции миграции: падение между DROP и RENAME
        # откатится и не оставит владельца без разметки.
        conn.execute(
            "CREATE TABLE speaker_names_new ("
            " device_id TEXT NOT NULL, session INTEGER NOT NULL,"
            " part INTEGER NOT NULL, speaker INTEGER NOT NULL,"
            " name TEXT NOT NULL DEFAULT '', is_me INTEGER NOT NULL DEFAULT 0,"
            " PRIMARY KEY (device_id, session, part, speaker))"
        )
        conn.execute(
            "INSERT OR IGNORE INTO speaker_names_new"
            " (device_id, session, part, speaker, name, is_me)"
            " SELECT s.device_id, s.session, p.part, s.speaker, s.name, s.is_me"
            " FROM speaker_names s JOIN parts p"
            "   ON p.device_id = s.device_id AND p.session = s.session"
        )
        conn.execute("DROP TABLE speaker_names")
        conn.execute("ALTER TABLE speaker_names_new RENAME TO speaker_names")
    # Имена, которые были до узнавания по голосу, поставил владелец.
    if "auto" not in _columns(conn, "speaker_names"):
        conn.execute("ALTER TABLE speaker_names ADD COLUMN auto INTEGER NOT NULL DEFAULT 0")
    # Отпечатки и имена, записанные до появления версии, относятся к текущим
    # расшифровкам.
    for table in ("voice_prints", "speaker_names"):
        if "transcript_at" not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN transcript_at TEXT")
            conn.execute(
                f"UPDATE {table} SET transcript_at = (SELECT t.updated_at FROM transcripts t"
                f" WHERE t.device_id = {table}.device_id AND t.session = {table}.session"
                f" AND t.part = {table}.part)")


def init_storage():
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as conn:
        conn.executescript(SCHEMA)
        # Переделки старой базы — под одной блокировкой и одной транзакцией:
        # сайт и воркер стартуют вместе, и без блокировки второй проверил бы
        # столбцы до того, как первый их добавил, и упал на повторном ALTER.
        # SQLite откатывает DDL, так что падение посреди не оставит полработы.
        # Блокировку берём, только если перестраивать есть что: иначе старт
        # службы ждал бы, пока другая служба пишет.
        if _migrations_needed(conn):
            conn.execute("BEGIN IMMEDIATE")
            _migrate(conn)
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
    cursor = conn.execute(
        "UPDATE transcripts SET status='done', text=?, words=?, utterances=?,"
        " duration_sec=?, last_error=NULL, updated_at=datetime('now')"
        " WHERE device_id=? AND session=? AND part=?",
        (text, words_json, json.dumps(utterances, ensure_ascii=False),
         duration_sec, device_id, session, part),
    )
    if cursor.rowcount == 0:
        # Сессию удалили, пока часть расшифровывалась: в поиск её не возвращаем.
        conn.commit()
        return
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


# --- архив: WAV -> FLAC --------------------------------------------------------

def claim_part_for_archive(conn):
    """Часть, которую пора сжать: аудио ещё в WAV и подошло время попытки.

    Расшифровка идёт первой: она платная, и ждать сжатия не должна. Поэтому
    берутся только части с готовым или окончательно провалившимся транскриптом."""
    return conn.execute(
        "SELECT p.device_id, p.session, p.part, p.audio_path, p.bytes,"
        "       p.archive_attempts"
        " FROM parts p LEFT JOIN transcripts t"
        "   ON t.device_id=p.device_id AND t.session=p.session AND t.part=p.part"
        " WHERE p.archive_status='pending'"
        "   AND p.audio_path LIKE '%.wav'"
        "   AND p.archive_next_try_at <= datetime('now')"
        "   AND t.status IN ('done', 'failed')"
        " ORDER BY p.session, p.part LIMIT 1"
    ).fetchone()


def finish_archive(conn, key, flac_path):
    """Новый путь в базу. Удаление WAV — после этого и только после.

    False — части больше нет: сессию удалили, пока шло сжатие."""
    cursor = conn.execute(
        "UPDATE parts SET audio_path=?, archive_status='done', archive_error=NULL"
        " WHERE device_id=? AND session=? AND part=?",
        (str(flac_path), *key),
    )
    conn.commit()
    return cursor.rowcount > 0


def fail_archive(conn, key, error, retry_in_minutes):
    if retry_in_minutes is None:
        conn.execute(
            "UPDATE parts SET archive_status='failed', archive_error=?,"
            " archive_attempts=archive_attempts+1"
            " WHERE device_id=? AND session=? AND part=?",
            (error, *key),
        )
    else:
        conn.execute(
            "UPDATE parts SET archive_error=?, archive_attempts=archive_attempts+1,"
            " archive_next_try_at=datetime('now', ?)"
            " WHERE device_id=? AND session=? AND part=?",
            (error, f"+{retry_in_minutes} minutes", *key),
        )
    conn.commit()


def stale_wavs(conn):
    """WAV, оставшиеся после краха между записью пути в базу и удалением файла.

    Их держит только упавший процесс: в базе уже лежит проверенный FLAC.
    Если FLAC на диске нет (база и файлы разошлись), WAV — последняя копия
    звука и в список не попадает: плеер его найдёт через audio_file."""
    rows = conn.execute(
        "SELECT audio_path FROM parts WHERE archive_status='done'"
        " AND audio_path LIKE '%.flac'"
    ).fetchall()
    leftovers = []
    for row in rows:
        flac = Path(row["audio_path"])
        wav = flac.with_suffix(".wav")
        if wav.exists() and flac.is_file() and flac.stat().st_size > 0:
            leftovers.append(wav)
    return leftovers


def audio_file(conn, device_id, session, part):
    """Путь к аудио части и его media type. None — такой части нет.

    Если записанного пути нет на диске, пробуем соседа с другим расширением:
    воркер меняет путь и удаляет WAV не одним действием, и запрос плеера может
    прийти ровно между ними."""
    row = conn.execute(
        "SELECT audio_path FROM parts WHERE device_id=? AND session=? AND part=?",
        (device_id, session, part),
    ).fetchone()
    if row is None:
        return None
    path = Path(row["audio_path"])
    if not path.exists():
        for suffix in (".flac", ".wav"):
            other = path.with_suffix(suffix)
            if other.exists():
                path = other
                break
        else:
            return None
    return path, "audio/flac" if path.suffix == ".flac" else "audio/wav"


# --- говорящие ----------------------------------------------------------------

def speaker_names(conn, device_id, session):
    """{(часть, номер): {'name': ..., 'is_me': bool}} для сессии.

    Номер говорящего принадлежит своей части: Deepgram нумерует голоса внутри
    одного запроса, а части уходят к нему поодиночке."""
    rows = conn.execute(
        "SELECT part, speaker, name, is_me, auto FROM speaker_names"
        " WHERE device_id=? AND session=?",
        (device_id, session),
    ).fetchall()
    return {(r["part"], r["speaker"]): {"name": r["name"], "is_me": bool(r["is_me"]),
                                        "auto": bool(r["auto"])}
            for r in rows}


# Отпечаток фразы свежий: снят с текущей версии расшифровки своей части.
_LINE_FRESH = ("EXISTS (SELECT 1 FROM transcripts t WHERE t.device_id = u.device_id"
               " AND t.session = u.session AND t.part = u.part AND t.status = 'done'"
               " AND t.updated_at IS u.transcript_at)")


def session_speakers(conn):
    """{(устройство, сессия): [имена]} — участники разговора для списков.

    Одним запросом на весь архив: списку сессий имя нужно у каждой строки, а
    speaker_names ходит за одной сессией. Безымянные («Говорящий 2») не
    отдаются: в списке они не помогают найти разговор.
    """
    found = {}
    rows = conn.execute(
        "SELECT DISTINCT device_id, session, name, is_me FROM speaker_names"
        " WHERE TRIM(COALESCE(name,'')) != '' ORDER BY is_me DESC, name"
    ).fetchall()
    # Человек, найденный только по голосу своих фраз, — тоже участник.
    rows += conn.execute(
        "SELECT DISTINCT u.device_id, u.session, u.name FROM utterance_prints u"
        " WHERE u.name IS NOT NULL AND " + _LINE_FRESH + " ORDER BY u.name").fetchall()
    for row in rows:
        names = found.setdefault((row["device_id"], row["session"]), [])
        if row["name"] not in names:
            names.append(row["name"])
    return found


# Сессию могут удалить, пока часть расшифровывается или пока сайт сохраняет
# имена. Проверка «сессия ещё есть» стоит в самой вставке, а не перед ней:
# между отдельной проверкой и записью сессию тоже успевают удалить, и в базе
# остались бы отпечатки голоса и имена удалённого разговора.
_SESSION_ALIVE = "EXISTS (SELECT 1 FROM parts WHERE device_id = ? AND session = ?)"
# Текущая версия расшифровки части: подставляется в ту же вставку.
_VERSION = "(SELECT updated_at FROM transcripts WHERE device_id = ? AND session = ? AND part = ?)"
# Версия, которой нет ни у одной строки: «старых отпечатков не было».
_NO_VERSION = object()


def transcript_version(conn, key):
    row = conn.execute("SELECT updated_at FROM transcripts"
                       " WHERE device_id=? AND session=? AND part=?", key).fetchone()
    return row["updated_at"] if row else None


def _drop_guesses(conn, key):
    """Снять подписи по голосу одной части. Имена уходят из текста для модели —
    саммари сессии пересобирается."""
    cursor = conn.execute("DELETE FROM speaker_names"
                          " WHERE device_id=? AND session=? AND part=? AND auto=1", key)
    if cursor.rowcount:
        _summary_stale(conn, key[0], key[1])


def drop_auto_names(conn, key):
    _drop_guesses(conn, key)
    conn.commit()


def part_prints(conn, key):
    """{номер: vec} одной части, в том числе устаревшие: по ним имена владельца
    переезжают на номера новой расшифровки."""
    return {row["speaker"]: row["vec"] for row in conn.execute(
        "SELECT speaker, vec FROM voice_prints WHERE device_id=? AND session=? AND part=?", key)}


def _save_prints(conn, key, prints, version):
    device_id, session, part = key
    conn.execute("DELETE FROM voice_prints WHERE device_id=? AND session=? AND part=?", key)
    _drop_guesses(conn, key)
    conn.executemany(
        "INSERT INTO voice_prints (device_id, session, part, speaker, seconds, vec, transcript_at)"
        " SELECT ?, ?, ?, ?, ?, ?, COALESCE(?, " + _VERSION + ") WHERE " + _SESSION_ALIVE,
        [(device_id, session, part, speaker, round(float(seconds), 1), blob, version,
          *key, device_id, session)
         for speaker, (blob, seconds) in prints.items()],
    )


def save_voice_prints(conn, key, prints, version=None):
    """Отпечатки части заменяются целиком: после повторной расшифровки номера
    говорящих другие, и подписи по голосу этой части уходят вместе со старыми
    отпечатками. Имена владельца остаются. prints: {номер: (vec, секунд)};
    version — версия расшифровки, с которой сняты отпечатки (по умолчанию
    текущая)."""
    _save_prints(conn, key, prints, version)
    conn.commit()


def add_voice_prints(conn, key, prints, version):
    """Дописать отпечатки номерам части, у которых их не было, — к уже снятым
    той же версии расшифровки. Ни старые отпечатки, ни имена не трогаются.
    Если часть успели расшифровать заново, ничего не пишется: версия не та."""
    device_id, session, part = key
    conn.executemany(
        "INSERT OR IGNORE INTO voice_prints"
        " (device_id, session, part, speaker, seconds, vec, transcript_at)"
        " SELECT ?, ?, ?, ?, ?, ?, ? WHERE " + _SESSION_ALIVE +
        " AND " + _VERSION + " IS ?",
        [(device_id, session, part, speaker, round(float(seconds), 1), blob, version,
          device_id, session, *key, version)
         for speaker, (blob, seconds) in prints.items()],
    )
    conn.commit()


def has_line_prints(conn, key, version):
    return conn.execute(
        "SELECT 1 FROM utterance_prints WHERE device_id=? AND session=? AND part=?"
        " AND transcript_at IS ? LIMIT 1", (*key, version)).fetchone() is not None


def save_line_prints(conn, key, lines, version):
    """Отпечатки фраз части — целиком, одной транзакцией. lines: {индекс
    реплики: (номер, vec)}. Пишется, только если version — текущая версия
    расшифровки: отпечатки снятой заново части уже не про её реплики."""
    device_id, session, part = key
    conn.execute("BEGIN IMMEDIATE")
    try:
        if transcript_version(conn, key) != version:
            conn.rollback()
            return
        named = conn.execute(
            "SELECT 1 FROM utterance_prints WHERE device_id=? AND session=? AND part=?"
            " AND name IS NOT NULL LIMIT 1", key).fetchone()
        conn.execute("DELETE FROM utterance_prints WHERE device_id=? AND session=? AND part=?",
                     key)
        conn.executemany(
            "INSERT INTO utterance_prints"
            " (device_id, session, part, idx, speaker, vec, transcript_at)"
            " SELECT ?, ?, ?, ?, ?, ?, ? WHERE " + _SESSION_ALIVE,
            [(device_id, session, part, idx, speaker, blob, version, device_id, session)
             for idx, (speaker, blob) in lines.items()],
        )
        if named:
            _summary_stale(conn, device_id, session)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def line_prints(conn, device_id, session):
    """Свежие отпечатки фраз сессии: part, idx, speaker, vec, name."""
    return conn.execute(
        "SELECT u.part, u.idx, u.speaker, u.vec, u.name FROM utterance_prints u"
        " WHERE u.device_id=? AND u.session=? AND " + _LINE_FRESH +
        " ORDER BY u.part, u.idx", (device_id, session)).fetchall()


def parts_without_line_prints(conn):
    """Расшифрованные части, у которых нет свежих отпечатков фраз."""
    return conn.execute(
        "SELECT t.device_id, t.session, t.part, t.utterances, t.updated_at, p.audio_path"
        " FROM transcripts t JOIN parts p"
        "   ON p.device_id = t.device_id AND p.session = t.session AND p.part = t.part"
        " WHERE t.status = 'done' AND NOT EXISTS (SELECT 1 FROM utterance_prints u"
        "   WHERE u.device_id = t.device_id AND u.session = t.session AND u.part = t.part"
        "     AND u.transcript_at IS t.updated_at)"
        " ORDER BY t.device_id, t.session, t.part").fetchall()


def set_line_names(conn, device_id, session, names):
    """Имена фраз по голосу. names: {(часть, индекс): имя или None}. Внутри
    транзакции вызывающего. Возвращает число изменённых строк; если что-то
    поменялось, саммари сессии уходит на пересборку."""
    changed = 0
    for (part, idx), name in names.items():
        changed += conn.execute(
            "UPDATE utterance_prints SET name = ?"
            " WHERE device_id=? AND session=? AND part=? AND idx=? AND name IS NOT ?",
            (name, device_id, session, part, idx, name)).rowcount
    if changed:
        _summary_stale(conn, device_id, session)
    return changed


def line_names(conn, device_id, session):
    """{(часть, индекс): имя} — фразы, которые голос подписал иначе, чем номер."""
    return {(row["part"], row["idx"]): row["name"] for row in conn.execute(
        "SELECT u.part, u.idx, u.name FROM utterance_prints u"
        " WHERE u.device_id=? AND u.session=? AND u.name IS NOT NULL AND " + _LINE_FRESH,
        (device_id, session))}


def _move_names(conn, key, moves, old_version, new_version):
    device_id, session, part = key
    keep = {new_version, transcript_version(conn, key)} - {None} or {None}
    owners = conn.execute(
        "SELECT speaker, name, is_me, transcript_at FROM speaker_names"
        " WHERE device_id=? AND session=? AND part=? AND auto=0", key).fetchall()
    # Старая версия — переезжает по голосу. Новая (и та, что сейчас в базе, если
    # её успели сохранить, пока снимали отпечатки) — вписана под новую
    # нумерацию, остаётся. Любая другая — промежуточная расшифровка, с которой
    # отпечатков нет: номер её не перенести, а оставить нельзя — под ним уже
    # другой голос, и он ушёл бы в чужой профиль.
    moving = [row for row in owners if row["transcript_at"] == old_version]
    dropped = [row for row in owners
               if row["transcript_at"] == old_version or row["transcript_at"] not in keep]
    for row in dropped:
            conn.execute("DELETE FROM speaker_names WHERE device_id=? AND session=?"
                         " AND part=? AND speaker=? AND auto=0", (*key, row["speaker"]))
    for row in moving:
        target = moves.get(row["speaker"])
        if target is None:
            continue
        conn.execute(
            "INSERT INTO speaker_names"
            " (device_id, session, part, speaker, name, is_me, auto, transcript_at)"
            " SELECT ?, ?, ?, ?, ?, ?, 0, COALESCE(?, " + _VERSION + ") WHERE " + _SESSION_ALIVE +
            " ON CONFLICT(device_id, session, part, speaker) DO UPDATE SET"
            " name=excluded.name, is_me=excluded.is_me, auto=0,"
            " transcript_at=excluded.transcript_at"
            " WHERE speaker_names.auto = 1",
            (device_id, session, part, target, row["name"], row["is_me"], new_version,
             *key, device_id, session),
        )
    if dropped:
        _summary_stale(conn, device_id, session)


def move_owner_names(conn, key, moves, old_version, new_version=None):
    """Имена владельца части после повторной расшифровки. moves: {старый
    номер: новый номер или None}; номер без пары по голосу теряет имя.

    Переезжают только имена, поставленные под старую версию расшифровки
    (old_version — версия старых отпечатков). Всё, что владелец вписал уже под
    новую, остаётся на месте, а при столкновении его слово важнее переезжающего
    имени."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        _move_names(conn, key, moves, old_version, new_version)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def replace_part_prints(conn, key, prints, version, mapper):
    """Новые отпечатки части и переезд имён владельца — одной транзакцией:
    иначе сбой между ними оставил бы свежие отпечатки с именами на чужих
    номерах, а --backfill такую часть уже не перепроверил бы. mapper получает
    старые отпечатки {номер: vec} и возвращает переезды {старый: новый}."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = transcript_version(conn, key)
        old_version = conn.execute(
            "SELECT transcript_at FROM voice_prints WHERE device_id=? AND session=? AND part=?"
            " LIMIT 1", key).fetchone()
        if (version is not None and current is not None and version != current
                and old_version is not None and old_version["transcript_at"] == current):
            # Пока снимали отпечатки, часть расшифровали снова, и воркер уже
            # записал отпечатки новой версии: старые поверх них не кладём. Если
            # новых ещё нет — кладём: по ним воркер потом перенесёт имена.
            conn.rollback()
            return
        if old_version is not None and old_version["transcript_at"] == version:
            # Эту версию уже обработали (воркер и --backfill взяли одну часть):
            # второй проход счёл бы имена «старыми» и снял бы те, у кого нет
            # отпечатка.
            conn.rollback()
            return
        old = part_prints(conn, key)
        moves = mapper(old) if old else {}
        if version is not None and current is not None and version != current:
            # Запоздалый --backfill прежней версии: текущую воркер обработает
            # сам, прямо со старых отпечатков. Писать стоит, только если есть
            # имена, вписанные под эту прежнюю версию, — кроме неё их никто не
            # перенесёт. Иначе запоздалый проход только навредил бы: снял бы
            # имена людей, у которых в его версии нет отпечатка.
            owned = conn.execute(
                "SELECT 1 FROM speaker_names WHERE device_id=? AND session=? AND part=?"
                " AND auto=0 AND transcript_at IS ? LIMIT 1", (*key, version)).fetchone()
            targets = {moves.get(row["speaker"]) for row in conn.execute(
                "SELECT speaker FROM speaker_names WHERE device_id=? AND session=? AND part=?"
                " AND auto=0 AND transcript_at IS ?",
                (*key, old_version["transcript_at"] if old_version else None))} if old else set()
            held = {row["speaker"] for row in conn.execute(
                "SELECT speaker FROM speaker_names WHERE device_id=? AND session=? AND part=?"
                " AND auto=0 AND transcript_at IS ?", (*key, current))}
            # Перенос лёг бы на номер, названный владельцем под текущую версию, —
            # тоже не пишем. Отпечатки на часть одни, поэтому здесь одно имя
            # теряется при любом порядке: теряются имена промежуточной версии
            # (воркер их снимет), а старые и текущие остаются.
            if not owned or (targets - {None}) & held:
                conn.rollback()
                return
        _save_prints(conn, key, prints, version)
        # Без старых отпечатков переносить не по чему, но имена прежней
        # нумерации всё равно снимаются: под их номерами уже другие голоса.
        _move_names(conn, key, moves,
                    old_version["transcript_at"] if old_version else _NO_VERSION, version)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def owner_display_name(conn):
    """Как владелец пишет своё имя: самое частое имя у строк «это я» во всём
    архиве. Пусто, пока не отмечал."""
    row = conn.execute(
        "SELECT name FROM speaker_names WHERE is_me = 1 AND auto = 0 AND TRIM(name) != ''"
        " GROUP BY name ORDER BY COUNT(*) DESC, MIN(rowid) LIMIT 1").fetchone()
    return row["name"] if row else ""


def voice_prints(conn, device_id=None, session=None):
    """Отпечатки и то, как владелец назвал их номера.

    owner_name — имя от владельца ('' — стёр, NULL — не называл или имя
    поставил сервер). has_owner_row — владелец трогал номер, в том числе стёр
    имя: такой номер сервер не подписывает. Устаревшие отпечатки (часть
    расшифрована заново, а снять новые ещё не вышло) не отдаются. С сессией —
    только то, что нужно
    для её подписи: все номера этой сессии и названные владельцем номера
    остальных (из них профили). Без сессии — весь архив."""
    where = (" WHERE NOT EXISTS (SELECT 1 FROM transcripts t"
             "   WHERE t.device_id = v.device_id AND t.session = v.session AND t.part = v.part"
             "     AND (t.status != 'done' OR v.transcript_at IS NOT t.updated_at))")
    args = ()
    if device_id is not None:
        where += (" AND ((s.auto = 0 AND TRIM(s.name) != '')"
                  " OR (v.device_id = ? AND v.session = ?))")
        args = (device_id, session)
    return conn.execute(
        "SELECT v.device_id, v.session, v.part, v.speaker, v.vec,"
        " CASE WHEN s.auto = 0 THEN TRIM(s.name) END AS owner_name,"
        " COALESCE(s.auto = 0, 0) AS has_owner_row"
        " FROM voice_prints v LEFT JOIN speaker_names s"
        "   ON s.device_id = v.device_id AND s.session = v.session"
        "  AND s.part = v.part AND s.speaker = v.speaker"
        + where +
        " ORDER BY v.device_id, v.session, v.part, v.speaker", args
    ).fetchall()


def set_auto_names(conn, device_id, session, names):
    """Имена по голосу. names: {(часть, номер): имя или None — снять подпись}.
    Строки владельца не трогаются. Возвращает число изменённых строк; если
    что-то поменялось, саммари сессии уходит на пересборку."""
    changed = 0
    for (part, speaker), name in names.items():
        key = (device_id, session, part, speaker)
        if name:
            cursor = conn.execute(
                "INSERT INTO speaker_names (device_id, session, part, speaker, name, is_me, auto)"
                " SELECT ?, ?, ?, ?, ?, 0, 1 WHERE " + _SESSION_ALIVE +
                " ON CONFLICT(device_id, session, part, speaker) DO UPDATE SET"
                " name=excluded.name"
                " WHERE speaker_names.auto = 1 AND speaker_names.name != excluded.name",
                (*key, name, device_id, session),
            )
        else:
            cursor = conn.execute(
                "DELETE FROM speaker_names"
                " WHERE device_id=? AND session=? AND part=? AND speaker=? AND auto=1", key)
        changed += cursor.rowcount
    if changed:
        _summary_stale(conn, device_id, session)
    conn.commit()
    return changed


def retry_part(conn, device_id, session, part):
    """Вернуть часть в очередь расшифровки. Успешную не трогаем."""
    cursor = conn.execute(
        "UPDATE transcripts SET status='pending', attempts=0, last_error=NULL,"
        " next_try_at=datetime('now'), updated_at=datetime('now')"
        " WHERE device_id=? AND session=? AND part=? AND status!='done'",
        (device_id, session, part),
    )
    return cursor.rowcount > 0


SESSION_TABLES = ("transcripts", "summaries", "speaker_names", "voice_prints",
                  "utterance_prints", "tasks",
                  "question_states", "marks", "recording_days", "transcript_fts", "shares",
                  "parts")


def delete_session(conn, device_id, session):
    """Стереть сессию целиком: строки во всех таблицах, потом её файлы.

    Сначала база, потом диск: упади удаление файла, останется лишний файл без
    строки, а не строка, ведущая в пустоту. Файлы ищутся и по имени, а не только
    по audio_path: там же лежат WAV рядом с FLAC после краха воркера и .partial
    недокачанной части. Возвращает False, если такой сессии не было.

    Имя устройства в шаблон попадает только из строки parts, а туда — после
    проверки приёмника (буквы, цифры, _ и -): знаков шаблона в нём нет."""
    if not session_exists(conn, device_id, session):
        return False
    for table in SESSION_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE device_id=? AND session=?",
                     (device_id, session))
    conn.commit()
    # Номер части и точка: иначе устройство «A_S0001_P01x» попало бы под
    # шаблон устройства «A».
    for path in AUDIO_DIR.glob(f"{device_id}_S{session:04d}_P[0-9][0-9].*"):
        path.unlink(missing_ok=True)
    return True


def session_exists(conn, device_id, session):
    return conn.execute("SELECT 1 FROM parts WHERE device_id=? AND session=?",
                        (device_id, session)).fetchone() is not None


# Сколько живёт ссылка для гостя. Забытая ссылка не должна открывать запись вечно.
SHARE_DAYS = 7


def create_share(conn, device_id, session):
    """Новая ссылка на запись. Ключ — 128 случайных бит, подобрать нельзя.
    Хранится как есть: владелец копирует живую ссылку ещё раз, а утечка базы
    и без того раскрыла бы сами разговоры."""
    token = secrets.token_urlsafe(16)
    conn.execute(
        "INSERT INTO shares (token, device_id, session, expires_at)"
        " VALUES (?, ?, ?, datetime('now', ?))",
        (token, device_id, session, f"+{SHARE_DAYS} days"),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT token, created_at, expires_at FROM shares WHERE token=?", (token,)).fetchone())


def session_shares(conn, device_id, session):
    """Живые ссылки записи, новые первыми. Истёкшие не показываем: они уже
    ничего не открывают."""
    rows = conn.execute(
        "SELECT token, created_at, expires_at FROM shares"
        " WHERE device_id=? AND session=? AND expires_at > datetime('now')"
        " ORDER BY rowid DESC",
        (device_id, session),
    ).fetchall()
    return [dict(row) for row in rows]


def revoke_share(conn, token):
    conn.execute("DELETE FROM shares WHERE token=?", (token,))
    conn.commit()


def shared_session(conn, token):
    """(устройство, сессия) живой ссылки или None. Неизвестная, истёкшая и
    закрытая ссылка неразличимы: гостю незачем знать, какая из них его."""
    row = conn.execute(
        "SELECT device_id, session FROM shares WHERE token=? AND expires_at > datetime('now')",
        (token,),
    ).fetchone()
    return (row["device_id"], row["session"]) if row else None


def retry_summary(conn, device_id, session):
    """Вернуть саммари в очередь. Готовое не трогаем."""
    cursor = conn.execute(
        "UPDATE summaries SET status='pending', attempts=0, last_error=NULL,"
        " next_try_at=datetime('now'), updated_at=datetime('now')"
        " WHERE device_id=? AND session=? AND status!='done'",
        (device_id, session),
    )
    return cursor.rowcount > 0


def set_device_state(conn, device_id, battery, firmware):
    """Заряд и прошивка с последней выгрузки. Ничего не пришло — ничего не пишем:
    затирать известное значение неизвестным хуже, чем показать вчерашнее."""
    if battery is None and firmware is None:
        return
    conn.execute(
        "INSERT INTO device_state (device_id, battery, firmware) VALUES (?,?,?)"
        " ON CONFLICT(device_id) DO UPDATE SET"
        "  battery=COALESCE(excluded.battery, device_state.battery),"
        "  firmware=COALESCE(excluded.firmware, device_state.firmware),"
        "  updated_at=datetime('now')",
        (device_id, battery, firmware),
    )


def device_states(conn):
    """Состояние каждого диктофона: заряд, прошивка, когда рассказано."""
    return [dict(row) for row in conn.execute(
        "SELECT device_id, battery, firmware, updated_at FROM device_state"
        " ORDER BY device_id")]


def _summary_stale(conn, device_id, session):
    """Имя попадает в транскрипт, который читает модель: исполнитель задачи
    стоит там именем, а не номером. Значит, смена имени делает саммари
    устаревшим ровно так же, как поздняя часть. parts_covered=0 возвращает
    сессию в очередь, next_try_at снимает паузу после прошлых неудач."""
    conn.execute(
        "UPDATE summaries SET parts_covered=0, next_try_at=datetime('now')"
        " WHERE device_id=? AND session=?",
        (device_id, session),
    )


def set_speaker(conn, device_id, session, part, speaker, name, is_me):
    conn.execute(
        "INSERT INTO speaker_names"
        " (device_id, session, part, speaker, name, is_me, auto, transcript_at)"
        " SELECT ?, ?, ?, ?, ?, ?, 0, " + _VERSION + " WHERE " + _SESSION_ALIVE +
        " ON CONFLICT(device_id, session, part, speaker) DO UPDATE SET"
        " name=excluded.name, is_me=excluded.is_me, auto=0,"
        " transcript_at=excluded.transcript_at",
        (device_id, session, part, speaker, name.strip(), 1 if is_me else 0,
         device_id, session, part, device_id, session),
    )
    _summary_stale(conn, device_id, session)
    conn.commit()


def speaker_label(names, part, speaker):
    """Имя, если задано, иначе технический номер. Номер не является личностью."""
    if speaker is None:
        return "Говорящий ?"
    entry = names.get((part, speaker))
    if entry and entry["name"]:
        return entry["name"]
    return f"Говорящий {speaker + 1}"


# --- шкала сессии -------------------------------------------------------------

def part_offsets(conn, device_id, session):
    """{часть: смещение в секундах}. Из размера файла: он есть у любой части,
    включая нерасшифрованные, поэтому шкала не едет от ошибок Deepgram."""
    rows = conn.execute(
        "SELECT part, bytes, bytes_per_second FROM parts"
        " WHERE device_id=? AND session=? ORDER BY part",
        (device_id, session),
    ).fetchall()
    offsets, cursor = {}, 0.0
    for row in rows:
        offsets[row["part"]] = cursor
        cursor += part_seconds(row["bytes"], row["bytes_per_second"])
    return offsets


def session_timeline(conn, device_id, session):
    """Реплики сессии на абсолютной шкале с подставленными именами.

    Возвращает (utterances, failed_parts). Каждая реплика: part, idx, start,
    end, start_abs, speaker, label, by_voice, text. by_voice — фразу подписал
    голос, а не номер: Gemini положил её под номер другого человека.
    Нерасшифрованные части перечислены отдельно, чтобы рендер честно показал
    дыру.
    """
    offsets = part_offsets(conn, device_id, session)
    names = speaker_names(conn, device_id, session)
    by_voice = line_names(conn, device_id, session)
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
            voice = by_voice.get((row["part"], idx))
            timeline.append({
                "part": row["part"],
                "idx": idx,
                "start": float(u.get("s") or 0.0),
                "end": float(u.get("e") or 0.0),
                "start_abs": base + float(u.get("s") or 0.0),
                "speaker": u.get("spk"),
                "label": voice or speaker_label(names, row["part"], u.get("spk")),
                "by_voice": voice is not None,
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
    # Один голос на всю запись — это заметка вслух, а не разговор, и разбор
    # у неё другой. Считаем по данным: номер говорящего у каждой реплики
    # модель видит и сама, но вывод из него делает не всегда. Номер живёт
    # внутри своей части, поэтому голос — это имя от владельца или пара
    # (часть, номер): «Говорящий 1» двух частей может быть двумя людьми.
    names = speaker_names(conn, device_id, session)
    # Фраза, подписанная голосом, — тоже голос: Gemini дал записи один номер,
    # а в нём говорит второй человек.
    voices = {u["label"] if u.get("by_voice")
              else (names.get((u["part"], u["speaker"])) or {}).get("name")
              or (u["part"], u["speaker"]) for u in timeline}
    if len(voices) == 1 and all(u["speaker"] is not None for u in timeline):
        blocks.append("[во всей записи один голос]")
    if len(parts) > 1:
        # Каждая часть распознавалась отдельно, поэтому номера говорящих в
        # разных частях друг с другом не связаны. Без этой строки модель
        # склеивает «Говорящего 1» из первой части с «Говорящим 1» из второй.
        blocks.append("[номера говорящих у каждой части свои и между частями"
                      " не совпадают; одинаковые имена — это один человек]")
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
        "SELECT device_id, session, part, bytes, bytes_per_second, envelope FROM parts"
        " ORDER BY device_id, session, part"
    ):
        key = (row["device_id"], row["session"])
        if row["envelope"]:
            values = json.loads(row["envelope"])
        else:
            seconds = part_seconds(row["bytes"], row["bytes_per_second"])
            values = [envelope.FLOOR] * max(1, round(seconds / envelope.BUCKET_SECONDS))
        found.setdefault(key, []).extend(values)
    return {key: envelope.resample(values, buckets) for key, values in found.items()}


def session_tapes(conn, buckets=48):
    """{(устройство, сессия): {"envelope": [...], "seconds": длина}} — «плёнка»
    записи для строк задач, вопросов и спорных мест: волна и место пункта на
    ней. Длина — по размеру файлов, как у плеера и шкалы реплик."""
    envelopes = session_envelopes(conn, buckets)
    seconds = {}
    for row in conn.execute("SELECT device_id, session, bytes, bytes_per_second FROM parts"):
        key = (row["device_id"], row["session"])
        seconds[key] = seconds.get(key, 0.0) + part_seconds(row["bytes"], row["bytes_per_second"])
    return {key: {"envelope": envelopes.get(key, []), "seconds": seconds[key]} for key in seconds}


def mark_offsets(conn):
    """{(устройство, сессия): [секунды от начала сессии]} — для списков.

    Двумя запросами и без разбора транскриптов: списку сессий нужны только
    места меток на шкале, а session_marks ради текста реплики читает и парсит
    все реплики сессии — на списке это была бы работа за весь архив сразу.
    """
    bases = {}
    cursor = {}
    for row in conn.execute(
        "SELECT device_id, session, part, bytes, bytes_per_second FROM parts"
        " ORDER BY device_id, session, part"
    ):
        key = (row["device_id"], row["session"])
        bases[(*key, row["part"])] = cursor.get(key, 0.0)
        cursor[key] = (cursor.get(key, 0.0)
                       + part_seconds(row["bytes"], row["bytes_per_second"]))

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

    results, cache, names = [], {}, {}
    for row in rows:
        key = (row["device_id"], row["session"])
        if key not in cache:
            timeline, _ = session_timeline(conn, *key)
            cache[key] = {(u["part"], u["idx"]): u for u in timeline}
            # Разговор в выдаче зовётся темой и днём, а не номером сессии:
            # номер владельцу ничего не говорит.
            topic = conn.execute(
                "SELECT json_extract(result, '$.topics[0]') FROM summaries"
                " WHERE device_id=? AND session=?", key).fetchone()
            names[key] = {"day": session_day(conn, *key).isoformat(),
                          "topic": topic[0] if topic else None}
        hit = cache[key].get((row["part"], row["idx"]))
        if hit:
            results.append({"device_id": row["device_id"], "session": row["session"],
                            **names[key], **hit})
    return results


NOTE_KINDS = (("decisions", "решение"), ("facts", "факт"), ("tasks", "задача"),
              ("open_questions", "вопрос"))


def search_notes(conn, query, limit=30):
    """Абзац саммари и его пункты (решения, факты, задачи, вопросы), где есть
    все слова запроса по леммам, последнее — по началу, как в поиске по
    репликам. «млн» из саммари раньше не находилось: искали только транскрипт."""
    words = [textnorm.lemma(t) for t in textnorm.tokens(query)]
    if not words:
        return []
    days, topics = session_days(conn), session_topics(conn)
    hits = []
    query_lemmas = set(words)
    for device_id, session, result in done_summaries(conn):
        # Абзац саммари — по предложениям: в выдаче одно нужное, а не десять строк.
        sentences = re.split(r"(?<=[.!?…])\s+", result.get("summary") or "")
        items = [("саммари", {"text": sentence}) for sentence in sentences if sentence]
        items += [(kind, item) for field, kind in NOTE_KINDS for item in result.get(field) or []]
        for kind, item in items:
            have = textnorm.lemmas(item.get("text") or "").split()
            if not (all(w in have for w in words[:-1]) and any(h.startswith(words[-1]) for h in have)):
                continue
            hits.append({
                "device_id": device_id, "session": session, "kind": kind,
                "text": item.get("text") or "", "time": item.get("time") or "",
                "source": item.get("source"),
                "day": days.get((device_id, session)),
                "topic": topics.get((device_id, session)),
                # Подсветка — данными, как у реплик: HTML в интерфейс не попадает.
                "segments": textnorm.segments(item.get("text") or "", query_lemmas),
            })
            if len(hits) >= limit:
                return hits
    return hits


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
    провалены, саммари нет или оно устарело, и сессия закончена — либо пришли
    все объявленные диктофоном части, либо новых частей давно не было."""
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
        "   AND (NOT EXISTS (SELECT 1 FROM parts p"
        "                    WHERE p.device_id = s.device_id AND p.session = s.session"
        f"                     AND p.received_at > datetime('now', '-{SESSION_QUIET_MINUTES} minutes'))"
        # Без объявления MAX даёт NULL, сравнение ложно, остаётся тишина.
        "        OR (SELECT COUNT(*) FROM parts p"
        "            WHERE p.device_id = s.device_id AND p.session = s.session)"
        "           >= (SELECT MAX(p.expected_parts) FROM parts p"
        "               WHERE p.device_id = s.device_id AND p.session = s.session))"
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


def source_pos(source):
    """(часть, реплика) у подтверждённого источника, иначе None.

    Неподтверждённый источник — ближайшая по времени реплика, то есть догадка.
    Сводить по ней два пункта значит объявить их одним по совпадению секунды.
    """
    if not source or not source.get("confirmed") or source.get("part") is None:
        return None
    return source["part"], source["idx"]


def tasks_by_source(conn, device_id, session):
    """{(часть, реплика): task_id} по уже сохранённым задачам сессии.

    Первая задача с этой реплики выигрывает: из одной фразы («сделаю смету и
    отправлю») модель иногда достаёт две задачи, и вторая получит свой
    идентификатор от текста.
    """
    known = {}
    for row in conn.execute(
        "SELECT task_id, source FROM tasks WHERE device_id=? AND session=?"
        " ORDER BY created_at, task_id", (device_id, session),
    ).fetchall():
        try:
            pos = source_pos(json.loads(row["source"] or "null"))
        except json.JSONDecodeError:
            continue
        if pos:
            known.setdefault(pos, row["task_id"])
    return known


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

    По умолчанию — день выгрузки первой части: часы диктофона пока не
    синхронизированы, настоящего времени записи в данных нет. Уточнённая
    владельцем дата перекрывает его.
    """
    days = {}
    for row in conn.execute(
        "SELECT device_id, session, MIN(received_at) AS first FROM parts"
        " GROUP BY device_id, session"
    ).fetchall():
        if row["first"]:
            days[(row["device_id"], row["session"])] = date.fromisoformat(row["first"][:10])
    for row in conn.execute("SELECT device_id, session, day FROM recording_days").fetchall():
        days[(row["device_id"], row["session"])] = date.fromisoformat(row["day"])
    return days


def session_day(conn, device_id, session):
    fixed = conn.execute(
        "SELECT day FROM recording_days WHERE device_id=? AND session=?",
        (device_id, session),
    ).fetchone()
    if fixed:
        return date.fromisoformat(fixed["day"])
    row = conn.execute(
        "SELECT MIN(received_at) AS first FROM parts WHERE device_id=? AND session=?",
        (device_id, session),
    ).fetchone()
    return date.fromisoformat(row["first"][:10]) if row and row["first"] else date.today()


def set_recording_day(conn, device_id, session, day):
    """Уточнить день разговора. day=None — вернуться ко дню выгрузки.

    Сроки задач считаются от дня разговора, поэтому пересчитываются здесь же:
    «в пятницу», сказанное в понедельник и в среду, — разные даты. Пересчёт
    идёт из сохранённого due_said, модель для этого не нужна.

    Возвращает получившийся день.
    """
    if day is None:
        conn.execute("DELETE FROM recording_days WHERE device_id=? AND session=?",
                     (device_id, session))
    else:
        conn.execute(
            "INSERT INTO recording_days (device_id, session, day) VALUES (?,?,?)"
            " ON CONFLICT(device_id, session) DO UPDATE SET day=excluded.day",
            (device_id, session, day.isoformat()),
        )
    base = session_day(conn, device_id, session)
    for row in conn.execute(
        "SELECT task_id, due_said FROM tasks"
        " WHERE device_id=? AND session=? AND due_said != ''",
        (device_id, session),
    ).fetchall():
        due = duedate.parse_due(row["due_said"], base)
        conn.execute(
            "UPDATE tasks SET due_date=? WHERE device_id=? AND session=? AND task_id=?",
            (due.isoformat() if due else None, device_id, session, row["task_id"]),
        )
    conn.commit()
    return base


def sync_tasks(conn, device_id, session, tasks):
    """Переносит задачи саммари в таблицу, сохраняя ручной статус.

    Задача, пропавшая из пересобранного саммари, остаётся видимой с пометкой:
    молча убрать то, что владелец уже читал, — потеря.
    """
    if not session_exists(conn, device_id, session):
        return  # сессию удалили, пока собиралось саммари
    day = session_day(conn, device_id, session)
    known = tasks_by_source(conn, device_id, session)
    fresh = {item_id(item) for item in tasks or []}
    seen = []
    for item in tasks or []:
        task_id = item_id(item)
        # Та же реплика, другие слова: модель пересказала задачу при пересборке.
        # Без этой привязки закрытая задача вернулась бы новой строкой, а старая
        # получила бы «исчезла из саммари» — владелец закрывал бы её дважды.
        same_place = known.get(source_pos(item.get("source")))
        if same_place and same_place not in fresh and same_place not in seen:
            task_id = same_place
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


def session_topics(conn):
    """{(устройство, сессия): первая тема саммари} — имя разговора в строках
    задач и вопросов: «откуда это» без перехода на страницу записи."""
    return {
        (row["device_id"], row["session"]): row["topic"]
        for row in conn.execute(
            "SELECT device_id, session, json_extract(result, '$.topics[0]') AS topic"
            " FROM summaries WHERE result IS NOT NULL").fetchall()
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
    days, topics, tapes = session_days(conn), session_topics(conn), session_tapes(conn)
    rows = []
    for row in conn.execute(sql, args).fetchall():
        item = dict(row)
        key = (item["device_id"], item["session"])
        item["day"], item["topic"] = days.get(key), topics.get(key)
        item["tape"] = tapes.get(key)
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
    days, topics, tapes = session_days(conn), session_topics(conn), session_tapes(conn)
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
                "topic": topics.get((device_id, session)),
                "tape": tapes.get((device_id, session)),
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
            # Подпись пустая: «задача без исполнителя · исполнитель не
            # прозвучал» — одно и то же дважды.
            items.append({"kind": "задача без исполнителя", "detail": "", **common})
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

    tapes = session_tapes(conn)
    for item in items:
        item["tape"] = tapes.get((item["device_id"], item["session"]))
    items.sort(key=lambda i: (-(i["session"] or 0), i["kind"]))
    return items


def session_rows(conn, limit=None):
    """Список сессий для интерфейса: свежие первыми.

    SQL жил в странице списка; вынесен сюда, потому что тот же список нужен
    сводке. Дублировать запрос в двух местах — способ получить две правды.
    """
    sql = (
        "SELECT p.device_id, p.session, COUNT(*) AS parts, SUM(p.bytes) AS bytes,"
        # Первая принятая часть: клавиша ▶ на карточке запускает звук прямо в
        # нажатии, ждать ответа сервера там нельзя — iPhone запретит автозапуск.
        "       MIN(p.part) AS first_part,"
        # Секунды суммируются по частям, а не байты: у моно и стерео разная
        # цена секунды, и сложить их размеры было бы сложением разных единиц.
        # Клэмп тоже по части: часть короче заголовка (битая, обрезанная) иначе
        # съедала бы чужие байты у соседней. part_offsets (шкала реплик) считает
        # так же поэлементно — список сессий обязан совпадать.
        "       SUM(MAX(p.bytes - ?, 0) * 1.0 / COALESCE(p.bytes_per_second, ?))"
        "         AS seconds,"
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
        "           AND t.status='done') AS done_parts,"
        # Упавшие части: карточка записи не должна показывать «готово», когда
        # кусок звука так и не расшифровался.
        "       (SELECT COUNT(*) FROM transcripts t"
        "         WHERE t.device_id=p.device_id AND t.session=p.session"
        "           AND t.status='failed') AS failed_parts"
        " FROM parts p GROUP BY p.device_id, p.session"
        " ORDER BY p.received_at DESC"
    )
    args = [WAV_HEADER_BYTES, BYTES_PER_SECOND]
    if limit is not None:
        sql += " LIMIT ?"
        args.append(int(limit))
    marks = mark_offsets(conn)
    # 60 столбиков: больше не разглядеть ни в строке списка, ни в блоке ленты.
    envelopes = session_envelopes(conn, 60)
    speakers = session_speakers(conn)
    rows = []
    for row in conn.execute(sql, args).fetchall():
        item = dict(row)
        item["day"] = item["first_at"][:10] if item["first_at"] else None
        item["seconds"] = item["seconds"] or 0.0
        item["envelope"] = envelopes.get((item["device_id"], item["session"]), [])
        # Метки отдаются секундами от начала сессии: списку нужно их число,
        # ленте дня — засечки на своих местах.
        item["marks"] = marks.get((item["device_id"], item["session"]), [])
        # Названные участники: разговор ищут по человеку не реже, чем по часу.
        item["speakers"] = speakers.get((item["device_id"], item["session"]), [])
        rows.append(item)
    return rows


# --- дайджест ---------------------------------------------------------------------

def digest(conn, days):
    """Числа за последние N суток. Только запросы к базе, без вызова модели."""
    window = f"-{days} days"
    one = lambda sql, *args: conn.execute(sql, args).fetchone()[0]

    recorded = one(
        "SELECT COALESCE(SUM(MAX(bytes - ?, 0) * 1.0"
        "                    / COALESCE(bytes_per_second, ?)), 0) FROM parts"
        " WHERE received_at >= datetime('now', ?)",
        WAV_HEADER_BYTES, BYTES_PER_SECOND, window)
    tasks = task_rows(conn, only_open=True)
    return {
        "days": days,
        # recorded уже в секундах: у моно и стерео разная цена секунды.
        "minutes": recorded / 60.0,
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
        # Пункты, а не причины: одна задача без исполнителя и с неразобранным
        # сроком — одно место, которое надо посмотреть, а не два.
        "review": len({(i["device_id"], i["session"], i["text"]) for i in review_items(conn)}),
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
        # Записи без готового саммари — и те, для которых строки ещё нет:
        # воркер заводит её сам, но до этого запись уже «обрабатывается» на
        # карточке, и «все собраны» здесь было бы неправдой.
        "summaries_pending": one(
            "SELECT COUNT(*) FROM (SELECT DISTINCT device_id, session FROM parts) p"
            " LEFT JOIN summaries s ON s.device_id=p.device_id AND s.session=p.session"
            " WHERE s.status IS NULL OR s.status!='done'"),
        "deepgram_minutes": (one(
            "SELECT COALESCE(SUM(duration_sec),0) FROM transcripts WHERE status='done'")
            or 0) / 60.0,
        "failed_parts": [dict(r) for r in conn.execute(
            "SELECT device_id, session, part, last_error, attempts FROM transcripts"
            " WHERE status='failed' ORDER BY session, part").fetchall()],
        "stuck_summaries": [dict(r) for r in conn.execute(
            "SELECT device_id, session, last_error, attempts FROM summaries"
            " WHERE status!='done' AND last_error IS NOT NULL ORDER BY session").fetchall()],
        # Что диктофон рассказал о себе: пустой список — прошивка ещё не шлёт
        # заряд и версию (плата в пути), и это честнее выдуманных процентов.
        "devices": device_states(conn),
        # Части, которые не удалось сжать: звук цел и работает, но владелец
        # должен знать, что архив встал.
        "archive_failed": [dict(r) for r in conn.execute(
            "SELECT device_id, session, part, archive_error, archive_attempts"
            " FROM parts WHERE archive_status='failed' ORDER BY session, part").fetchall()],
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
        "SELECT p.device_id, p.session, p.part, p.bytes, p.bytes_per_second,"
        "       t.duration_sec"
        " FROM parts p JOIN transcripts t"
        "   ON t.device_id=p.device_id AND t.session=p.session AND t.part=p.part"
        " WHERE t.status='done' AND t.duration_sec > 0"
    ).fetchall():
        by_size = part_seconds(row["bytes"], row["bytes_per_second"])
        if by_size and abs(by_size - row["duration_sec"]) / by_size > 0.02:
            status["duration_mismatch"].append({
                "device_id": row["device_id"], "session": row["session"],
                "part": row["part"], "by_size": by_size,
                "by_deepgram": row["duration_sec"],
            })
    return status
