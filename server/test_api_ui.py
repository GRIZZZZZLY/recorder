"""Проверки JSON-API и отдачи собранного фронтенда."""
import importlib
import json
import os
import time
from contextlib import closing
from pathlib import Path

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


def test_site_serves_next_segment_prefetch(site):
    """Next просит /records/__next.records.__PAGE__.txt, а сборка кладёт файл в
    records/__next.records/__PAGE__.txt; раньше каждая предзагрузка давала 404."""
    nested = Path(os.environ["RECORDER_WEB_DIR"]) / "records" / "__next.records"
    nested.mkdir(parents=True)
    (nested / "__PAGE__.txt").write_text("rsc", encoding="utf-8")
    with TestClient(site.app) as client:
        answer = client.get("/records/__next.records.__PAGE__.txt?_rsc=x", auth=AUTH)
    assert answer.status_code == 200 and answer.text == "rsc"


def test_site_answers_head_requests(site):
    """Next.js проверяет ссылки HEAD-запросом; раньше каждый давал 405."""
    with TestClient(site.app) as client:
        assert client.head("/session/", auth=AUTH).status_code == 200


def test_site_is_closed_by_the_same_password(site):
    """Ни страница, ни файлы сборки не отдаются без пароля: содержимого в
    ответе нет, вместо него переход на форму входа."""
    with TestClient(site.app) as client:
        for path, attempt in (("/", {"auth": ("owner", "wrong")}), ("/_next/app.js", {})):
            answer = client.get(path, follow_redirects=False, **attempt)
            assert answer.status_code == 303
            assert answer.headers["location"].startswith("/login")
            assert "Сводка" not in answer.text and "console.log" not in answer.text
        # JSON-API остаётся при отказе 401: странице нужна форма, фоновому
        # запросу — честная ошибка, а не HTML формы вместо данных.
        assert client.get("/api/ui/digest").status_code == 401


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


def test_site_without_build_says_so(env, monkeypatch, tmp_path):
    monkeypatch.setenv("RECORDER_WEB_DIR", str(tmp_path / "нет-такого"))
    import static_site, app as app_module
    importlib.reload(static_site)
    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        response = client.get("/", auth=AUTH)
        assert response.status_code == 503
        assert "не собран" in response.text


def test_site_does_not_serve_files_outside_the_build(site):
    with TestClient(site.app) as client:
        assert client.get("/../../server/db.py", auth=AUTH).status_code in (400, 404)


def test_resolve_returns_none_instead_of_crashing_on_value_error(site, monkeypatch):
    """Путь со встроенным NUL валит Path.resolve() с ValueError (на POSIX), не
    только с OSError — раньше это было необработанным 500. Windows этот кейс
    не воспроизводит напрямую (resolve() молча пропускает NUL), поэтому
    ValueError вызывается принудительно — так проверка не зависит от ОС."""
    import static_site
    from pathlib import Path

    real_resolve = Path.resolve

    def flaky_resolve(self, *args, **kwargs):
        if self.name == "boom":
            raise ValueError("embedded null byte")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", flaky_resolve)
    assert static_site.resolve("boom") is None


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


def test_session_rows_seconds_match_per_part_clamp_like_timeline(env):
    """session_rows раньше клэмпил один раз на сумму: max(0, SUM(bytes) - 44*parts).
    part_offsets (шкала реплик) клэмпит каждую часть отдельно и потом суммирует.
    На части короче WAV_HEADER_BYTES (битые/обрезанные, есть recovered/failed для
    таких) формулы расходятся: список сессий и шкала должны показывать одно и то же
    число, поэтому seconds обязан считаться так же поэлементно."""
    with closing(env.db.connect()) as conn:
        conn.execute(
            "INSERT INTO parts (device_id, session, part, bytes, crc32, audio_path)"
            " VALUES ('xiao-01', 7, 1, 20, '00000000', 'x')")
        conn.execute(
            "INSERT INTO parts (device_id, session, part, bytes, crc32, audio_path)"
            " VALUES ('xiao-01', 7, 2, 32044, '00000000', 'y')")
        conn.commit()
        row = env.db.session_rows(conn)[0]

    per_part_total = (max(0, 20 - env.db.WAV_HEADER_BYTES)
                       + max(0, 32044 - env.db.WAV_HEADER_BYTES))
    assert row["seconds"] == pytest.approx(per_part_total / env.db.BYTES_PER_SECOND)

    old_formula = max(0, (20 + 32044) - env.db.WAV_HEADER_BYTES * 2) / env.db.BYTES_PER_SECOND
    assert row["seconds"] != pytest.approx(old_formula)


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
    assert body["speakers"] == [{"part": 1, "speaker": 0, "name": "", "is_me": False},
                                {"part": 1, "speaker": 1, "name": "", "is_me": False}]


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


def test_session_detail_404_for_unknown_session(env):
    with TestClient(env.app) as client:
        assert client.get("/api/ui/sessions/xiao-01/999", auth=AUTH).status_code == 404


