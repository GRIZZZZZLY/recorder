"""Проверки конвейера: расшифровка, саммари, очередь, память разговоров, интерфейс.

Настоящие Deepgram и Claude не вызываются: это платные внешние сервисы, и тест,
который тратит деньги, не тест. Форма ответа Deepgram взята из документации
(developers.deepgram.com, сверено 12 сентября 2026).
"""
import importlib
import json
import os
import struct
import zlib
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"
PASSWORD = "owner-secret"
AUTH = ("owner", PASSWORD)


def wav(seconds=1, marker=0):
    payload = bytes([marker]) * (16000 * 2 * seconds)
    h = bytearray(44)
    h[0:4] = b"RIFF"
    struct.pack_into("<I", h, 4, 36 + len(payload))
    h[8:12] = b"WAVE"
    h[12:16] = b"fmt "
    struct.pack_into("<IHHIIHH", h, 16, 16, 1, 1, 16000, 32000, 2, 16)
    h[36:40] = b"data"
    struct.pack_into("<I", h, 40, len(payload))
    return bytes(h) + payload


def deepgram_reply(utterances, duration=600.0):
    """Ответ Deepgram в документированной форме. utterances: [(start, speaker, text)]."""
    return {
        "metadata": {"request_id": "req-1", "duration": duration, "channels": 1},
        "results": {
            "channels": [{"alternatives": [{
                "transcript": " ".join(t for _, _, t in utterances),
                "confidence": 0.98,
                "words": [{"word": "надо", "punctuated_word": "Надо", "confidence": 0.93,
                           "start": 1.2, "end": 1.6, "speaker": 0}],
            }]}],
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

    import db, duedate, textnorm, transcribe, summarize, app as app_module, worker

    for module in (textnorm, duedate, db, transcribe, summarize, app_module, worker):
        importlib.reload(module)
    db.init_storage()

    class Env:
        pass

    bundle = Env()
    bundle.db = db
    bundle.duedate = duedate
    bundle.transcribe = transcribe
    bundle.summarize = summarize
    bundle.worker = worker
    bundle.textnorm = textnorm
    bundle.app = app_module.app
    bundle.tmp = tmp_path
    return bundle


def upload(env, session=7, part=1, marker=0, seconds=1, marks=None):
    body = wav(seconds=seconds, marker=marker)
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-Device-Id": "xiao-01",
        "X-Session": str(session),
        "X-Part": str(part),
        "X-Crc32": f"{zlib.crc32(body) & 0xFFFFFFFF:08x}",
    }
    if marks:
        headers["X-Marks"] = ",".join(str(offset) for offset in marks)
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

def test_utterances_keep_time_speaker_and_text(env):
    t = env.transcribe.parse_payload(deepgram_reply([(1.2, 1, "привет")]))
    assert t.utterances == [{"s": 1.2, "e": 4.2, "spk": 1, "text": "привет"}]
    assert t.text == "привет"
    assert t.duration_sec == 600.0


def test_without_utterances_whole_part_is_one_utterance(env):
    payload = deepgram_reply([(0, 0, "сплошной текст")])
    payload["results"]["utterances"] = []
    t = env.transcribe.parse_payload(payload)
    assert t.utterances == [{"s": 0.0, "e": 600.0, "spk": None, "text": "сплошной текст"}]


def test_words_are_trimmed_to_what_ui_needs(env):
    words = env.transcribe.extract_words(deepgram_reply([(1, 0, "x")]))
    assert words == [{"w": "Надо", "s": 1.2, "e": 1.6, "spk": 0, "c": 0.93}]


@pytest.mark.parametrize(
    "status, retryable",
    [(401, False), (402, False), (400, False), (429, True), (503, True), (504, True)],
)
def test_deepgram_errors_are_classified(env, monkeypatch, status, retryable):
    def reply(request):
        return httpx.Response(status, text="{}")

    monkeypatch.setattr(
        env.transcribe.httpx, "post",
        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(reply)).post(
            "http://x", content=b""
        ),
    )
    audio = env.tmp / "a.wav"
    audio.write_bytes(wav())
    with pytest.raises(env.transcribe.TranscribeError) as caught:
        env.transcribe.transcribe_file(str(audio))
    assert caught.value.retryable is retryable


