# Ссылка на запись для гостя — план работ

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** владелец выдаёт другому человеку ссылку на одну запись; по ней без пароля слышен звук и видны расшифровка и изложение, 7 дней или пока владелец не закроет доступ.

**Architecture:** таблица `shares` в SQLite хранит ключ → запись и срок. Новый модуль `server/share.py` отдаёт без пароля JSON одной записи (только разрешённые поля) и её звук. `static_site.py` пускает без пароля только страницу `/shared/`, `/_next/` и значки. На сайте — страница `/shared/` из готовых компонентов (плеер, расшифровка, пункты изложения) и блок «Поделиться» на странице записи владельца.

**Tech Stack:** FastAPI + SQLite (Python 3), pytest; Next.js 16 со статическим экспортом, React 19, Tailwind 4.

**Spec:** `docs/superpowers/specs/2026-10-08-share-link-design.md`

## Global Constraints

- Срок ссылки: `SHARE_DAYS = 7`, константа в `server/db.py`.
- Ключ: `secrets.token_urlsafe(16)` (22 знака, 128 бит), хранится как есть.
- Без пароля открыты ровно: `shared/…` и `_next/…` внутри сборки, `icon.svg`, `icon-192.png`, `icon-512.png`, `apple-touch-icon.png`, `GET /api/share/{token}`, `GET /share/{token}/audio/{part}`. Всё остальное — под паролем.
- Неизвестный, истёкший, закрытый ключ и ключ удалённой записи — один и тот же ответ 404 `{"detail": "ссылка не работает"}`.
- Гостевые ответы: `Referrer-Policy: no-referrer`, `X-Robots-Tag: noindex, nofollow`; JSON ещё `Cache-Control: no-store`.
- Гостю отдаются только поля: `day`, `first_at`, `parts[part, offset, seconds, envelope]`, `summary[summary, topics, decisions, facts, tasks, open_questions]`, `timeline[part, idx, start, end, start_abs, speaker, label, text]`, `speakers[part, speaker, name, auto]`, `voice_groups`, `short_keys`.
- Сервер без `RECORDER_OWNER_PASSWORD` не отдаёт гостю ничего (503), как и владельцу.
- Код и комментарии — в стиле репозитория: комментарии по-русски, объясняют «почему».
- Новых зависимостей нет ни на сервере, ни на сайте.

## Review Focus

1. Ключ пустой, мусорный или длиной в несколько килобайт → сервер отвечает тем же 404, а не 500; страница гостя говорит «Ссылка больше не работает» и не показывает ссылку на вход. Тест — Task 2 (`test_odd_keys_are_just_dead_links`), живая проверка — Task 7.
2. Запись, у которой ещё нет изложения или расшифровки → гость получает 200 с `summary: null` и пустой `timeline`, страница показывает плеер и «Расшифровки пока нет». Тест — Task 2 (`test_record_without_summary_is_still_shared`).
3. Владелец открывает свою же ссылку в браузере, где он вошёл → та же страница гостя: без меню разделов, без задач, без запроса `/digest`. Живая проверка — Task 7.
4. Телефон, ширина 390 px → плеер стоит у нижнего края экрана, а не висит на 80 px выше пустоты (у гостя нет нижней панели разделов). Живая проверка со снимком — Task 7.
5. Тестовый клиент, который хоть раз ходил с паролем владельца, хранит метку входа, и «гость» на нём видит всё → в тестах гость и владелец всегда ходят разными клиентами (`as_guest` / `as_owner`), Task 1.

---

### Task 1: Хранение ссылок и управление ими для владельца

**Files:**
- Modify: `server/db.py` (импорт `secrets`; таблица в `SCHEMA` после `project_aliases`; `SESSION_TABLES` около строки 846; новые функции после `session_exists`)
- Modify: `server/api_ui.py` (три маршрута после `delete_session`, около строки 181)
- Create: `server/test_share.py`

**Interfaces:**
- Produces: `db.SHARE_DAYS: int`; `db.create_share(conn, device_id: str, session: int) -> dict{token, created_at, expires_at}`; `db.session_shares(conn, device_id, session) -> list[dict{token, created_at, expires_at}]` (живые, новые первыми); `db.revoke_share(conn, token: str) -> None`.
- Produces (HTTP, под паролем): `POST /api/ui/sessions/{device_id}/{session}/shares` → 200 `{token, created_at, expires_at}` или 404; `GET /api/ui/sessions/{device_id}/{session}/shares` → 200 список; `DELETE /api/ui/shares/{token}` → 200 `{"ok": true}`.
- Produces (тесты): помощники `as_owner(env, method, path)`, `as_guest(env, path, **kwargs)`, `share(env, session=7) -> str` в `server/test_share.py`.

- [ ] **Step 1: Написать падающие тесты**

Создать `server/test_share.py`:

```python
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
```

- [ ] **Step 2: Запустить — должны упасть**

Run: `cd server && python -m pytest test_share.py -q`
Expected: FAIL — `POST /api/ui/sessions/xiao-01/7/shares` отвечает 405 (маршрута нет), `assert answer.status_code == 200`.

- [ ] **Step 3: Таблица и функции в `server/db.py`**

В импорты (рядом с `import re`):

```python
import secrets
```

В `SCHEMA` сразу после таблицы `project_aliases`:

```sql
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
```

`SESSION_TABLES` — `shares` перед `parts` (`parts` последней: по ней `session_exists` узнаёт, есть ли запись):

```python
SESSION_TABLES = ("transcripts", "summaries", "speaker_names", "voice_prints", "tasks",
                  "question_states", "marks", "recording_days", "transcript_fts", "shares",
                  "parts")
```

После функции `session_exists`:

```python
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
```

- [ ] **Step 4: Маршруты владельца в `server/api_ui.py`**

Сразу после функции `delete_session`:

