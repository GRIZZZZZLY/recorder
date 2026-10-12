"""Проверки конвейера: расшифровка, саммари, очередь, память разговоров, интерфейс.

Настоящие Deepgram и Claude не вызываются: это платные внешние сервисы, и тест,
который тратит деньги, не тест. Форма ответа Deepgram взята из документации
(developers.deepgram.com, сверено 12 сентября 2026).
"""
import importlib
import json
import struct
import zlib
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"
PASSWORD = "owner-secret"
AUTH = ("owner", PASSWORD)


def wav(seconds=1, marker=0, channels=1):
    """Канонический 44-байтный заголовок плюс тишина, как пишет прошивка.

    channels=2 — Recorder RevA с двумя микрофонами: та же секунда весит вдвое
    больше, и заголовок описывает это целиком, включая байты в секунду и
    выравнивание кадра.
    """
    frame = channels * 2
    payload = bytes([marker]) * (16000 * frame * seconds)
    h = bytearray(44)
    h[0:4] = b"RIFF"
    struct.pack_into("<I", h, 4, 36 + len(payload))
    h[8:12] = b"WAVE"
    h[12:16] = b"fmt "
    struct.pack_into("<IHHIIHH", h, 16, 16, 1, channels, 16000, 16000 * frame, frame, 16)
    h[36:40] = b"data"
    struct.pack_into("<I", h, 40, len(payload))
    return bytes(h) + payload


def deepgram_reply(utterances, duration=600.0, paragraphs=None):
    """Ответ Deepgram в документированной форме.

    utterances: [(start, speaker, text)] — грубая нарезка по паузам.
    paragraphs: [(start, speaker, [текст, ...])] — абзацы с предложениями.
    По умолчанию абзацы повторяют реплики: так ведёт себя короткая запись,
    где обе нарезки совпадают. paragraphs=False убирает их совсем.
    """
    if paragraphs is None:
        paragraphs = [(start, spk, [text]) for start, spk, text in utterances]
    alternative = {
        "transcript": " ".join(t for _, _, t in utterances),
        "confidence": 0.98,
        "words": [{"word": "надо", "punctuated_word": "Надо", "confidence": 0.93,
                   "start": 1.2, "end": 1.6, "speaker": 0}],
    }
    if paragraphs is not False:
        alternative["paragraphs"] = {"paragraphs": [
            {
                "start": start,
                "end": start + 3.0,
                "speaker": spk,
                "num_words": sum(len(t.split()) for t in texts),
                # Предложение своего говорящего не несёт: он у абзаца.
                # Последнее предложение кончается вместе с абзацем, как у Deepgram.
                "sentences": [
                    {"text": text, "start": start + index,
                     "end": start + 3.0 if index == len(texts) - 1 else start + index + 1.0}
                    for index, text in enumerate(texts)
                ],
            }
            for start, spk, texts in paragraphs
        ]}
    return {
        "metadata": {"request_id": "req-1", "duration": duration, "channels": 1},
        "results": {
            "channels": [{"alternatives": [alternative]}],
            "utterances": [
                {"start": s, "end": s + 3.0, "channel": 0, "transcript": t, "speaker": spk}
                for s, spk, t in utterances
            ],
        },
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDER_DATA", str(tmp_path))
    monkeypatch.setenv("RECORDER_DEVICE_TOKEN", TOKEN)
    monkeypatch.setenv("RECORDER_OWNER_PASSWORD", PASSWORD)
    monkeypatch.setenv("RECORDER_QUIET_MINUTES", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "key-for-tests")

    # Архив выключен: ffmpeg есть не на всякой машине разработки, а тесты
    # обязаны вести себя одинаково. Проверки архива включают его сами.
    monkeypatch.setenv("RECORDER_ARCHIVE_FLAC", "0")
    # Настоящий Claude проверки не зовут никогда: на VPS он установлен, и
    # проверка без подмены изложения висела там до таймаута в 900 с.
    monkeypatch.setenv("RECORDER_CLAUDE_BIN", "claude-is-not-called-in-tests")

    import archive, db, duedate, textnorm, stt, transcribe, summarize, voiceprint
    import app as app_module
    import worker

    for module in (textnorm, duedate, db, stt, transcribe, voiceprint, archive, summarize,
                   app_module, worker):
        importlib.reload(module)
    db.init_storage()
    # Плеер отдаёт громкую копию (audio.loud_copy); проверки отдачи сверяют
    # байты исходника, громкость проверяется отдельно.
    import audio
    monkeypatch.setattr(audio, "PLAY_LOUDNORM", False)

    class Env:
        pass

    bundle = Env()
    bundle.db = db
    bundle.archive = archive
    bundle.stt = stt
    bundle.duedate = duedate
    bundle.transcribe = transcribe
    bundle.summarize = summarize
    bundle.worker = worker
    bundle.textnorm = textnorm
    bundle.app = app_module.app
    bundle.tmp = tmp_path
    return bundle


def upload(env, session=7, part=1, marker=0, seconds=1, marks=None, session_parts=None,
           extra=None, channels=1, body=None):
    body = wav(seconds=seconds, marker=marker, channels=channels) if body is None else body
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-Device-Id": "xiao-01",
        "X-Session": str(session),
        "X-Part": str(part),
        "X-Crc32": f"{zlib.crc32(body) & 0xFFFFFFFF:08x}",
    }
    if marks:
        headers["X-Marks"] = ",".join(str(offset) for offset in marks)
    if session_parts is not None:
        headers["X-Session-Parts"] = str(session_parts)
    headers.update(extra or {})
    with TestClient(env.app) as client:
        return client.post("/api/v1/upload", content=body, headers=headers)


