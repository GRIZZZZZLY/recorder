"""Ссылка на одну запись для другого человека.

Гость приходит без пароля и без метки входа, поэтому каждый его запрос идёт
своим чистым клиентом. Метка владельца, оставшаяся в общем клиенте, открыла
бы «гостю» всё, и проверки перестали бы что-то значить.
"""
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

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


def test_link_lives_seven_days(env):
    upload(env)
    share(env)
    with closing(env.db.connect()) as conn:
        days = conn.execute(
            "SELECT julianday(expires_at) - julianday(created_at) FROM shares").fetchone()[0]
    assert days == pytest.approx(env.db.SHARE_DAYS, abs=1e-6)
    assert env.db.SHARE_DAYS == 7


def test_no_link_for_a_missing_record(env):
    assert as_owner(env, "POST", "/api/ui/sessions/xiao-01/99/shares").status_code == 404


def test_link_management_needs_the_owner(env):
    upload(env)
    token = share(env)
    with TestClient(env.app) as client:
        assert client.post("/api/ui/sessions/xiao-01/7/shares").status_code == 401
        assert client.get("/api/ui/sessions/xiao-01/7/shares").status_code == 401
        assert client.delete(f"/api/ui/shares/{token}").status_code == 401
    assert tokens(env) == [token]


def test_deleting_the_record_deletes_its_links(env):
    upload(env)
    share(env)
    assert as_owner(env, "DELETE", "/api/ui/sessions/xiao-01/7").status_code == 200
    with closing(env.db.connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM shares").fetchone()[0] == 0