```python
@router.post("/sessions/{device_id}/{session}/shares")
def create_share(device_id: str, session: int, _=Depends(owner)):
    """Ссылка для другого человека: слушать и читать эту запись, db.SHARE_DAYS дней."""
    with closing(db.connect()) as conn:
        if not db.session_exists(conn, device_id, session):
            raise HTTPException(404, "нет такой сессии")
        return db.create_share(conn, device_id, session)


@router.get("/sessions/{device_id}/{session}/shares")
def list_shares(device_id: str, session: int, _=Depends(owner)):
    with closing(db.connect()) as conn:
        return db.session_shares(conn, device_id, session)


@router.delete("/shares/{token}")
def close_share(token: str, _=Depends(owner)):
    """Закрыть доступ. Уже закрытая ссылка — тоже успех: итог тот же.
    Ответ — JSON, а не пустой 204: apiDelete на сайте читает тело."""
    with closing(db.connect()) as conn:
        db.revoke_share(conn, token)
    return {"ok": True}
```

- [ ] **Step 5: Запустить — должны пройти, и старые тоже**

Run: `cd server && python -m pytest test_share.py test_api_ui.py -q`
Expected: PASS (5 новых + 44 старых). `test_session_can_be_deleted_with_its_files` теперь проверяет и `shares`: таблица в `SESSION_TABLES`.

- [ ] **Step 6: Commit**

```bash
git add server/db.py server/api_ui.py server/test_share.py
git commit -m "feat(server): owner can issue, list and close share links"
```

---

### Task 2: Запись и звук по ссылке — для гостя без пароля

**Files:**
- Modify: `server/db.py` (функция `shared_session` после `revoke_share`)
- Modify: `server/api_ui.py:81-160` (тело `session_detail` → функция `session_view`)
- Modify: `server/audio.py:68-80` (тело `audio` → функция `serve`)
- Create: `server/share.py`
- Modify: `server/app.py` (импорт и подключение роутера перед `static_site`)
- Modify: `server/README_RU.md:31` (строка про `share.py` в таблице модулей)
- Modify: `server/test_share.py`

**Interfaces:**
- Consumes: `db.create_share`, `db.revoke_share`, помощники тестов из Task 1.
- Produces: `db.shared_session(conn, token: str) -> tuple[str, int] | None`; `api_ui.session_view(conn, device_id: str, session: int) -> dict | None` (тот же словарь, что отдавал `session_detail`); `audio.serve(device_id: str, session: int, part: int, headers: dict | None = None) -> FileResponse`; `share.HEADERS: dict[str, str]`; `share.guest_view(view: dict) -> dict`.
- Produces (HTTP, без пароля): `GET /api/share/{token}` → 200 JSON (поля из Global Constraints) или 404; `GET /share/{token}/audio/{part}` → 200/206 звук или 404.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `server/test_share.py`. Импорт `prepared` — в начало файла, к остальным:

```python
from test_api_ui import prepared  # noqa: F401
```

Тесты в конец файла:

```python
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
                                         "speaker", "label", "text"}
    assert set(body["summary"]) <= {"summary", "topics", "decisions", "facts", "tasks",
                                     "open_questions"}
    assert body["summary"]["topics"] == ["смета для заказчика"]
    assert "смету" in body["timeline"][0]["text"]
    assert answer.headers["cache-control"] == "no-store"
    assert answer.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in answer.headers["x-robots-tag"]


def test_record_without_summary_is_still_shared(env):
    """Изложение ещё не готово — гость всё равно слушает запись."""
    upload(env)
    body = as_guest(env, f"/api/share/{share(env)}").json()
    assert body["summary"] is None and body["timeline"] == []
    assert body["parts"][0]["part"] == 1


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
    # Истёкшая в списке владельца не висит: она уже ничего не открывает.
    assert tokens(env) == []


def test_odd_keys_are_just_dead_links(env):
    upload(env)
    share(env)
    # «..» здесь не годится: клиент свернул бы адрес в /api/ и проверял бы не то.
    for token in ("x" * 5000, "%00", "' OR 1=1 --"):
        assert as_guest(env, f"/api/share/{token}").status_code == 404, token[:20]


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


def test_unclosed_server_shares_nothing(env, monkeypatch):
    """Сервер без пароля владельца не отдаёт разговоров никому — и гостю тоже."""
    upload(env)
    token = share(env)
    import auth
    monkeypatch.setattr(auth, "OWNER_PASSWORD", "")
    assert as_guest(env, f"/api/share/{token}").status_code == 503
    assert as_guest(env, f"/share/{token}/audio/1").status_code == 503
```

- [ ] **Step 2: Запустить — должны упасть**

Run: `cd server && python -m pytest test_share.py -q`
Expected: FAIL — новые тесты получают от `/api/share/...` переадресацию 303 на вход (маршрута нет, ловит catch-all сайта) вместо 200/404.

- [ ] **Step 3: `db.shared_session` в `server/db.py`**

После `revoke_share`:

```python
def shared_session(conn, token):
    """(устройство, сессия) живой ссылки или None. Неизвестная, истёкшая и
    закрытая ссылка неразличимы: гостю незачем знать, какая из них его."""
    row = conn.execute(
        "SELECT device_id, session FROM shares WHERE token=? AND expires_at > datetime('now')",
        (token,),
    ).fetchone()
    return (row["device_id"], row["session"]) if row else None
```

- [ ] **Step 4: `session_view` в `server/api_ui.py`**

Заменить функцию `session_detail` целиком на две. Тело `session_view` — бывшее тело `session_detail` без изменений по смыслу: `with` убирается (соединение приходит снаружи), `raise HTTPException(404, ...)` заменяется на `return None`, разбор `summary` остаётся в той же функции.