PART_TEXTS = {
    1: [(1.0, 0, "Надо к пятнице выслать смету заказчику."),
        (5.0, 1, "Беру на себя, пришлю в четверг.")],
    2: [(1.0, 0, "Ещё надо проверить договор с подрядчиком."),
        (4.0, 1, "Кто смотрит, пока не решили.")],
    3: [(2.0, 2, "Встреча со стройнадзором двадцать третьего.")],
}


def fake_transcribe(env, texts=None):
    """Подмена Deepgram: реплики берутся по номеру части из имени файла."""
    def call(path):
        part = int(path.rsplit("_P", 1)[1][:2])
        utterances = (texts or PART_TEXTS).get(part, [(0.5, 0, "реплика")])
        return env.transcribe.parse_payload(deepgram_reply(utterances))
    return call


def run_all(env):
    with closing(env.db.connect()) as conn:
        while env.worker.tick(conn):
            pass


SUMMARY = {
    "summary": "Обсудили смету.",
    "topics": ["смета для заказчика"],
    "decisions": [{"text": "Смету отправить в пятницу", "time": "0:00:01",
                   "quote": "к пятнице выслать смету"}],
    "facts": [],
    "tasks": [{"text": "Выслать смету", "owner": "Мария", "due": "пятница",
               "time": "0:00:01", "quote": "к пятнице выслать смету заказчику"}],
    "open_questions": [],
}


def summary_stub(env, monkeypatch, result=None):
    monkeypatch.setattr(env.worker.summarize, "summarize",
                        lambda text: json.loads(json.dumps(result or SUMMARY)))


# --- разбор ответа Deepgram -------------------------------------------------


# --- очередь ----------------------------------------------------------------

def test_part_is_transcribed_then_session_summarized(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)

    with closing(env.db.connect()) as conn:
        # один проход делает и расшифровку, и саммари: иначе сессия ждала бы
        # лишний цикл сна впустую
        assert env.worker.tick(conn) is True
        assert env.worker.tick(conn) is False

        row = conn.execute("SELECT * FROM transcripts").fetchone()
        assert row["status"] == "done"
        assert json.loads(row["utterances"])[0]["text"].startswith("Надо")
        summary = conn.execute("SELECT * FROM summaries").fetchone()
        assert summary["status"] == "done"
        assert json.loads(summary["result"])["tasks"][0]["owner"] == "Мария"
        assert summary["parts_covered"] == 1


