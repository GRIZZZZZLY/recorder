"""Проверки JSON-API и отдачи собранного фронтенда."""
import importlib
import json
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from test_pipeline import AUTH, PASSWORD, TOKEN, env, fake_transcribe, run_all, summary_stub, upload  # noqa: F401


@pytest.fixture
def site(env, tmp_path, monkeypatch):
    """Каталог сборки с двумя страницами: главной и страницей сессии."""
    out = tmp_path / "out"
    (out / "session").mkdir(parents=True)
    (out / "index.html").write_text("<!doctype html><title>Сводка</title>", encoding="utf-8")
    (out / "session" / "index.html").write_text("<!doctype html><title>Сессия</title>", encoding="utf-8")
    (out / "_next").mkdir()
    (out / "_next" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("RECORDER_WEB_DIR", str(out))

    import static_site, app as app_module
    importlib.reload(static_site)
    importlib.reload(app_module)
    env.app = app_module.app
    return env


def test_site_serves_index_and_nested_page(site):
    with TestClient(site.app) as client:
        assert client.get("/", auth=AUTH).text.startswith("<!doctype html>")
        assert "Сессия" in client.get("/session/?d=xiao-01&n=7", auth=AUTH).text
        assert client.get("/_next/app.js", auth=AUTH).status_code == 200


def test_login_form_lets_the_owner_in(site):
    """Форма входа: ради неё всё и затевалось — Safari в режиме «на домашний
    экран» окна Basic-авторизации не показывает."""
    with TestClient(site.app) as client:
        assert "Пароль" in client.get("/login").text

        bad = client.post("/login", data={"user": "owner", "password": "wrong", "next": "/"})
        assert bad.status_code == 200 and "Неверное имя или пароль" in bad.text
        assert client.get("/", follow_redirects=False).status_code == 303

        good = client.post("/login", data={"user": "owner", "password": PASSWORD, "next": "/"},
                           follow_redirects=False)
        assert good.status_code == 303 and good.headers["location"] == "/"
        # Метка выдана: дальше сайт открывается без пароля.
        assert client.get("/").status_code == 200
        # Вошедшему форма не нужна — уводим на сайт, а не в тупик.
        assert client.get("/login", follow_redirects=False).status_code == 303


def test_login_does_not_forward_to_a_stranger(site):
    """`next` — только путь внутри сайта: иначе форма входа стала бы удобной
    пересылкой на чужую страницу под нашим адресом."""
    import login as login_module
    assert login_module.safe_next("https://example.org/") == "/"
    assert login_module.safe_next("//example.org/") == "/"
    assert login_module.safe_next("/records/?show=all") == "/records/?show=all"

    with TestClient(site.app) as client:
        answer = client.post("/login",
                             data={"user": "owner", "password": PASSWORD,
                                   "next": "https://example.org/"},
                             follow_redirects=False)
        assert answer.headers["location"] == "/"


def test_site_does_not_serve_files_outside_the_build(site):
    with TestClient(site.app) as client:
        # %2e%2e, а не "..": HTTP-клиент вырезает ".." из пути ещё до отправки,
        # и такой запрос ничего бы не проверял.
        r = client.get("/%2e%2e/%2e%2e/server/db.py", auth=AUTH)
        assert r.status_code in (400, 404) and b"def " not in r.content


def prepared(env, monkeypatch):
    """Сессия 7 с расшифровкой, саммари, задачей и вопросом."""
    upload(env)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)
    return env


