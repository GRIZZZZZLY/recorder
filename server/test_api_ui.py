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