def test_done_transcript_is_never_requested_again(env, monkeypatch):
    """Повторный вызов Deepgram — это повторная оплата."""
    upload(env)
    calls = []

    def counting(path):
        calls.append(path)
        return fake_transcribe(env)(path)

    monkeypatch.setattr(env.transcribe, "transcribe_file", counting)
    summary_stub(env, monkeypatch)

    with closing(env.db.connect()) as conn:
        for _ in range(5):
            env.worker.tick(conn)
    assert len(calls) == 1

    upload(env)   # та же часть пришла заново — платная работа не повторяется
    with closing(env.db.connect()) as conn:
        for _ in range(3):
            env.worker.tick(conn)
    assert len(calls) == 1


def test_failed_summary_keeps_transcript_available(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))

    def failing(text):
        raise env.summarize.SummaryError("модель недоступна")

    monkeypatch.setattr(env.worker.summarize, "summarize", failing)
    run_all(env)

    with closing(env.db.connect()) as conn:
        assert conn.execute("SELECT status FROM transcripts").fetchone()[0] == "done"
        summary = conn.execute("SELECT status, last_error FROM summaries").fetchone()
        assert summary["status"] == "pending"
        assert "недоступна" in summary["last_error"]
        assert env.db.session_transcript(conn, "xiao-01", 7) != ""


def test_transient_failure_is_retried_forever(env, monkeypatch):
    """Пропавший интернет или лежащий Deepgram — не повод бросать часть навсегда.
    После любого числа попыток она остаётся в очереди, не чаще раза в сутки."""
    upload(env)

    def network_down(path):
        raise env.transcribe.TranscribeError("сеть", retryable=True)

    monkeypatch.setattr(env.transcribe, "transcribe_file", network_down)
    with closing(env.db.connect()) as conn:
        env.worker.tick(conn)
        conn.execute("UPDATE transcripts SET attempts=99,"
                     " next_try_at=datetime('now', '-1 minute')")
        conn.commit()
        assert env.worker.tick(conn) is True
        row = conn.execute(
            "SELECT status, attempts,"
            " (julianday(next_try_at) - julianday('now')) * 1440 AS wait_min"
            " FROM transcripts").fetchone()
    assert row["status"] == "pending"
    assert row["attempts"] == 100
    assert 1000 < row["wait_min"] <= 1440


def test_declared_complete_session_is_summarized_without_waiting(env, monkeypatch):
    """Диктофон сказал, сколько частей в сессии. Все пришли — тишину не ждём."""
    monkeypatch.setattr(env.db, "SESSION_QUIET_MINUTES", 12)
    upload(env, part=1, session_parts=2)
    upload(env, part=2, marker=2, session_parts=2)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)
    with closing(env.db.connect()) as conn:
        assert conn.execute("SELECT status FROM summaries").fetchone()[0] == "done"


def test_declared_incomplete_session_still_waits_for_quiet(env, monkeypatch):
    """Объявлено две части, пришла одна: как раньше, ждём тишины."""
    monkeypatch.setattr(env.db, "SESSION_QUIET_MINUTES", 12)
    upload(env, part=1, session_parts=2)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)
    with closing(env.db.connect()) as conn:
        done = conn.execute(
            "SELECT COUNT(*) FROM summaries WHERE status='done'").fetchone()[0]
    assert done == 0


def test_unrecoverable_part_does_not_block_session_summary(env, monkeypatch):
    upload(env, part=1)
    upload(env, part=2, marker=2)

    def half_broken(path):
        if "P02" in path:
            raise env.transcribe.TranscribeError("битое аудио", retryable=False)
        return fake_transcribe(env)(path)

    monkeypatch.setattr(env.transcribe, "transcribe_file", half_broken)
    captured = []
    monkeypatch.setattr(env.worker.summarize, "summarize",
                        lambda text: captured.append(text) or json.loads(json.dumps(SUMMARY)))
    run_all(env)

    with closing(env.db.connect()) as conn:
        assert conn.execute("SELECT status FROM summaries").fetchone()[0] == "done"
    assert "часть 02: расшифровка не удалась" in captured[0]