```python
@router.get("/sessions/{device_id}/{session}")
def session_detail(device_id: str, session: int, _=Depends(owner)):
    with closing(db.connect()) as conn:
        view = session_view(conn, device_id, session)
    if view is None:
        raise HTTPException(404, "нет такой сессии")
    return view


def session_view(conn, device_id, session):
    """Всё о записи для её страницы. None — записи нет. Ссылка для гостя
    (share.py) берёт отсюда же и оставляет только разрешённые поля: владелец
    и гость видят одни и те же части, реплики и имена, посчитанные одним кодом."""
    parts = conn.execute(
        "SELECT p.part, p.recovered, p.received_at, p.envelope, p.bytes, p.bytes_per_second,"
        "       t.status AS transcript_status, t.last_error"
        " FROM parts p LEFT JOIN transcripts t"
        "   ON t.device_id=p.device_id AND t.session=p.session AND t.part=p.part"
        " WHERE p.device_id=? AND p.session=? ORDER BY p.part",
        (device_id, session),
    ).fetchall()
    if not parts:
        return None

    summary_row = conn.execute(
        "SELECT result, status, last_error FROM summaries"
        " WHERE device_id=? AND session=?", (device_id, session)).fetchone()
    timeline, failed = db.session_timeline(conn, device_id, session)
    # ... дальше построчно бывший код session_detail от `offsets = ...` до
    # `day = db.session_day(conn, device_id, session)`, с теми же комментариями,
    # сдвинутый на один отступ влево (вышел из `with`) ...

    summary = None
    if summary_row and summary_row["result"]:
        try:
            summary = json.loads(summary_row["result"])
        except json.JSONDecodeError:
            summary = None

    return {
        # ... бывший словарь ответа session_detail без единого изменения ...
    }
```

Перенос делается вырезанием и вставкой существующих строк `server/api_ui.py:82-160`; ни одна строка вычисления не переписывается заново.

- [ ] **Step 5: `audio.serve` в `server/audio.py`**

Заменить функцию `audio` в конце файла на:

```python
def serve(device_id, session, part, headers=None):
    """Часть записи: громкая копия, если получилась, иначе исходник. Общая для
    владельца и для ссылки гостя (share.py); `headers` — заголовки гостя."""
    with closing(db.connect()) as conn:
        found = db.audio_file(conn, device_id, session, part)
    if found is None:
        raise HTTPException(404, "нет такой части", headers=headers)
    path, media_type = found
    loud = loud_copy(path)
    if loud is not None:
        path, media_type = loud, "audio/flac"
    # FileResponse отдаёт Range, без него Safari на iPhone не проигрывает аудио.
    # Тип берётся из расширения: часть, уже сжатая в архив, приходит как FLAC.
    return FileResponse(path, media_type=media_type, headers=headers)


@router.get("/audio/{device_id}/{session}/{part}")
def audio(device_id: str, session: int, part: int, _=Depends(owner)):
    return serve(device_id, session, part)
```

- [ ] **Step 6: Модуль `server/share.py`**

```python
"""Ссылка на одну запись для другого человека — без пароля владельца.

Ключ из ссылки открывает ровно одну запись, только на чтение и только пока
ссылка жива (db.SHARE_DAYS). Ответ собирается из разрешённых полей, а не
вычёркиванием лишних из ответа владельца: новое поле на странице владельца
само к гостю не попадёт.
"""
from contextlib import closing

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

import api_ui
import audio
import auth
import db

router = APIRouter()

# Ключ лежит в адресе страницы: без no-referrer он уходил бы чужим сайтам в
# заголовке Referer, а поисковик, наткнувшись на ссылку, мог бы её запомнить.
HEADERS = {"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}

PART_FIELDS = ("part", "offset", "seconds", "envelope")
UTTERANCE_FIELDS = ("part", "idx", "start", "end", "start_abs", "speaker", "label", "text")
# `auto` нужен сайту: имя, вписанное владельцем, побеждает догадку по голосу
# (groupRows в web/lib/speakers.ts).
SPEAKER_FIELDS = ("part", "speaker", "name", "auto")
SUMMARY_FIELDS = ("summary", "topics", "decisions", "facts", "tasks", "open_questions")


def _pick(row, fields):
    return {key: row[key] for key in fields if key in row}


def guest_view(view):
    """Из ответа владельца (api_ui.session_view) — только то, что видит гость."""
    summary = view["summary"]
    return {
        "day": view["day"],
        "first_at": view["first_at"],
        "parts": [_pick(part, PART_FIELDS) for part in view["parts"]],
        "summary": _pick(summary, SUMMARY_FIELDS) if isinstance(summary, dict) else None,
        "timeline": [_pick(item, UTTERANCE_FIELDS) for item in view["timeline"]],
        "speakers": [_pick(item, SPEAKER_FIELDS) for item in view["speakers"]],
        "voice_groups": view["voice_groups"],
        "short_keys": view["short_keys"],
    }


def _gone():
    """Один ответ на всё мёртвое: по нему нельзя понять, была ли ссылка вообще."""
    return HTTPException(404, "ссылка не работает", headers=HEADERS)


def _record(conn, token):
    # Незакрытый сервер не отдаёт разговоров никому, как и владельцу (auth.verify).
    if not auth.OWNER_PASSWORD:
        raise HTTPException(503, "RECORDER_OWNER_PASSWORD не задан, доступ закрыт")
    found = db.shared_session(conn, token)
    if found is None:
        raise _gone()
    return found


@router.get("/api/share/{token}")
def shared_record(token: str):
    with closing(db.connect()) as conn:
        view = api_ui.session_view(conn, *_record(conn, token))
    if view is None:
        raise _gone()
    return JSONResponse(guest_view(view), headers={**HEADERS, "Cache-Control": "no-store"})


@router.get("/share/{token}/audio/{part}")
def shared_audio(token: str, part: int):
    with closing(db.connect()) as conn:
        device_id, session = _record(conn, token)
    return audio.serve(device_id, session, part, headers=HEADERS)
```

- [ ] **Step 7: Подключить в `server/app.py`**

