"""Ссылка на одну запись для другого человека.

Гость приходит без пароля и без метки входа, поэтому каждый его запрос идёт
своим чистым клиентом. Метка владельца, оставшаяся в общем клиенте, открыла
бы «гостю» всё, и проверки перестали бы что-то значить.
"""
import os
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_api_ui import prepared, site  # noqa: F401
from test_pipeline import AUTH, env, upload  # noqa: F401


def as_owner(env, method, path):
    with TestClient(env.app) as client:
        return client.request(method, path, auth=AUTH)


def as_guest(env, path, **kwargs):
    with TestClient(env.app) as client:
        return client.get(path, follow_redirects=False, **kwargs)


def share(env, session=7):
    answer = as_owner(env, "POST", f"/api/ui/sessions/xiao-01/{session}/shares")
    assert answer.status_code == 200, answer.text
    return answer.json()["token"]


def tokens(env, session=7):
    listed = as_owner(env, "GET", f"/api/ui/sessions/xiao-01/{session}/shares")
    assert listed.status_code == 200, listed.text
    return [row["token"] for row in listed.json()]


def test_owner_creates_lists_and_closes_links(env):
    upload(env)
    first, second = share(env), share(env)
    assert len(first) >= 22 and first != second
    assert tokens(env) == [second, first]

    assert as_owner(env, "DELETE", f"/api/ui/shares/{first}").status_code == 200
    # Повторное закрытие — не ошибка: владелец мог нажать дважды.
    assert as_owner(env, "DELETE", f"/api/ui/shares/{first}").status_code == 200
    assert tokens(env) == [second]


def test_link_management_needs_the_owner(env):
    upload(env)
    token = share(env)
    with TestClient(env.app) as client:
        assert client.post("/api/ui/sessions/xiao-01/7/shares").status_code == 401
        assert client.get("/api/ui/sessions/xiao-01/7/shares").status_code == 401
        assert client.delete(f"/api/ui/shares/{token}").status_code == 401
    assert tokens(env) == [token]


GUEST_KEYS = {"day", "first_at", "parts", "summary", "timeline", "speakers",
              "voice_groups", "short_keys"}


def test_guest_sees_the_record_and_nothing_else(env, monkeypatch):
    prepared(env, monkeypatch)
    answer = as_guest(env, f"/api/share/{share(env)}")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    # Ровно разрешённое: ни задач с отметками владельца, ни меток, ни
    # устройства, ни ошибок обработки.
    assert set(body) == GUEST_KEYS
    assert set(body["parts"][0]) == {"part", "offset", "seconds", "envelope"}
    assert set(body["speakers"][0]) == {"part", "speaker", "name", "auto"}
    assert set(body["timeline"][0]) <= {"part", "idx", "start", "end", "start_abs",
                                         "speaker", "label", "by_voice", "text"}
    assert set(body["summary"]) <= {"summary", "topics", "decisions", "facts", "tasks",
                                     "open_questions"}
    assert body["summary"]["topics"] == ["смета для заказчика"]
    assert "смету" in body["timeline"][0]["text"]
    assert answer.headers["cache-control"] == "no-store"
    assert answer.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in answer.headers["x-robots-tag"]


def test_dead_links_all_answer_the_same(env, monkeypatch):
    prepared(env, monkeypatch)
    upload(env, session=8, marker=1)
    expired, closed, deleted = share(env), share(env), share(env, session=8)
    with closing(env.db.connect()) as conn:
        conn.execute("UPDATE shares SET expires_at=datetime('now', '-1 second') WHERE token=?",
                     (expired,))
        conn.commit()
    assert as_owner(env, "DELETE", f"/api/ui/shares/{closed}").status_code == 200
    assert as_owner(env, "DELETE", "/api/ui/sessions/xiao-01/8").status_code == 200

    answers = [as_guest(env, f"/api/share/{token}")
               for token in (expired, closed, deleted, "no-such-link")]
    assert [answer.status_code for answer in answers] == [404] * 4
    # По ответу нельзя понять, что за ключом было.
    assert len({answer.text for answer in answers}) == 1
    sounds = [as_guest(env, f"/share/{token}/audio/1") for token in (expired, closed, deleted)]
    assert [answer.status_code for answer in sounds] == [404] * 3
    assert len({answer.text for answer in sounds}) == 1
    for answer in answers + sounds:
        assert answer.headers["cache-control"] == "no-store"
        assert answer.headers["referrer-policy"] == "no-referrer"
    # Истёкшая в списке владельца не висит: она уже ничего не открывает.
    assert tokens(env) == []


def test_link_plays_only_its_own_record(env):
    upload(env)
    upload(env, session=8, marker=1)
    token = share(env)
    own = as_guest(env, f"/share/{token}/audio/1", headers={"Range": "bytes=0-43"})
    assert own.status_code == 206 and own.content[:4] == b"RIFF"
    assert own.headers["referrer-policy"] == "no-referrer"
    # Части 2 у записи 7 нет. Часть 1 записи 8 есть, но адрес ссылки не умеет
    # назвать другую запись: номер записи берётся из ключа.
    assert as_guest(env, f"/share/{token}/audio/2").status_code == 404
    assert as_guest(env, "/audio/xiao-01/8/1").status_code == 401


@pytest.fixture
def guest_site(site):
    """Сборка из фикстуры `site` плюс страница гостя, раздел владельца и значок."""
    out = Path(os.environ["RECORDER_WEB_DIR"])
    for page in ("shared", "records"):
        (out / page).mkdir(exist_ok=True)
        (out / page / "index.html").write_text(f"<!doctype html><title>{page}</title>",
                                               encoding="utf-8")
    (out / "icon.svg").write_text("<svg/>", encoding="utf-8")
    return site


def test_only_the_guest_page_opens_without_password(guest_site):
    for path in ("/shared/", "/shared/?t=abc", "/_next/app.js", "/icon.svg"):
        answer = as_guest(guest_site, path)
        assert answer.status_code == 200, path
        assert answer.headers["referrer-policy"] == "no-referrer", path
        assert "noindex" in answer.headers["x-robots-tag"], path

    for path in ("/", "/records/", "/session/", "/shared/%2e%2e/records/",
                 "/_next/%2e%2e/index.html", "/nothing-here.txt"):
        answer = as_guest(guest_site, path)
        assert answer.status_code == 303, path
        assert answer.headers["location"].startswith("/login"), path
        assert "<title>" not in answer.text, path

    for path in ("/api/ui/sessions", "/audio/xiao-01/7/1", "/api/v1/sessions"):
        assert as_guest(guest_site, path).status_code == 401, path


def test_public_is_judged_by_the_resolved_path(guest_site):
    """Решает путь после разрешения, а не строка запроса: клиент может и не
    свернуть `..` сам, а `/shared/../records/` — это раздел владельца."""
    import static_site
    assert static_site.public(static_site.resolve("shared/index.html"))
    assert static_site.public(static_site.resolve("_next/app.js"))
    assert not static_site.public(static_site.resolve("shared/../records/index.html"))
    assert not static_site.public(static_site.resolve("_next/../index.html"))
    assert not static_site.public(static_site.resolve("index.html"))