# --- абсолютная шкала сессии --------------------------------------------------

def test_timeline_is_absolute_across_parts(env, monkeypatch):
    """Часть 2 начинается там, где кончилась часть 1: 2 секунды аудио → сдвиг 2 с."""
    for part in (1, 2, 3):
        upload(env, part=part, marker=part, seconds=2)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)

    with closing(env.db.connect()) as conn:
        timeline, failed = env.db.session_timeline(conn, "xiao-01", 7)
        text = env.db.session_transcript(conn, "xiao-01", 7)

    by_part = {}
    for u in timeline:
        by_part.setdefault(u["part"], []).append(u)
    assert by_part[1][0]["start_abs"] == 1.0          # без сдвига
    assert by_part[2][0]["start_abs"] == 2.0 + 1.0    # после 2 с первой части
    assert by_part[3][0]["start_abs"] == 4.0 + 2.0    # после двух частей по 2 с
    assert failed == []
    # модель видит единую шкалу и границы частей
    assert "[0:00:01] Говорящий 1:" in text
    assert "[0:00:03] Говорящий 1:" in text
    assert "[0:00:06] Говорящий 3:" in text
    assert "[часть 02]" in text


# --- источник каждого вывода --------------------------------------------------

def test_quote_found_confirms_source_with_seek_target(env, monkeypatch):
    for part in (1, 2):
        upload(env, part=part, marker=part, seconds=2)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    result = json.loads(json.dumps(SUMMARY))
    # пункт из второй части: абсолютное время 2 + 1 = 3 с
    result["decisions"].append({"text": "Проверить договор", "time": "0:00:03",
                                "quote": "проверить договор с подрядчиком"})
    summary_stub(env, monkeypatch, result)
    run_all(env)

    with closing(env.db.connect()) as conn:
        stored = json.loads(conn.execute("SELECT result FROM summaries").fetchone()[0])

    first = stored["tasks"][0]["source"]
    assert first == {"part": 1, "idx": 0, "start": 1.0, "start_abs": 1.0, "confirmed": True}
    second = stored["decisions"][1]["source"]
    assert second["part"] == 2 and second["start"] == 1.0 and second["confirmed"] is True


# --- поиск -------------------------------------------------------------------


# --- состояние обработки -----------------------------------------------------

def test_status_sees_gap_failure_and_duration_mismatch(env, monkeypatch):
    upload(env, part=1, seconds=2)
    upload(env, part=3, marker=3, seconds=2)     # части 2 нет

    def mixed(path):
        if "P03" in path:
            raise env.transcribe.TranscribeError("битое аудио", retryable=False)
        # Deepgram насчитал 1 с там, где файл на 2 с
        return env.transcribe.parse_payload(deepgram_reply(PART_TEXTS[1], duration=1.0))

    monkeypatch.setattr(env.transcribe, "transcribe_file", mixed)
    summary_stub(env, monkeypatch)
    run_all(env)

    with closing(env.db.connect()) as conn:
        s = env.db.processing_status(conn)
    assert s["gaps"] == [{"device_id": "xiao-01", "session": 7, "missing": [2]}]
    assert s["failed_parts"][0]["part"] == 3
    assert s["duration_mismatch"][0]["part"] == 1
    assert s["transcripts_pending"] == 0
    assert abs(s["deepgram_minutes"] - 1.0 / 60) < 1e-6


# --- интерфейс --------------------------------------------------------------


