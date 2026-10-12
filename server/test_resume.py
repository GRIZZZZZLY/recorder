"""Докачка части после обрыва. Запуск: python -m pytest server/ -q

Обрыв на живом канале до сервера случается посреди 19-мегабайтной части;
без докачки часть каждый раз уходила заново с нуля."""
import asyncio
import zlib

from test_upload import AUTH, client, headers, wav  # noqa: F401


def crc_of(data):
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"


def resume_headers(body, offset, **kw):
    sent = headers(body, **kw)
    sent["X-Total"] = str(len(body))
    sent["X-Offset"] = str(offset)
    return sent


def probe(client, body, **kw):
    sent = headers(body, **kw)
    sent["X-Total"] = str(len(body))
    return client.get("/api/v1/upload/offset", headers=sent)


def partials(client):
    return list(client.audio_dir.glob("*.partial"))


def test_drop_then_resume_gives_the_exact_file(client):
    """Главное: обрыв посреди тела не выбрасывает дошедшие байты, а
    продолжение с них даёт ровно исходный файл."""
    import app as app_module

    body = wav(seconds=3, marker=5)
    cut = 40_000
    sent = resume_headers(body, 0)

    async def dropped():
        # ASGI напрямую: два куска тела, затем клиент пропал.
        messages = [
            {"type": "http.request", "body": body[:20_000], "more_body": True},
            {"type": "http.request", "body": body[20_000:cut], "more_body": True},
            {"type": "http.disconnect"},
        ]

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(_message):
            pass

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/api/v1/upload",
            "raw_path": b"/api/v1/upload", "query_string": b"", "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in
                        (sent | {"Content-Length": str(len(body))}).items()],
            "client": ("127.0.0.1", 1), "server": ("t", 80),
        }
        try:
            await app_module.app(scope, receive, send)
        except Exception:
            pass  # обрыв может всплыть исключением — важно, что лежит на диске

    asyncio.run(dropped())
    assert [p.stat().st_size for p in partials(client)] == [cut]
    assert not (client.audio_dir / "xiao-01_S0007_P01.wav").exists()

    response = probe(client, body)
    assert response.json() == {"offset": cut}

    rest = body[cut:]
    response = client.post("/api/v1/upload", content=rest, headers=resume_headers(body, cut))
    assert response.status_code == 201, response.text
    assert response.json()["bytes"] == len(body)
    assert (client.audio_dir / "xiao-01_S0007_P01.wav").read_bytes() == body
    assert partials(client) == []


def test_offset_beyond_what_server_has_is_refused(client):
    body = wav()
    rest = body[100:]
    response = client.post("/api/v1/upload", content=rest, headers=resume_headers(body, 100))
    assert response.status_code == 416
    assert not (client.audio_dir / "xiao-01_S0007_P01.wav").exists()


def test_corrupt_partial_is_caught_by_crc_and_thrown_away(client):
    body = wav(seconds=2, marker=3)
    partial = client.audio_dir / f"xiao-01_S0007_P01.{crc_of(body)}.{len(body)}.partial"
    partial.write_bytes(b"\xff" * 1000)  # не те байты
    response = client.post("/api/v1/upload", content=body[1000:],
                           headers=resume_headers(body, 1000))
    assert response.status_code == 422
    assert partials(client) == []
    assert not (client.audio_dir / "xiao-01_S0007_P01.wav").exists()


def test_probe_of_stored_part_confirms_it(client):
    """Ответ потерялся после приёма: диктофон узнаёт о доставке из вопроса,
    не отправляя 19 МБ ещё раз."""
    body = wav()
    client.post("/api/v1/upload", content=body, headers=headers(body))
    response = probe(client, body)
    assert response.status_code == 200
    assert response.json() == {"status": "stored", "session": 7, "part": 1,
                               "bytes": len(body), "crc32": crc_of(body)}