В импорты (по алфавиту, после `import login`):

```python
import share
```

Перед комментарием про catch-all (после `app.include_router(login.router)`):

```python
# Ссылка гостя — без пароля и до catch-all: иначе её адреса ушли бы на форму входа.
app.include_router(share.router)
```

- [ ] **Step 8: Строка в `server/README_RU.md`**

После строки про `audio.py` (строка 31) в таблице модулей:

```markdown
| `share.py` | Ссылка на одну запись для другого человека: `GET /api/share/{token}` (только разрешённые поля) и `GET /share/{token}/audio/{part}` без пароля; ссылка живёт 7 дней, владелец закрывает её раньше |
```

- [ ] **Step 9: Запустить — должны пройти, и весь сервер тоже**

Run: `cd server && python -m pytest -q`
Expected: PASS все тесты. Отдельно убедиться, что `test_api_ui.py::test_session_detail_carries_timeline_summary_and_tasks` зелёный: ответ владельца не изменился.

- [ ] **Step 10: Commit**

```bash
git add server/db.py server/api_ui.py server/audio.py server/share.py server/app.py server/README_RU.md server/test_share.py
git commit -m "feat(server): guest reads and hears one record by its share link"
```

---

### Task 3: Страница гостя открывается без пароля, остальной сайт — нет

**Files:**
- Modify: `server/static_site.py` (константы и функция `public` после `resolve`; функция `site`)
- Modify: `server/test_api_ui.py:57-68` (`test_site_is_closed_by_the_same_password`: `/_next/` теперь открыт осознанно)
- Modify: `server/test_share.py`

**Interfaces:**
- Consumes: `share.HEADERS` (Task 2); фикстура `site` из `server/test_api_ui.py`.
- Produces: `static_site.public(hit: Path) -> bool`; `static_site.PUBLIC_DIRS`, `static_site.PUBLIC_FILES`.

- [ ] **Step 1: Написать падающие тесты**

В `server/test_share.py` к импортам:

```python
import os
from pathlib import Path

from test_api_ui import prepared, site  # noqa: F401
```

(строку `from test_api_ui import prepared` из Task 2 заменить этой.)

В конец файла:

```python
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
        assert as_guest(guest_site, path).status_code == 200, path
    assert as_guest(guest_site, "/shared/").headers["referrer-policy"] == "no-referrer"

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
```

- [ ] **Step 2: Запустить — должны упасть**

Run: `cd server && python -m pytest test_share.py -q -k "without_password or resolved_path"`
Expected: FAIL — `/shared/` без пароля даёт 303 вместо 200; `AttributeError: module 'static_site' has no attribute 'public'`.

- [ ] **Step 3: Список открытого в `server/static_site.py`**

К импортам:

```python
import share
```

После функции `resolve`:

```python
# Без пароля — только то, без чего не откроется страница гостя (/shared/?t=…):
# сама страница, код и стили сборки, значки. Данных в этих файлах нет: запись
# страница берёт с /api/share/<ключ>, а тот проверяет ключ сам (share.py).
PUBLIC_DIRS = {"shared", "_next"}
PUBLIC_FILES = {"icon.svg", "icon-192.png", "icon-512.png", "apple-touch-icon.png"}


def public(hit):
    """Файл сборки, открытый без пароля. Судим по уже разрешённому пути, а не
    по строке запроса: `/shared/../records/` разрешится в records/ — пароль."""
    relative = hit.relative_to(WEB_DIR.resolve()).parts
    if len(relative) == 1:
        return relative[0] in PUBLIC_FILES
    return relative[0] in PUBLIC_DIRS
```

Функция `site` целиком:

```python
# HEAD тоже: Next.js проверяет ссылки HEAD-запросом, и 405 на них сыпался в консоль.
@router.api_route("/{path:path}", methods=["GET", "HEAD"])
def site(request: Request, response: Response, path: str = "",
         credentials: HTTPBasicCredentials = Depends(security)):
    hit = resolve(path or "index.html") if WEB_DIR.is_dir() else None
    open_to_all = hit is not None and public(hit)
    # Страница ведёт на форму входа, а не отвечает 401: в режиме «на домашний
    # экран» Safari окно Basic-авторизации не показывает, и владелец видел бы
    # пустоту. В обычной вкладке пароль из браузера по-прежнему принимается —
    # `verify` сначала смотрит метку, потом Basic-заголовок.
    if not open_to_all and auth.verify(request, response, credentials) is None:
        return login.redirect_to_login(request)

    if not WEB_DIR.is_dir():
        raise HTTPException(503, f"фронтенд не собран: нет каталога {WEB_DIR}")
    if hit is None:
        fallback = WEB_DIR / "404.html"
        if fallback.is_file():
            return FileResponse(fallback, status_code=404)
        raise HTTPException(404, "нет такой страницы")
    return FileResponse(hit, headers=share.HEADERS if open_to_all else None)
```

- [ ] **Step 4: Поправить старый тест в `server/test_api_ui.py`**

`test_site_is_closed_by_the_same_password` проверял, что `/_next/app.js` без пароля закрыт. Теперь `/_next/` открыт ради страницы гостя; страница владельца по-прежнему закрыта. Заменить тест на:

```python
def test_site_is_closed_by_the_same_password(site):
    """Ни одна страница владельца не отдаётся без пароля: содержимого в ответе
    нет, вместо него переход на форму входа. Файлы сборки (_next) открыты ради
    страницы гостя — в них код, а не данные (test_share.py)."""
    with TestClient(site.app) as client:
        for path, attempt in (("/", {"auth": ("owner", "wrong")}), ("/session/", {})):
            answer = client.get(path, follow_redirects=False, **attempt)
            assert answer.status_code == 303
            assert answer.headers["location"].startswith("/login")
            assert "Сводка" not in answer.text and "Сессия" not in answer.text
        # JSON-API остаётся при отказе 401: странице нужна форма, фоновому
        # запросу — честная ошибка, а не HTML формы вместо данных.
        assert client.get("/api/ui/digest").status_code == 401
```