def test_audio_supports_range_requests(env):
    """Safari на iPhone без Range не проигрывает аудио. Адрес — часть протокола
    плеера (и старого интерфейса, и нового фронтенда), переезд под /legacy
    вместе с остальными страницами владельца сломал бы воспроизведение."""
    upload(env)
    with TestClient(env.app) as client:
        response = client.get("/audio/xiao-01/7/1", auth=AUTH, headers={"Range": "bytes=0-43"})
        assert response.status_code == 206
        assert response.content[:4] == b"RIFF"
        # Старый адрес под /legacy эту же часть отдавать не должен — иначе
        # регрессию снова не заметят, как в первом раунде.
        assert client.get("/legacy/audio/xiao-01/7/1", auth=AUTH).status_code != 200


# --- сроки -------------------------------------------------------------------

def test_due_phrases_become_dates_or_stay_unparsed(env):
    """12 сентября 2026 — суббота; отсчёт идёт от дня разговора, не от сегодня."""
    from datetime import date

    base = date(2026, 9, 12)
    due = env.duedate.parse_due
    assert due("к пятнице", base) == date(2026, 9, 18)
    assert due("в четверг вечером", base) == date(2026, 9, 17)
    assert due("23 сентября", base) == date(2026, 9, 23)
    assert due("завтра", base) == date(2026, 9, 13)
    assert due("через неделю", base) == date(2026, 9, 19)
    assert due("через 3 дня", base) == date(2026, 9, 15)
    assert due("до конца месяца", base) == date(2026, 9, 30)
    assert due("23.09", base) == date(2026, 9, 23)
    # Прошедшая дата означает следующий год, а не прошлое.
    assert due("3 марта", base) == date(2027, 3, 3)
    # Неоднозначное не превращаем в дату: такой пункт уходит в спорные места.
    assert due("как получится", base) is None
    assert due("в сентябре", base) is None
    assert due("до конца недели", base) is None
    assert due("", base) is None


# --- задачи ------------------------------------------------------------------

def test_task_survives_summary_rebuild_and_keeps_manual_status(env, monkeypatch):
    """Поздняя часть пересобирает саммари. Закрытие владельца при этом не теряется."""
    upload(env, part=1)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))

    other = json.loads(json.dumps(SUMMARY))
    other["tasks"] = [{"text": "Совсем другая задача", "owner": "", "due": "как получится",
                       "time": "0:00:05", "quote": "беру на себя"}]
    versions = [SUMMARY, SUMMARY, other]
    calls = []

    def staged(text):
        calls.append(text)
        return json.loads(json.dumps(versions[min(len(calls) - 1, len(versions) - 1)]))

    monkeypatch.setattr(env.worker.summarize, "summarize", staged)
    run_all(env)

    with closing(env.db.connect()) as conn:
        rows = env.db.task_rows(conn)
        assert len(rows) == 1
        task_id = rows[0]["task_id"]
        assert rows[0]["due_date"]               # «пятница» разобрана в дату
        env.db.set_task_status(conn, "xiao-01", 7, task_id, "done")

    upload(env, part=2, marker=2)
    run_all(env)
    with closing(env.db.connect()) as conn:
        kept = conn.execute("SELECT status, vanished FROM tasks WHERE task_id=?",
                            (task_id,)).fetchone()
        assert kept["status"] == "done" and kept["vanished"] == 0

    upload(env, part=3, marker=3)
    run_all(env)
    with closing(env.db.connect()) as conn:
        gone = conn.execute("SELECT status, vanished FROM tasks WHERE task_id=?",
                            (task_id,)).fetchone()
        assert gone["vanished"] == 1             # исчезла из саммари, но не удалена
        assert gone["status"] == "done"
        assert [t["text"] for t in env.db.task_rows(conn)] == ["Совсем другая задача"]


