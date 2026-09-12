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