def test_speakers_are_saved_as_json(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        response = client.post("/api/ui/sessions/xiao-01/7/speakers", auth=AUTH,
                               json={"names": {"1:0": "Игорь", "1:1": "Мария"},
                                     "me": "1:0"})
        assert response.status_code == 200
        body = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()
    assert body["timeline"][0]["label"] == "Игорь"
    assert body["speakers"][0] == {"part": 1, "speaker": 0, "name": "Игорь", "is_me": True}
    with closing(env.db.connect()) as conn:
        assert env.db.speaker_names(conn, "xiao-01", 7)[(1, 0)] == {"name": "Игорь",
                                                                    "is_me": True}


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


def test_tasks_carry_the_tape_of_their_record(env, monkeypatch):
    """У строки задачи — волна её записи и длина: интерфейс ставит засечку
    места, где задача прозвучала, прямо на плёнке."""
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        task = client.get("/api/ui/tasks", auth=AUTH).json()["tasks"][0]
    assert task["tape"]["seconds"] > 0
    assert isinstance(task["tape"]["envelope"], list)


def test_tasks_name_their_conversation(env, monkeypatch):
    """Строка задачи говорит, из какого она разговора: тема и день."""
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        task = client.get("/api/ui/tasks", auth=AUTH).json()["tasks"][0]
    assert task["topic"] == "смета для заказчика"
    assert task["day"].startswith("20")


def test_api_requires_password(env):
    with TestClient(env.app) as client:
        assert client.get("/api/ui/digest").status_code == 401


def test_v1_sessions_requires_owner_password(env):
    """GET /api/v1/sessions отдаёт список загрузок и раньше был открыт всем:
    без Depends(owner) размеры и время выгрузки видел любой, кто знает адрес.
    Прошивку это не задевает — она вызывает только POST /api/v1/upload."""
    with TestClient(env.app) as client:
        assert client.get("/api/v1/sessions").status_code == 401
        assert client.get("/api/v1/sessions", auth=AUTH).status_code == 200


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


def test_task_status_is_validated(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        task_id = client.get("/api/ui/tasks", auth=AUTH).json()["tasks"][0]["task_id"]
        assert client.post(f"/api/ui/tasks/xiao-01/7/{task_id}", auth=AUTH,
                           json={"status": "мусор"}).status_code == 422


def test_questions_state_roundtrip(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    result = json.loads(json.dumps(SUMMARY_WITH_QUESTION))
    summary_stub(env, monkeypatch, result)
    run_all(env)
    with TestClient(env.app) as client:
        rows = client.get("/api/ui/questions?show=open", auth=AUTH).json()
        assert len(rows) == 1
        question_id = rows[0]["question_id"]
        client.post(f"/api/ui/questions/xiao-01/7/{question_id}", auth=AUTH,
                    json={"state": "answered"})
        assert client.get("/api/ui/questions?show=open", auth=AUTH).json() == []
        assert len(client.get("/api/ui/questions?show=all", auth=AUTH).json()) == 1


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


def test_search_without_query_is_empty(env):
    with TestClient(env.app) as client:
        assert client.get("/api/ui/search?q=", auth=AUTH).json() == []


def test_project_aliases_route_does_not_collide_with_project_name_route(env, monkeypatch):
    """/projects/aliases (POST) не должен уходить в обработчик /projects/{name}
    (GET) с именем проекта «aliases» — методы разные, но порядок объявления
    маршрутов проверяем явно, а не полагаемся на то, как сейчас матчит Starlette."""
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        response = client.post("/api/ui/projects/aliases", auth=AUTH,
                               json={"alias": "x", "canonical": "Y"})
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        with closing(env.db.connect()) as conn:
            assert env.db.alias_map(conn)["x"] == "Y"


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


def test_review_and_status_are_exposed(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        review = client.get("/api/ui/review", auth=AUTH).json()
        status = client.get("/api/ui/status", auth=AUTH).json()
    assert isinstance(review, list)
    assert status["transcripts_pending"] == 0
    assert "failed_parts" in status


def test_device_state_comes_from_upload_headers(env):
    """Заряд и прошивка приходят заголовками выгрузки и видны в состоянии."""
    upload(env, extra={"X-Battery": "73", "X-Firmware": "0.3.1"})
    with TestClient(env.app) as client:
        devices = client.get("/api/ui/status", auth=AUTH).json()["devices"]
    assert devices == [{"device_id": "xiao-01", "battery": 73, "firmware": "0.3.1",
                        "updated_at": devices[0]["updated_at"]}]


def test_device_state_survives_uploads_without_headers(env):
    """Прошивка перестала слать заряд — вчерашнее значение остаётся, а не
    затирается неизвестным."""
    upload(env, part=1, extra={"X-Battery": "73", "X-Firmware": "0.3.1"})
    upload(env, part=2)
    with TestClient(env.app) as client:
        devices = client.get("/api/ui/status", auth=AUTH).json()["devices"]
    assert devices[0]["battery"] == 73 and devices[0]["firmware"] == "0.3.1"


def test_broken_device_headers_are_rejected(env):
    for header, value in (("X-Battery", "101"), ("X-Battery", "full"),
                          ("X-Firmware", "0.3.1; rm -rf /")):
        assert upload(env, extra={header: value}).status_code == 400


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


def test_sessions_list_carries_named_speakers(env, monkeypatch):
    prepared(env, monkeypatch)
    with closing(env.db.connect()) as conn:
        env.db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", True)
        env.db.set_speaker(conn, "xiao-01", 7, 1, 1, "", False)
        conn.commit()
    with TestClient(env.app) as client:
        rows = client.get("/api/ui/sessions", auth=AUTH).json()
    # Безымянный говорящий в список не попадает: «Говорящий 2» искать не помогает.
    assert rows[0]["speakers"] == ["Игорь"]


def test_projects_count_open_tasks(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        projects = client.get("/api/ui/projects", auth=AUTH).json()["projects"]
    assert projects and all("open_tasks" in item for item in projects)
    assert projects[0]["open_tasks"] >= 1