def test_missing_key_is_not_retryable(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "API_KEY", "")
    audio = env.tmp / "a.wav"
    audio.write_bytes(wav())
    with pytest.raises(env.transcribe.TranscribeError) as caught:
        env.transcribe.transcribe_file(str(audio))
    assert caught.value.retryable is False


# --- очередь ----------------------------------------------------------------

def test_part_is_transcribed_then_session_summarized(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
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

    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", counting)
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


def test_late_part_makes_summary_stale_and_it_is_rebuilt(env, monkeypatch):
    upload(env, part=1)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
    summaries = []

    def counting_summary(text):
        summaries.append(text)
        return json.loads(json.dumps(SUMMARY))

    monkeypatch.setattr(env.worker.summarize, "summarize", counting_summary)

    run_all(env)
    assert len(summaries) == 1
    upload(env, part=2, marker=2)
    run_all(env)
    assert len(summaries) == 2
    with closing(env.db.connect()) as conn:
        assert conn.execute("SELECT parts_covered FROM summaries").fetchone()[0] == 2


def test_failed_summary_keeps_transcript_available(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))

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


def test_auth_failure_in_summary_is_reported_not_retried_fast(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))

    def expired(text):
        raise env.summarize.SummaryError("authentication_error: OAuth access token has expired")

    monkeypatch.setattr(env.worker.summarize, "summarize", expired)
    run_all(env)

    with closing(env.db.connect()) as conn:
        row = conn.execute("SELECT last_error, next_try_at FROM summaries").fetchone()
        assert "нужен вход" in row["last_error"]
        gap = conn.execute(
            "SELECT CAST((julianday(?) - julianday('now')) * 1440 AS INT)",
            (row["next_try_at"],),
        ).fetchone()[0]
        assert gap >= 50


def test_unrecoverable_part_does_not_block_session_summary(env, monkeypatch):
    upload(env, part=1)
    upload(env, part=2, marker=2)

    def half_broken(path):
        if "P02" in path:
            raise env.transcribe.TranscribeError("битое аудио", retryable=False)
        return fake_transcribe(env)(path)

    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", half_broken)
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
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
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


def test_abs_time_roundtrip(env):
    assert env.db.format_abs(3661) == "1:01:01"
    assert env.db.parse_abs("1:01:01") == 3661
    assert env.db.parse_abs("00:04") == 4
    assert env.db.parse_abs("мусор") is None


# --- источник каждого вывода --------------------------------------------------

def test_quote_found_confirms_source_with_seek_target(env, monkeypatch):
    for part in (1, 2):
        upload(env, part=part, marker=part, seconds=2)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
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