- [ ] **Step 5: Запустить весь сервер**

Run: `cd server && python -m pytest -q`
Expected: PASS все.

- [ ] **Step 6: Commit**

```bash
git add server/static_site.py server/test_api_ui.py server/test_share.py
git commit -m "feat(server): guest page and build assets open without the owner password"
```

---

### Task 4: Расшифровка — в свой компонент, плееру — адрес звука снаружи

Перестановка без изменения поведения: страница записи владельца выглядит и работает как раньше.

**Files:**
- Create: `web/components/recorder/transcript.tsx`
- Modify: `web/app/session/page.tsx` (убрать `LevelMeter`, `VOICE_SHADES`, `Line`, `Transcript` — строки 255–430; импорт `Transcript`; убрать ставшие лишними импорты)
- Modify: `web/components/recorder/player.tsx:74-84, 153, 176` (свойство `audioBase`)

**Interfaces:**
- Produces: `Transcript({ timeline: Utterance[]; marked: Set<string>; received: Set<number> })` из `@/components/recorder/transcript`; `PlayerProvider` принимает необязательное `audioBase?: string` — начало адреса звука без номера части, по умолчанию `` `/audio/${encodeURIComponent(deviceId)}/${session}` ``.

- [ ] **Step 1: Создать `web/components/recorder/transcript.tsx`**

Перенести из `web/app/session/page.tsx` строки 255–430 (`LevelMeter`, комментарий к строке транскрипта, `VOICE_SHADES`, `Line`, `Transcript`) без изменений; `Transcript` сделать экспортом. Шапка файла:

```tsx
"use client";

import { memo, useCallback, useEffect, useMemo, useRef } from "react";
import { BookmarkSimple } from "@phosphor-icons/react/dist/ssr";
import { Card, Section, Time } from "@/components/recorder/list";
import { usePlayhead, useSeek } from "@/components/recorder/player";
import type { Utterance } from "@/lib/api";
import { formatClock } from "@/lib/format";
import { cx } from "@/utils/cx";

// ... перенесённые LevelMeter, VOICE_SHADES, Line без изменений ...

/** Весь разговор, сгруппированный по частям; едет за звуком. Общий для
 * страницы записи и страницы гостя (/shared/). */
export function Transcript({ timeline, marked, received }: {
  // ... тело без изменений ...
```

- [ ] **Step 2: Страница записи берёт расшифровку из компонента**

В `web/app/session/page.tsx` удалить перенесённые строки и добавить импорт:

```tsx
import { Transcript } from "@/components/recorder/transcript";
```

Затем убрать из импортов то, что больше не используется в файле: проверить `memo`, `useCallback`, `Time`, `usePlayhead`, `useSeek`, `Utterance` — `npm run lint` и `npm run typecheck` в шаге 4 покажут лишние. `BookmarkSimple` остаётся: им рисуются метки в секции «Метки».

- [ ] **Step 3: Адрес звука у плеера**

В `web/components/recorder/player.tsx` в сигнатуре `PlayerProvider`:

```tsx
export function PlayerProvider({ deviceId, session, title, parts, marks = [], audioBase, children }: {
  deviceId: string;
  session: number;
  /** Имя записи — подпись плеера, когда звук идёт не от конкретного пункта. */
  title: string;
  /** По возрастанию номера, как отдаёт сервер. */
  parts: PlayerPart[];
  /** Метки кнопкой: засечки на волне. */
  marks?: Mark[];
  /** Откуда брать звук частей, без номера части. По умолчанию — адрес
   * владельца; у гостя — /share/<ключ>/audio. */
  audioBase?: string;
  children: React.ReactNode;
}) {
  const base = audioBase ?? `/audio/${encodeURIComponent(deviceId)}/${session}`;
```

В `seek` заменить

```tsx
    const src = audioSrc(deviceId, session, nextPart);
```

на

```tsx
    const src = `${base}/${nextPart}`;
```

и зависимости этого `useCallback` с `[deviceId, session]` на `[base]`. Из импорта `@/lib/audio` убрать `audioSrc`, если он больше нигде в файле не используется (`loadedPart`, `sharedAudio`, `takeLabel` остаются). `lib/audio.ts` не меняется: `audioSrc` и `startPlayback` нужны карточкам записей.

- [ ] **Step 4: Проверки сайта**

Run: `cd web && npm run typecheck && npm run lint && npm test && npm run build`
Expected: все четыре проходят; в `npm run build` страница `/session` собирается.

- [ ] **Step 5: Commit**

```bash
git add web/components/recorder/transcript.tsx web/app/session/page.tsx web/components/recorder/player.tsx
git commit -m "refactor(web): transcript in its own component, player takes its audio address"
```

---

### Task 5: Страница гостя `/shared/`

**Files:**
- Modify: `web/lib/api.ts` (типы `SharedDetail`, `Share`; функция `sharedGet` после `apiDelete`)
- Modify: `web/components/recorder/shell.tsx` (ветка гостя)
- Create: `web/app/shared/layout.tsx`
- Create: `web/app/shared/page.tsx`
- Modify: `web/styles/studio.css` (в конец: место плеера у гостя)

**Interfaces:**
- Consumes: `Transcript`, `PlayerProvider` с `audioBase` (Task 4); `GET /api/share/{token}` (Task 2); `/shared/` открыт без пароля (Task 3).
- Produces: `type SharedDetail`, `type Share = { token: string; created_at: string; expires_at: string }`, `sharedGet(token: string): Promise<SharedDetail>` в `@/lib/api`.

- [ ] **Step 1: Типы и запрос в `web/lib/api.ts`**

После `apiDelete`:

```ts
/** Ссылка гостя, как её видит владелец. Время — UTC из SQLite. */
export type Share = { token: string; created_at: string; expires_at: string };

/** Запись по ссылке гостя: только то, что сервер разрешил показать (server/share.py). */
export type SharedDetail = {
  day: string;
  first_at: string | null;
  parts: { part: number; offset: number; seconds: number; envelope: number[] }[];
  summary: Summary | null;
  timeline: Utterance[];
  speakers: { part: number; speaker: number; name: string; auto: boolean }[];
  voice_groups: { keys: string[]; seconds: number }[];
  short_keys: string[];
};

/** Запись по ключу из ссылки. Мимо /api/ui: там всё под паролем владельца. */
export async function sharedGet(token: string): Promise<SharedDetail> {
  const response = await fetch(`/api/share/${encodeURIComponent(token)}`);
  if (!response.ok) throw new ApiError(response.status, "ссылка не работает");
  return (await response.json()) as SharedDetail;
}
```

- [ ] **Step 2: Оболочка без меню у гостя**

В `web/components/recorder/shell.tsx`:

1. Сразу после `const pathname = usePathname() ?? "/";`:

```tsx
  // Страница гостя по ссылке: ни меню разделов, ни запроса /digest — тот под
  // паролем владельца, и гость получил бы отказ со ссылкой на вход.
  const guest = pathname.startsWith("/shared");
```

2. `useApi<DigestBody>("/digest")` заменить на `useApi<DigestBody>(guest ? null : "/digest")`.

3. Значение `className` у `<main id="content" …>` вынести в константу модуля над `Shell` и подставить её в `<main>`:

```tsx
/** Колонка содержимого. Отступ снизу на телефоне — под плавающую панель
 * разделов и жестовую полосу iPhone. */
const MAIN = "page-in mx-auto flex w-full max-w-[1250px] flex-col gap-[23px] px-[15px] pt-[23px] pb-[calc(8rem+env(safe-area-inset-bottom))] min-[361px]:px-[21px] sm:px-8 lg:gap-[30px] lg:px-12 lg:pt-[30px] lg:pb-14";
```

(строку скопировать из текущего `className` дословно.)

4. Непосредственно перед `return (` в конце `Shell` (после последнего `useEffect`; все хуки должны остаться выше):

```tsx
  if (guest) {
    return <main id="content" className={cx(MAIN, "recorder-guest")}>{children}</main>;
  }
```

- [ ] **Step 3: `web/app/shared/layout.tsx`**

```tsx
import type { Metadata } from "next";

// Страница гостя: поисковикам не показывать, ключ из адреса чужим сайтам не
// отдавать. Манифест приложения владельца лежит под паролем — гостю он не нужен.
export const metadata: Metadata = {
  title: "Запись разговора",
  robots: { index: false, follow: false },
  referrer: "no-referrer",
  manifest: null,
};

export default function SharedLayout({ children }: { children: React.ReactNode }) {
  return children;
}
```

- [ ] **Step 4: `web/app/shared/page.tsx`**

```tsx
"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { PageHeader } from "@/components/recorder/page-header";
import { PlayerProvider } from "@/components/recorder/player";
import { SummaryList } from "@/components/recorder/summary-list";
import { Transcript } from "@/components/recorder/transcript";
import { Empty, EmptyNote, Loading } from "@/components/recorder/states";
import { ApiError, sharedGet } from "@/lib/api";
import type { SharedDetail } from "@/lib/api";
import { groupRows, withVoiceLabels } from "@/lib/speakers";
import { formatClock, formatDay, formatTime, keepNumbers, recordTitle } from "@/lib/format";

/** У гостя нет меток кнопкой: сервер их не отдаёт. */
const NO_MARKS = new Set<string>();

function Body() {
  const token = useSearchParams().get("t") ?? "";
  const [result, setResult] = useState<{ token: string; data: SharedDetail | null; error: Error | null }>(
    { token: "", data: null, error: null });

  useEffect(() => {
    if (!token) return;
    let alive = true;
    sharedGet(token).then(
      (data) => { if (alive) setResult({ token, data, error: null }); },
      (error: Error) => { if (alive) setResult({ token, data: null, error }); },
    );
    return () => { alive = false; };
  }, [token]);

  // Реплики подписаны голосом, как у владельца. «Это я» гостю не отдаётся и
  // на подписи не влияет.
  const data = useMemo(() => result.data && {
    ...result.data,
    timeline: withVoiceLabels(result.data.timeline, groupRows(
      result.data.speakers.map((row) => ({ ...row, is_me: false })),
      result.data.voice_groups, result.data.short_keys)),
  }, [result.data]);

  const error = result.token === token ? result.error : null;
  if (!token || error) {
    const gone = !token || (error instanceof ApiError && error.status === 404);
    return gone
      ? <Empty title="Ссылка больше не работает" text="Попросите прислать новую." />
      : <Empty title="Не удалось открыть запись" text="Проверьте связь и обновите страницу." />;
  }
  if (!data || result.token !== token) return <Loading />;

  const topic = data.summary?.topics?.[0] ?? null;
  const title = recordTitle({ topic, day: data.day, first_at: data.first_at });
  const duration = data.parts.reduce((end, part) => Math.max(end, part.offset + part.seconds), 0);

  return (
    <PlayerProvider deviceId={`share-${token}`} session={0} title={title} parts={data.parts}
                    audioBase={`/share/${encodeURIComponent(token)}/audio`}>
      <PageHeader
        title={title}
        aside={
          <span className="flex flex-wrap items-center gap-x-3.5 gap-y-1">
            {topic ? <span>{formatDay(data.day)}, {formatTime(data.first_at)}</span> : null}
            {duration > 0 ? <span className="font-mono">{formatClock(duration)}</span> : null}
          </span>
        }
      />
      {data.summary ? (
        <>
          {/* Та же табличка, что у владельца: оформление в styles/studio.css. */}
          <section className="studio-summary" aria-labelledby="summary-label">
            <h2 id="summary-label" className="studio-summary-label">Главное из разговора</h2>
            <p>{keepNumbers(data.summary.summary)}</p>
          </section>
          <SummaryList title="Решения" items={data.summary.decisions} />
          <SummaryList title="Задачи" items={data.summary.tasks} />
          <SummaryList title="Вопросы без ответа" items={data.summary.open_questions} />
          <SummaryList title="Факты" items={data.summary.facts} />
        </>
      ) : null}
      {data.timeline.length === 0
        ? <EmptyNote>Расшифровки пока нет.</EmptyNote>
        : <Transcript timeline={data.timeline} marked={NO_MARKS}
                      received={new Set(data.parts.map((part) => part.part))} />}
    </PlayerProvider>
  );
}

export default function SharedPage() {
  // Статический экспорт требует Suspense вокруг useSearchParams.
  return (
    <Suspense fallback={<Loading />}>
      <Body />
    </Suspense>
  );
}
```