def test_digest_gives_day_week_and_recent_sessions(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        body = client.get("/api/ui/digest", auth=AUTH).json()
    assert body["day"]["sessions"] == 1
    assert body["week"]["new_tasks"] == 1
    assert body["recent"][0]["session"] == 7
    assert body["my_tasks"] == []          # «это я» ещё не отмечен
    assert body["has_me"] is False


def test_sessions_list_has_progress_and_state(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        rows = client.get("/api/ui/sessions", auth=AUTH).json()
    assert rows[0]["session"] == 7
    assert rows[0]["parts"] == 1 and rows[0]["done_parts"] == 1
    assert rows[0]["first_part"] == 1
    assert rows[0]["summary_status"] == "done"
    assert rows[0]["day"].startswith("20")


def test_sessions_list_counts_failed_parts(env, monkeypatch):
    """Запись с упавшей частью не должна выглядеть «готово»: карточке нужно
    знать, что кусок звука так и не расшифровался."""
    prepared(env, monkeypatch)
    with closing(env.db.connect()) as conn:
        conn.execute("UPDATE transcripts SET status='failed'")
        conn.commit()
    with TestClient(env.app) as client:
        rows = client.get("/api/ui/sessions", auth=AUTH).json()
    assert rows[0]["failed_parts"] == 1


def test_session_detail_carries_timeline_summary_and_tasks(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        body = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()
    assert body["summary"]["topics"] == ["смета для заказчика"]
    assert body["timeline"][0]["label"] == "Говорящий 1"
    assert body["timeline"][0]["start_abs"] == 1.0
    assert body["parts"][0]["part"] == 1
    assert body["tasks"][0]["text"] == "Выслать смету"
    # Номер говорящего идёт вместе со своей частью: в другой части тот же
    # номер — другой человек.
    assert body["speakers"] == [
        {"part": 1, "speaker": 0, "name": "", "is_me": False, "auto": False},
        {"part": 1, "speaker": 1, "name": "", "is_me": False, "auto": False}]


def test_session_detail_places_each_part_on_the_session_scale(env, monkeypatch):
    """Плеер показывает время разговора и сам переходит на следующую часть:
    ему нужны смещение и длина каждой части на той же шкале, что у реплик."""
    upload(env, part=1)
    upload(env, part=2, marker=1)
    with TestClient(env.app) as client:
        parts = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()["parts"]
    assert parts[0]["offset"] == 0
    assert parts[0]["seconds"] > 0
    assert parts[1]["offset"] == pytest.approx(parts[0]["seconds"])


def test_marks_land_on_session_scale_with_nearest_reply(env, monkeypatch):
    """Метка кнопкой: абсолютное время сессии и реплика, прозвучавшая до неё."""
    upload(env, part=1, marks=[5.5])
    upload(env, part=2, marker=1, marks=[2.0])
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)

    with TestClient(env.app) as client:
        body = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()
        rows = client.get("/api/ui/sessions", auth=AUTH).json()

    first, second = body["marks"]
    # Первая часть — одна секунда звука, значит вторая начинается с 1,0.
    assert (first["start_abs"], second["start_abs"]) == (5.5, 3.0)
    # Источник — последняя реплика до метки и только из её собственной части:
    # реплика соседней части здесь ближе по времени, но брать её нельзя.
    assert first["text"] == "Беру на себя, пришлю в четверг."
    assert first["source"]["part"] == 1
    assert second["text"] == "Ещё надо проверить договор с подрядчиком."
    assert second["source"]["part"] == 2
    assert rows[0]["marks"] == [5.5, 3.0]

    with closing(env.db.connect()) as conn:
        # Модель видит метку строкой на её месте в разговоре.
        assert "[метка владельца]" in env.db.session_transcript(conn, "xiao-01", 7)


def test_speakers_are_saved_as_json(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        response = client.post("/api/ui/sessions/xiao-01/7/speakers", auth=AUTH,
                               json={"names": {"1:0": "Игорь", "1:1": "Мария"},
                                     "me": "1:0"})
        assert response.status_code == 200
        body = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()
    assert body["timeline"][0]["label"] == "Игорь"
    assert body["speakers"][0] == {"part": 1, "speaker": 0, "name": "Игорь", "is_me": True,
                                   "auto": False}
    with closing(env.db.connect()) as conn:
        assert env.db.speaker_names(conn, "xiao-01", 7)[(1, 0)] == {"name": "Игорь",
                                                                    "is_me": True,
                                                                    "auto": False}


def test_recording_day_is_fixed_by_the_owner_and_validated(env, monkeypatch):
    """День разговора правится руками: другого источника даты нет. Мусор и
    будущее не принимаются — выдуманный день сдвинул бы все сроки задач."""
    from datetime import date, timedelta

    prepared(env, monkeypatch)
    spoken = (date.today() - timedelta(days=2)).isoformat()
    ahead = (date.today() + timedelta(days=1)).isoformat()
    with TestClient(env.app) as client:
        assert client.post("/api/ui/sessions/xiao-01/7/day", auth=AUTH,
                           json={"day": "позавчера"}).status_code == 400
        assert client.post("/api/ui/sessions/xiao-01/7/day", auth=AUTH,
                           json={"day": ahead}).status_code == 400

        saved = client.post("/api/ui/sessions/xiao-01/7/day", auth=AUTH, json={"day": spoken})
        assert saved.status_code == 200 and saved.json() == {"day": spoken}
        assert client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()["day"] == spoken
        # Срок «пятница» пересчитан от нового дня, а не от дня выгрузки.
        task = client.get("/api/ui/tasks", auth=AUTH).json()["tasks"][0]
        assert task["due_date"] == env.duedate.parse_due("пятница",
                                                         date.fromisoformat(spoken)).isoformat()

        assert client.post("/api/ui/sessions/xiao-01/7/day", auth=AUTH,
                           json={"day": None}).status_code == 200


def test_status_counts_sessions_whose_summary_row_does_not_exist_yet(env):
    """Запись без строки саммари карточка уже зовёт «обрабатывается»;
    «Состояние» не должно при этом писать «все собраны»."""
    upload(env, part=1)
    with TestClient(env.app) as client:
        status = client.get("/api/ui/status", auth=AUTH).json()
    assert status["summaries_pending"] == 1


SUMMARY_WITH_QUESTION = {
    "summary": "Обсудили смету.",
    "topics": ["смета для заказчика"],
    "decisions": [],
    "facts": [],
    "tasks": [{"text": "Выслать смету", "owner": "Мария", "due": "пятница",
               "time": "0:00:01", "quote": "к пятнице выслать смету заказчику"}],
    "open_questions": [{"text": "Кто смотрит договор", "time": "0:00:05",
                        "quote": "кто смотрит"}],
}


def test_tasks_filters_and_status_change(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        rows = client.get("/api/ui/tasks?show=open", auth=AUTH).json()
        assert rows["has_me"] is False and len(rows["tasks"]) == 1
        task_id = rows["tasks"][0]["task_id"]
        assert rows["tasks"][0]["due_date"]          # «пятница» разобрана
        # source приходит объектом, как в /review, а не строкой JSON из базы
        assert rows["tasks"][0]["source"]["confirmed"] is True

        assert client.post(f"/api/ui/tasks/xiao-01/7/{task_id}", auth=AUTH,
                           json={"status": "done"}).status_code == 200
        assert client.get("/api/ui/tasks?show=open", auth=AUTH).json()["tasks"] == []
        assert len(client.get("/api/ui/tasks?show=all", auth=AUTH).json()["tasks"]) == 1


def test_projects_list_feed_and_alias(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    result = json.loads(json.dumps(SUMMARY_WITH_QUESTION))
    result["topics"] = ["Posiflora"]
    summary_stub(env, monkeypatch, result)
    run_all(env)
    with TestClient(env.app) as client:
        assert client.get("/api/ui/projects", auth=AUTH).json()["projects"][0]["name"] == "Posiflora"
        client.post("/api/ui/projects/aliases", auth=AUTH,
                    json={"alias": "posiflora", "canonical": "Пози флора"})
        listing = client.get("/api/ui/projects", auth=AUTH).json()
        assert listing["projects"][0]["name"] == "Пози флора"
        assert listing["aliases"] == [{"alias": "posiflora", "canonical": "Пози флора"}]
        feed = client.get("/api/ui/projects/Posiflora", auth=AUTH).json()
        assert feed["name"] == "Пози флора" and feed["sessions"][0]["session"] == 7


def test_search_returns_segments_not_html(env, monkeypatch):
    prepared(env, monkeypatch)
    if not env.db.textnorm.available():
        pytest.skip("pymorphy3 не установлен")
    with TestClient(env.app) as client:
        hits = client.get("/api/ui/search?q=сметы", auth=AUTH).json()
    assert hits[0]["session"] == 7
    # Разговор назван темой и днём, а не номером сессии.
    assert hits[0]["topic"] == "смета для заказчика"
    assert hits[0]["day"].startswith("20")
    assert any(part["hit"] for part in hits[0]["segments"])
    assert "".join(part["text"] for part in hits[0]["segments"]) == hits[0]["text"]
    assert "<mark>" not in json.dumps(hits, ensure_ascii=False)


def test_search_finds_summary_notes_too(env, monkeypatch):
    """Слово из задачи саммари находится, даже если его нет в репликах."""
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        notes = client.get("/api/ui/search/notes?q=выслать", auth=AUTH).json()
        assert client.get("/api/ui/search/notes?q=", auth=AUTH).json() == []
    assert notes and notes[0]["kind"] == "задача"
    assert notes[0]["text"] == "Выслать смету"
    assert any(part["hit"] for part in notes[0]["segments"])
    assert notes[0]["topic"] == "смета для заказчика"


def test_password_once_leaves_a_month_long_cookie(env):
    """Safari на телефоне забывает логин Basic при перезапуске, поэтому после
    удачного входа выдаётся подписанная метка, и пароль больше не спрашивают."""
    import auth
    with TestClient(env.app) as client:
        assert client.get("/api/ui/digest", auth=AUTH).status_code == 200
        assert auth.COOKIE_NAME in client.cookies
        # Пароля в запросе нет: пускает метка.
        assert client.get("/api/ui/digest").status_code == 200

        client.cookies.clear()
        client.cookies.set(auth.COOKIE_NAME, f"{int(time.time()) + 3600}.00ff")
        assert client.get("/api/ui/digest").status_code == 401

        stale = int(time.time()) - 1
        client.cookies.clear()
        client.cookies.set(auth.COOKIE_NAME, f"{stale}.{auth._sign(stale)}")
        assert client.get("/api/ui/digest").status_code == 401


def test_cookie_is_marked_secure_behind_nginx(env):
    """uvicorn работает без --proxy-headers и сам видит http, поэтому без разбора
    заголовка от nginx метка уходила бы без флага Secure."""
    with TestClient(env.app) as client:
        forwarded = client.get("/api/ui/digest", auth=AUTH,
                               headers={"X-Forwarded-Proto": "https"})
        assert "Secure" in forwarded.headers["set-cookie"]
        client.cookies.clear()
        assert "Secure" not in client.get("/api/ui/digest", auth=AUTH).headers["set-cookie"]


def test_device_state_survives_uploads_without_headers(env):
    """Прошивка перестала слать заряд — вчерашнее значение остаётся, а не
    затирается неизвестным."""
    upload(env, part=1, extra={"X-Battery": "73", "X-Firmware": "0.3.1"})
    upload(env, part=2)
    with TestClient(env.app) as client:
        devices = client.get("/api/ui/status", auth=AUTH).json()["devices"]
    assert devices[0]["battery"] == 73 and devices[0]["firmware"] == "0.3.1"


def test_failed_part_can_be_returned_to_the_queue(env, monkeypatch):
    prepared(env, monkeypatch)
    with closing(env.db.connect()) as conn:
        conn.execute("UPDATE transcripts SET status='failed', attempts=3,"
                     " last_error='Deepgram лёг', next_try_at=datetime('now','+1 hour')")
        conn.commit()

    with TestClient(env.app) as client:
        assert client.post("/api/ui/parts/xiao-01/7/1/retry", auth=AUTH).status_code == 200
        with closing(env.db.connect()) as conn:
            row = conn.execute("SELECT status, attempts, last_error FROM transcripts").fetchone()
        assert (row["status"], row["attempts"], row["last_error"]) == ("pending", 0, None)

        # Расшифрованную часть повторять нечего: иначе кнопка молча стирала бы
        # готовый текст и отправляла запись в Deepgram второй раз за деньги.
        conn = env.db.connect()
        with closing(conn):
            conn.execute("UPDATE transcripts SET status='done'")
            conn.commit()
        assert client.post("/api/ui/parts/xiao-01/7/1/retry", auth=AUTH).status_code == 404
        assert client.post("/api/ui/parts/xiao-01/7/9/retry", auth=AUTH).status_code == 404


def test_stuck_summary_can_be_returned_to_the_queue(env, monkeypatch):
    prepared(env, monkeypatch)
    with closing(env.db.connect()) as conn:
        conn.execute("UPDATE summaries SET status='failed', last_error='claude не ответил'")
        conn.commit()
    with TestClient(env.app) as client:
        assert client.post("/api/ui/sessions/xiao-01/7/retry-summary", auth=AUTH).status_code == 200
        assert client.post("/api/ui/sessions/xiao-01/7/retry-summary", auth=AUTH).status_code == 200
        with closing(env.db.connect()) as conn:
            conn.execute("UPDATE summaries SET status='done'")
            conn.commit()
        assert client.post("/api/ui/sessions/xiao-01/7/retry-summary", auth=AUTH).status_code == 404


def test_session_can_be_deleted_with_its_files(env, monkeypatch):
    """Удаление стирает запись целиком — строки всех таблиц и звук, — а соседнюю
    сессию не трогает. Поиск по удалённому разговору ничего не находит."""
    prepared(env, monkeypatch)
    assert upload(env, session=8).status_code == 201
    run_all(env)
    audio = env.db.AUDIO_DIR
    (audio / "xiao-01_S0007_P02.deadbeef.100.partial").write_bytes(b"x")

    with TestClient(env.app) as client:
        assert client.delete("/api/ui/sessions/xiao-01/7").status_code == 401
        assert client.delete("/api/ui/sessions/xiao-01/7", auth=AUTH).status_code == 200
        assert client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).status_code == 404
        assert client.get("/api/ui/sessions/xiao-01/8", auth=AUTH).status_code == 200
        found = client.get("/api/ui/search", params={"q": "смета"}, auth=AUTH).json()
        assert found and {hit["session"] for hit in found} == {8}
        assert client.delete("/api/ui/sessions/xiao-01/7", auth=AUTH).status_code == 404

    with closing(env.db.connect()) as conn:
        for table in env.db.SESSION_TABLES:
            left = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE session=7").fetchone()[0]
            assert left == 0, table
    assert not list(audio.glob("xiao-01_S0007_*"))
    assert list(audio.glob("xiao-01_S0008_*"))


def test_worker_does_not_resurrect_a_deleted_session(env, monkeypatch):
    """Воркер мог взять часть до удаления и закончить после: его запись не
    должна вернуть разговор в поиск и задачи."""
    prepared(env, monkeypatch)
    with closing(env.db.connect()) as conn:
        env.db.delete_session(conn, "xiao-01", 7)
        env.db.save_transcript(conn, ("xiao-01", 7, 1), "смета", "[]",
                               [{"text": "смета"}], 1.0)
        env.db.sync_tasks(conn, "xiao-01", 7, [{"text": "Выслать смету"}])
        assert not env.db.finish_archive(conn, ("xiao-01", 7, 1), "x.flac")
        assert conn.execute("SELECT COUNT(*) FROM transcript_fts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