def test_rephrased_task_keeps_its_id_and_manual_status(env, monkeypatch):
    """Пересобранное саммари пересказывает ту же задачу другими словами.

    Идентификатор считается из текста, поэтому пересказ заводил новую строку,
    а закрытая владельцем задача получала «исчезла из саммари». Задача
    привязана к своей реплике, и по ней узнаётся та же самая.
    """
    upload(env, part=1)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))

    rephrased = json.loads(json.dumps(SUMMARY))
    rephrased["tasks"] = [{"text": "Отправить смету заказчику", "owner": "Мария",
                           "due": "пятница", "time": "0:00:01",
                           "quote": "выслать смету заказчику"}]
    versions = [SUMMARY, rephrased]
    calls = []

    def staged(text):
        calls.append(text)
        return json.loads(json.dumps(versions[min(len(calls) - 1, len(versions) - 1)]))

    monkeypatch.setattr(env.worker.summarize, "summarize", staged)
    run_all(env)

    with closing(env.db.connect()) as conn:
        rows = env.db.task_rows(conn)
        assert len(rows) == 1
        task_id = rows[0]["task_id"]
        env.db.set_task_status(conn, "xiao-01", 7, task_id, "done")

    upload(env, part=2, marker=2)          # поздняя часть пересобирает саммари
    run_all(env)

    with closing(env.db.connect()) as conn:
        after = conn.execute("SELECT task_id, text, status, vanished FROM tasks").fetchall()
    assert len(after) == 1, "пересказ той же задачи завёл вторую строку"
    assert after[0]["task_id"] == task_id
    assert after[0]["text"] == "Отправить смету заказчику"   # текст всё же обновился
    assert after[0]["status"] == "done" and after[0]["vanished"] == 0


def test_two_tasks_from_one_reply_stay_two_tasks(env, monkeypatch):
    """Из одной фразы модель достаёт две задачи. Привязка к реплике не должна
    сводить их в одну: реплика та же, а дела разные."""
    upload(env, part=1)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    result = json.loads(json.dumps(SUMMARY))
    result["tasks"] = [
        {"text": "Посчитать смету", "owner": "", "due": "", "time": "0:00:01",
         "quote": "выслать смету заказчику"},
        {"text": "Отправить смету", "owner": "", "due": "", "time": "0:00:01",
         "quote": "выслать смету заказчику"},
    ]
    summary_stub(env, monkeypatch, result)
    run_all(env)

    with closing(env.db.connect()) as conn:
        rows = env.db.task_rows(conn)
    assert sorted(t["text"] for t in rows) == ["Отправить смету", "Посчитать смету"]


def test_renaming_a_speaker_rebuilds_the_summary(env, monkeypatch):
    """Имя говорящего уходит в транскрипт, который читает модель: исполнителя
    задачи она берёт оттуда. Значит, переименование делает саммари устаревшим
    так же, как поздняя часть, — иначе в задаче остался бы «Говорящий 1»."""
    upload(env, part=1)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    seen = []

    def remember(text):
        seen.append(text)
        return json.loads(json.dumps(SUMMARY))

    monkeypatch.setattr(env.worker.summarize, "summarize", remember)
    run_all(env)
    assert len(seen) == 1 and "Говорящий 1:" in seen[0]

    with closing(env.db.connect()) as conn:
        env.db.set_speaker(conn, "xiao-01", 7, 1, 0, "Мария", False)
    run_all(env)
    assert len(seen) == 2, "саммари осталось со старым именем"
    assert "Мария:" in seen[1]

    # Пересборка одноразовая: без новых поводов очередь пуста.
    run_all(env)
    assert len(seen) == 2


def test_one_number_in_every_part_is_not_one_voice(env, monkeypatch):
    """Номера говорящих у каждой части свои: «Говорящий 1» первой части и
    «Говорящий 1» второй могут быть двумя людьми. Один голос доказывает
    только одно имя, которое дал владелец."""
    upload(env, session=13, part=1)
    upload(env, session=13, part=2, marker=1)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, {
        1: [(1.0, 0, "Я рассказываю про смету.")],
        2: [(1.0, 0, "А я — про договор.")],
    }))
    summary_stub(env, monkeypatch)
    run_all(env)

    with closing(env.db.connect()) as conn:
        assert "один голос" not in env.db.session_transcript(conn, "xiao-01", 13)
        for part in (1, 2):
            env.db.set_speaker(conn, "xiao-01", 13, part, 0, "Игорь", True)
        conn.commit()
        assert "[во всей записи один голос]" in env.db.session_transcript(conn, "xiao-01", 13)


