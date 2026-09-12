"""Проверки JSON-API и отдачи собранного фронтенда."""
import importlib
import json
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


def test_site_is_closed_by_the_same_password(site):
    with TestClient(site.app) as client:
        assert client.get("/", auth=("owner", "wrong")).status_code == 401
        assert client.get("/_next/app.js").status_code == 401


def test_site_without_build_says_so_and_keeps_legacy(env, monkeypatch, tmp_path):
    monkeypatch.setenv("RECORDER_WEB_DIR", str(tmp_path / "нет-такого"))
    import static_site, app as app_module
    importlib.reload(static_site)
    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        response = client.get("/", auth=AUTH)
        assert response.status_code == 503
        assert "не собран" in response.text
        assert client.get("/legacy/", auth=AUTH).status_code == 200


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
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
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
    assert rows[0]["summary_status"] == "done"
    assert rows[0]["day"].startswith("20")


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
    assert body["speakers"] == [{"speaker": 0, "name": "", "is_me": False},
                                {"speaker": 1, "name": "", "is_me": False}]


def test_session_detail_404_for_unknown_session(env):
    with TestClient(env.app) as client:
        assert client.get("/api/ui/sessions/xiao-01/999", auth=AUTH).status_code == 404


def test_speakers_are_saved_as_json(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        response = client.post("/api/ui/sessions/xiao-01/7/speakers", auth=AUTH,
                               json={"names": {"0": "Игорь", "1": "Мария"}, "me": 0})
        assert response.status_code == 200
        body = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()
    assert body["timeline"][0]["label"] == "Игорь"
    with closing(env.db.connect()) as conn:
        assert env.db.speaker_names(conn, "xiao-01", 7)[0] == {"name": "Игорь", "is_me": True}


def test_api_requires_password(env):
    with TestClient(env.app) as client:
        assert client.get("/api/ui/digest").status_code == 401


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
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
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
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
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
    assert any(part["hit"] for part in hits[0]["segments"])
    assert "".join(part["text"] for part in hits[0]["segments"]) == hits[0]["text"]
    assert "<mark>" not in json.dumps(hits, ensure_ascii=False)


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


def test_review_and_status_are_exposed(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        review = client.get("/api/ui/review", auth=AUTH).json()
        status = client.get("/api/ui/status", auth=AUTH).json()
    assert isinstance(review, list)
    assert status["transcripts_pending"] == 0
    assert "failed_parts" in status