- [ ] **Step 5: Место плеера у гостя в `web/styles/studio.css`**

В конец файла:

```css
/* ----- Страница гостя (/shared/): ни боковой, ни нижней панели разделов -----
 * Плеер и подложка под ним встают от края до края окна, а на телефоне плеер
 * садится к нижнему краю: висеть на 80 px выше пустоты ему незачем. */
.recorder-guest .recorder-player {
  left: 40px;
}

.recorder-guest .recorder-player-veil {
  left: 0;
}

@media (max-width: 1023px) {
  .recorder-guest .recorder-player {
    left: 13px;
    bottom: max(13px, env(safe-area-inset-bottom, 0px));
  }
}
```

- [ ] **Step 6: Проверки сайта и сборки**

Run: `cd web && npm run typecheck && npm run lint && npm test && npm run build`
Expected: проходят. Затем:

Run: `ls web/out/shared/index.html && grep -o 'name="robots" content="[^"]*"' web/out/shared/index.html && grep -o 'name="referrer" content="[^"]*"' web/out/shared/index.html && grep -c manifest.webmanifest web/out/shared/index.html`
Expected: файл есть; `content="noindex, nofollow"`; `content="no-referrer"`; счётчик манифеста `0`.

- [ ] **Step 7: Commit**

```bash
git add web/lib/api.ts web/components/recorder/shell.tsx web/app/shared web/styles/studio.css
git commit -m "feat(web): guest page plays and reads one record by its link"
```

---

### Task 6: Блок «Поделиться» на странице записи владельца

**Files:**
- Create: `web/components/recorder/share-links.tsx`
- Modify: `web/app/session/page.tsx` (импорт; блок в боковой колонке перед `<DeleteSession …/>`)

**Interfaces:**
- Consumes: `Share` из `@/lib/api` (Task 5); маршруты владельца из Task 1.
- Produces: `ShareLinks({ deviceId: string; session: number })`.

- [ ] **Step 1: Компонент `web/components/recorder/share-links.tsx`**

```tsx
"use client";

import { useState } from "react";
import { LinkSimple } from "@phosphor-icons/react/dist/ssr";
import { Card, Row, Section } from "@/components/recorder/list";
import { Button } from "@/components/recorder/ui";
import { apiDelete, apiPost } from "@/lib/api";
import type { Share } from "@/lib/api";
import { formatDay } from "@/lib/format";
import { useApi } from "@/lib/use-api";

function shareUrl(token: string) {
  return `${window.location.origin}/shared/?t=${encodeURIComponent(token)}`;
}

/**
 * Ссылки на эту запись для другого человека: слушать и читать без пароля,
 * 7 дней (server/share.py). Копирование — только по нажатию самой кнопки:
 * Safari пускает в буфер обмена лишь из жеста, а не после ответа сервера.
 */
export function ShareLinks({ deviceId, session }: { deviceId: string; session: number }) {
  const path = `/sessions/${encodeURIComponent(deviceId)}/${session}/shares`;
  const { data: links, reload } = useApi<Share[]>(path);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      await apiPost<Share>(path, {});
      reload();
    } catch (err) {
      setError(`не удалось создать ссылку: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function close(token: string) {
    setError(null);
    try {
      await apiDelete(`/shares/${encodeURIComponent(token)}`);
      reload();
    } catch (err) {
      setError(`не удалось закрыть доступ: ${(err as Error).message}`);
    }
  }

  async function copy(token: string) {
    try {
      await navigator.clipboard.writeText(shareUrl(token));
      setCopied(token);
    } catch {
      // Буфер недоступен — ссылка видна текстом, её можно выделить руками.
      setError("не удалось скопировать: выделите ссылку и скопируйте её");
    }
  }

  return (
    <Section note="Поделиться записью">
      <Card>
        {(links ?? []).map((link) => (
          <Row key={link.token} className="flex-col items-start gap-2">
            <span className="w-full select-all break-all font-mono text-micro text-text-secondary">
              {shareUrl(link.token)}
            </span>
            <span className="text-note text-text-tertiary">работает до {formatDay(link.expires_at)}</span>
            <span className="flex flex-wrap gap-2">
              <Button onClick={() => copy(link.token)}>
                {copied === link.token ? "Скопировано" : "Скопировать"}
              </Button>
              <Button variant="ghost" onClick={() => close(link.token)}>Закрыть доступ</Button>
            </span>
          </Row>
        ))}
        <Row className="flex-col items-start gap-3">
          <span className="text-note text-text-secondary">
            По ссылке человек без пароля послушает запись и прочтёт главное и расшифровку.
            Задачи, другие записи и правка ему не видны. Ссылка работает 7 дней.
          </span>
          <Button icon={LinkSimple} disabled={busy} onClick={create}>
            {busy ? "Создаю…" : "Создать ссылку"}
          </Button>
        </Row>
      </Card>
      {error ? <p className="rise-in px-1 text-note text-accent-text">{error}</p> : null}
    </Section>
  );
}
```

- [ ] **Step 2: Вставить блок на страницу записи**

В `web/app/session/page.tsx` импорт:

```tsx
import { ShareLinks } from "@/components/recorder/share-links";
```

В боковой колонке строку

```tsx
          <DeleteSession deviceId={data.device_id} session={data.session} />