def test_quote_not_found_is_flagged_but_still_seekable(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
    result = json.loads(json.dumps(SUMMARY))
    result["facts"] = [{"text": "Выдумка", "time": "0:00:05",
                        "quote": "этих слов в записи нет"}]
    summary_stub(env, monkeypatch, result)
    run_all(env)

    with closing(env.db.connect()) as conn:
        stored = json.loads(conn.execute("SELECT result FROM summaries").fetchone()[0])
    src = stored["facts"][0]["source"]
    assert src["confirmed"] is False
    assert src["part"] == 1 and src["start"] == 5.0   # ближайшая по времени реплика
    # пункт не удалён молча
    assert stored["facts"][0]["text"] == "Выдумка"


def test_source_none_when_no_time_and_no_quote(env):
    timeline = [{"part": 1, "idx": 0, "start": 1.0, "start_abs": 1.0, "text": "что-то"}]
    item = {"text": "x", "time": "", "quote": ""}
    assert env.summarize.find_source(item, timeline) is None


# --- поиск -------------------------------------------------------------------

def test_search_finds_other_word_form(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)

    if not env.textnorm.available():
        pytest.skip("pymorphy3 не установлен")
    with closing(env.db.connect()) as conn:
        hits = env.db.search(conn, "сметы")      # в записи было «смету»
        assert len(hits) == 1
        assert hits[0]["part"] == 1 and hits[0]["idx"] == 0
        assert hits[0]["start_abs"] == 1.0
        assert env.db.search(conn, "подводная лодка") == []


# --- состояние обработки -----------------------------------------------------

def test_status_sees_gap_failure_and_duration_mismatch(env, monkeypatch):
    upload(env, part=1, seconds=2)
    upload(env, part=3, marker=3, seconds=2)     # части 2 нет

    def mixed(path):
        if "P03" in path:
            raise env.transcribe.TranscribeError("битое аудио", retryable=False)
        # Deepgram насчитал 1 с там, где файл на 2 с
        return env.transcribe.parse_payload(deepgram_reply(PART_TEXTS[1], duration=1.0))

    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", mixed)
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

def test_web_requires_password(env):
    with TestClient(env.app) as client:
        assert client.get("/").status_code == 401
        assert client.get("/", auth=("owner", "wrong")).status_code == 401
        assert client.get("/", auth=AUTH).status_code == 200


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


def test_audio_path_in_db_is_absolute(env, monkeypatch):
    """Приёмник и воркер запускаются из разных каталогов; относительный путь
    читался бы у одного и отдавал 500 у другого. Этот отказ уже случался."""
    upload(env)
    with closing(env.db.connect()) as conn:
        path = conn.execute("SELECT audio_path FROM parts").fetchone()[0]
    assert os.path.isabs(path), path
    monkeypatch.chdir(env.tmp)
    assert os.path.exists(path)


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
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))

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


def test_my_tasks_come_from_speaker_marked_as_me(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)

    with closing(env.db.connect()) as conn:
        assert env.db.task_rows(conn, mine=True) == []     # никто не отмечен «это я»
        env.db.set_speaker(conn, "xiao-01", 7, 1, "Мария", True)
        mine = env.db.task_rows(conn, mine=True)
    assert len(mine) == 1 and mine[0]["owner"] == "Мария"


# --- вопросы без ответа --------------------------------------------------------

def test_question_state_is_manual_and_survives_rebuild(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
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

def test_alias_joins_two_spellings_of_one_project(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
    result = json.loads(json.dumps(SUMMARY))
    result["topics"] = ["Posiflora"]
    summary_stub(env, monkeypatch, result)
    run_all(env)

    with closing(env.db.connect()) as conn:
        assert list(env.db.project_sessions(conn)) == ["Posiflora"]
        env.db.set_alias(conn, "posiflora", "Пози флора")
        projects = env.db.project_sessions(conn)
    assert list(projects) == ["Пози флора"]
    assert projects["Пози флора"][0]["session"] == 7


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

    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", shaky)
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


def test_old_transcripts_without_confidence_are_not_flagged(env, monkeypatch):
    """Уверенность слов начали писать в этом этапе. Старые записи не помечаем."""
    upload(env)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)

    with closing(env.db.connect()) as conn:
        conn.execute("UPDATE transcripts SET words=?",
                     (json.dumps([{"w": "23", "s": 1.1, "e": 1.5, "spk": 0}]),))
        conn.commit()
        assert env.db.uncertain_numbers(conn, "xiao-01", 7) == {}


# --- дайджест ------------------------------------------------------------------

def test_digest_counts_without_calling_model(env, monkeypatch):
    upload(env, seconds=60)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)

    with closing(env.db.connect()) as conn:
        day = env.db.digest(conn, 1)
    assert day["sessions"] == 1
    assert day["new_tasks"] == 1
    assert abs(day["minutes"] - 1.0) < 0.01
    assert day["my_open_tasks"] == 0        # «это я» ещё не отмечен