def test_my_tasks_come_from_speaker_marked_as_me(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)

    with closing(env.db.connect()) as conn:
        assert env.db.task_rows(conn, mine=True) == []     # никто не отмечен «это я»
        env.db.set_speaker(conn, "xiao-01", 7, 1, 1, "Мария", True)
        mine = env.db.task_rows(conn, mine=True)
    assert len(mine) == 1 and mine[0]["owner"] == "Мария"


# --- вопросы без ответа --------------------------------------------------------

def test_the_same_number_in_two_parts_is_not_the_same_person(env, monkeypatch):
    """Deepgram нумерует голоса внутри одной части. Склеить номера значит
    приписать слова тому, кто их не говорил."""
    upload(env, part=1)
    upload(env, part=2, marker=2)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)

    with closing(env.db.connect()) as conn:
        env.db.set_speaker(conn, "xiao-01", 7, 1, 0, "Мария", False)
        timeline, _ = env.db.session_timeline(conn, "xiao-01", 7)

    named = {(u["part"], u["speaker"]): u["label"] for u in timeline}
    assert named[(1, 0)] == "Мария"
    assert named[(2, 0)] == "Говорящий 1", "имя из первой части утекло во вторую"


def test_question_state_is_manual_and_survives_rebuild(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    result = json.loads(json.dumps(SUMMARY))
    result["open_questions"] = [{"text": "Кто смотрит договор", "time": "0:00:05",
                                 "quote": "кто смотрит"}]
    summary_stub(env, monkeypatch, result)
    run_all(env)

    with closing(env.db.connect()) as conn:
        rows = env.db.question_rows(conn)
        assert len(rows) == 1
        question_id = rows[0]["question_id"]
        env.db.set_question_state(conn, "xiao-01", 7, question_id, "answered")
        assert env.db.question_rows(conn) == []
        assert len(env.db.question_rows(conn, only_open=False)) == 1

    upload(env, part=2, marker=2)
    run_all(env)
    with closing(env.db.connect()) as conn:
        assert env.db.question_rows(conn) == []      # пометка пережила пересборку


# --- лента проекта -------------------------------------------------------------


# --- спорные места -------------------------------------------------------------

def test_review_collects_only_what_needs_owner(env, monkeypatch):
    upload(env)

    def shaky(path):
        payload = deepgram_reply(PART_TEXTS[1])
        payload["results"]["channels"][0]["alternatives"][0]["words"] = [
            {"word": "23", "punctuated_word": "23", "start": 1.1, "end": 1.5,
             "speaker": 0, "confidence": 0.4},
        ]
        return env.transcribe.parse_payload(payload)

    monkeypatch.setattr(env.transcribe, "transcribe_file", shaky)
    result = json.loads(json.dumps(SUMMARY))
    result["facts"] = [
        {"text": "Встреча 23 числа", "time": "0:00:01", "quote": "к пятнице выслать смету"},
        {"text": "Выдумка", "time": "0:00:01", "quote": "этих слов в записи нет"},
    ]
    result["tasks"] = [{"text": "Проверить договор", "owner": "", "due": "как получится",
                        "time": "0:00:01", "quote": "к пятнице выслать смету"}]
    summary_stub(env, monkeypatch, result)
    run_all(env)

    with closing(env.db.connect()) as conn:
        kinds = [i["kind"] for i in env.db.review_items(conn)]
    assert "задача без исполнителя" in kinds
    assert "срок не разобран" in kinds
    assert "число распознано неуверенно" in kinds
    assert "источник не подтверждён" in kinds
    # подтверждённые пункты владельца не беспокоят
    assert kinds.count("источник не подтверждён") == 1


# --- дайджест ------------------------------------------------------------------