```

заменить на

```tsx
          <ShareLinks deviceId={data.device_id} session={data.session} />
          <DeleteSession deviceId={data.device_id} session={data.session} />
```

- [ ] **Step 3: Проверки сайта**

Run: `cd web && npm run typecheck && npm run lint && npm test && npm run build`
Expected: проходят. Если `LinkSimple` не найден в `@phosphor-icons/react/dist/ssr` — взять `Link` из того же пакета (проверить: `ls web/node_modules/@phosphor-icons/react/dist/ssr | grep -i link`).

- [ ] **Step 4: Commit**

```bash
git add web/components/recorder/share-links.tsx web/app/session/page.tsx
git commit -m "feat(web): share block on the record page — create, copy, close"
```

---

### Task 7: Живая проверка на локальном сервере

Ничего не коммитится, кроме исправлений найденного (каждое — тест + коммит в задаче, к которой относится код).

**Files:** нет (временные данные — в каталоге scratchpad сессии).

- [ ] **Step 1: Демо-данные и сервер**

Run (своя машина, каталог scratchpad, рабочие данные не трогаются):

```bash
export DEMO="$SCRATCH/share-demo" && RECORDER_DATA="$DEMO" python tools/seed_demo.py
cd server && RECORDER_DATA="$DEMO" RECORDER_OWNER_PASSWORD=demo RECORDER_DEVICE_TOKEN=demo RECORDER_WEB_DIR=../web/out python -m uvicorn app:app --port 8090
```

(`$SCRATCH` — каталог scratchpad сессии; сервер запускать в фоне.)

- [ ] **Step 2: Выдать ссылку и проверить адреса**

Run:

```bash
SESSION=$(curl -s -u owner:demo localhost:8090/api/ui/sessions | python -c "import json,sys; r=json.load(sys.stdin)[0]; print(r['device_id'], r['session'])")
set -- $SESSION
TOKEN=$(curl -s -u owner:demo -X POST localhost:8090/api/ui/sessions/$1/$2/shares | python -c "import json,sys; print(json.load(sys.stdin)['token'])")
curl -s -o /dev/null -w "%{http_code} shared\n" "localhost:8090/shared/?t=$TOKEN"
curl -s -o /dev/null -w "%{http_code} api\n" "localhost:8090/api/share/$TOKEN"
curl -s -o /dev/null -w "%{http_code} audio\n" "localhost:8090/share/$TOKEN/audio/1"
curl -s -o /dev/null -w "%{http_code} records (ждём 303)\n" "localhost:8090/records/"
```

Expected: `200 shared`, `200 api`, `200 audio`, `303 records`.

- [ ] **Step 3: Страница гостя в браузере — компьютер и телефон**

Открыть `http://localhost:8090/shared/?t=$TOKEN` в headless Chrome чистым профилем (без метки входа) и снять снимки шириной 1440 и 390 px. Проверить на снимках:
- шапка с темой и датой, «Главное из разговора», пункты, расшифровка;
- нет меню разделов и нижней панели;
- на 390 px плеер у нижнего края экрана (Review Focus 4).

В журнале сети страницы (DevTools Protocol или `--enable-logging --v=1`) нет ни одного ответа 303/401: все файлы, которые страница просит, открыты.

- [ ] **Step 4: Нажать Play**

В браузере нажать Play на странице гостя: время в плеере идёт, звучащая реплика подсвечивается. Нажать на реплику — звук перескакивает к ней.

- [ ] **Step 5: Мёртвые и странные ссылки**

Открыть `/shared/`, `/shared/?t=abc` и `/shared/?t=<ключ>` после «Закрыть доступ» — на всех трёх «Ссылка больше не работает», без ссылки на вход (Review Focus 1).

- [ ] **Step 6: Владелец открывает свою ссылку**

В профиле с меткой входа владельца (войти через `/login`, пароль `demo`) открыть ссылку гостя: та же страница гостя, без меню, без блока «Поделиться», в сети нет запроса `/api/ui/digest` (Review Focus 3).

- [ ] **Step 7: Блок «Поделиться» у владельца**

На странице записи владельца: «Создать ссылку» → ссылка появилась с «работает до …»; «Скопировать» → «Скопировано»; «Закрыть доступ» → ссылка исчезла из списка. Снимок блока на 390 px.

- [ ] **Step 8: Остановить сервер и убрать демо-данные из scratchpad**

---

### Task 8: Выкладка на сервер владельца и проверка там

**Files:** нет.

- [ ] **Step 1: Тесты сервера на VPS (там есть ffmpeg)**

Run: `bash tools/remote_tests.sh`
Expected: все тесты проходят.

- [ ] **Step 2: Выложить**

Run: `bash tools/deploy.sh`
Expected: проверка типов и сборка проходят, службы `recorder-api` и `recorder-worker` активны.

- [ ] **Step 3: Проверка на живом сайте без пароля**

С адресом сайта из `tools/deploy.sh`:

```bash
curl -s -o /dev/null -w "%{http_code} shared\n" https://<сайт>/shared/
curl -s -o /dev/null -w "%{http_code} records (ждём 303)\n" https://<сайт>/records/
curl -s -o /dev/null -w "%{http_code} api ui (ждём 401)\n" https://<сайт>/api/ui/digest
curl -s -o /dev/null -w "%{http_code} мёртвая ссылка (ждём 404)\n" https://<сайт>/api/share/no-such-link
```

Expected: `200`, `303`, `401`, `404`. Ссылку на настоящую запись создаёт владелец сам, кнопкой на странице записи.
