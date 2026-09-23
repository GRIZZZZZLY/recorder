"""Проверки приёмника. Запуск: python -m pytest server/ -q"""
import importlib
import struct
import zlib

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"
PASSWORD = "owner-secret"
AUTH = ("owner", PASSWORD)


def wav(seconds=1, marker=0):
    """Канонический 44-байтный заголовок плюс тишина, как пишет прошивка."""
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


def headers(body, session=7, part=1, crc=None, device="xiao-01", recovered="0", marks=None,
            session_parts=None):
    sent = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/octet-stream",
        "X-Device-Id": device,
        "X-Session": str(session),
        "X-Part": str(part),
        "X-Recovered": recovered,
        "X-Crc32": crc or f"{zlib.crc32(body) & 0xFFFFFFFF:08x}",
    }
    if marks is not None:
        sent["X-Marks"] = marks
    if session_parts is not None:
        sent["X-Session-Parts"] = session_parts
    return sent


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDER_DATA", str(tmp_path))
    monkeypatch.setenv("RECORDER_DEVICE_TOKEN", TOKEN)
    monkeypatch.setenv("RECORDER_OWNER_PASSWORD", PASSWORD)
    import db as db_module
    import app as app_module

    # db читает пути из окружения при импорте, поэтому обновляем оба модуля
    importlib.reload(db_module)
    importlib.reload(app_module)
    with TestClient(app_module.app) as test_client:
        test_client.audio_dir = tmp_path / "audio"
        yield test_client


def test_stores_part_and_reports_size_and_crc(client):
    body = wav()
    response = client.post("/api/v1/upload", content=body, headers=headers(body))

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "stored"
    assert result["bytes"] == len(body)
    assert result["crc32"] == f"{zlib.crc32(body) & 0xFFFFFFFF:08x}"
    # файл лежит целиком, временных хвостов не осталось
    stored = client.audio_dir / "xiao-01_S0007_P01.wav"
    assert stored.read_bytes() == body
    assert list(client.audio_dir.glob("*.part")) == []


def test_repeat_of_same_part_is_duplicate_not_second_row(client):
    body = wav()
    first = client.post("/api/v1/upload", content=body, headers=headers(body))
    second = client.post("/api/v1/upload", content=body, headers=headers(body))

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert second.json()["crc32"] == first.json()["crc32"]
    sessions = client.get("/api/v1/sessions", auth=AUTH).json()
    assert len(sessions) == 1
    assert sessions[0]["parts"] == 1


def test_same_part_with_different_audio_is_rejected_not_overwritten(client):
    original = wav(marker=1)
    client.post("/api/v1/upload", content=original, headers=headers(original))

    other = wav(marker=2)
    response = client.post("/api/v1/upload", content=other, headers=headers(other))

    assert response.status_code == 409
    stored = client.audio_dir / "xiao-01_S0007_P01.wav"
    assert stored.read_bytes() == original


def test_parallel_uploads_of_one_part_do_not_touch_the_accepted_file(client):
    """Повтор диктофона может прийти, пока сервер ещё принимает прошлую
    попытку той же части. Опоздавший запрос не должен дописать уже принятый
    и подтверждённый файл."""
    import asyncio
    import httpx
    import app as app_module

    first, second = wav(marker=1), wav(marker=2)
    half = len(first) // 2

    async def run():
        first_half_in, second_half_in, first_done = (asyncio.Event() for _ in range(3))

        async def body_first():
            yield first[:half]
            first_half_in.set()
            await second_half_in.wait()
            yield first[half:]

        async def body_second():
            await first_half_in.wait()
            yield second[:half]
            second_half_in.set()
            await first_done.wait()
            yield second[half:]

        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            async def send(body, data):
                sent = headers(data) | {"Content-Length": str(len(data))}
                return await http.post("/api/v1/upload", content=body(), headers=sent)

            async def send_first():
                try:
                    return await send(body_first, first)
                finally:
                    first_done.set()

            return await asyncio.gather(send_first(), send(body_second, second))

    accepted, late = asyncio.run(run())
    assert (accepted.status_code, late.status_code) == (201, 409)
    assert (client.audio_dir / "xiao-01_S0007_P01.wav").read_bytes() == first
    assert list(client.audio_dir.glob("*.part")) == []


def test_wrong_crc_is_rejected_and_leaves_nothing_behind(client):
    body = wav()
    response = client.post(
        "/api/v1/upload", content=body, headers=headers(body, crc="deadbeef")
    )

    assert response.status_code == 422
    assert list(client.audio_dir.iterdir()) == []
    assert client.get("/api/v1/sessions", auth=AUTH).json() == []


def test_wrong_token_is_rejected(client):
    body = wav()
    bad = headers(body) | {"Authorization": "Bearer nope"}
    assert client.post("/api/v1/upload", content=body, headers=bad).status_code == 401


def test_bad_headers_are_rejected(client):
    body = wav()
    cases = {
        "X-Session": "0",
        "X-Part": "100",
        "X-Device-Id": "bad id!",
        "X-Crc32": "xyz",
    }
    for name, value in cases.items():
        broken = headers(body) | {name: value}
        response = client.post("/api/v1/upload", content=body, headers=broken)
        assert response.status_code == 400, name


def test_marks_are_stored_and_repeat_does_not_double_them(client):
    body = wav()
    first = client.post("/api/v1/upload", content=body,
                        headers=headers(body, marks="0.50, 12.25"))
    second = client.post("/api/v1/upload", content=body,
                         headers=headers(body, marks="0.50,12.25"))

    assert (first.status_code, second.status_code) == (201, 200)
    import db as db_module
    with db_module.connect() as conn:
        rows = conn.execute("SELECT part, offset FROM marks ORDER BY offset").fetchall()
    assert [tuple(row) for row in rows] == [(1, 0.5), (1, 12.25)]


def test_envelope_is_measured_from_the_audio(client):
    """Громкость берётся из файла: тихая половина видна как тихая."""
    loud = bytes([0x00, 0x40]) * (16000 * 4)       # ±16384 четыре секунды
    quiet = bytes([0x00, 0x04]) * (16000 * 4)      # ±1024 четыре секунды
    h = bytearray(wav(seconds=0)[:44])
    struct.pack_into("<I", h, 40, len(loud) + len(quiet))
    struct.pack_into("<I", h, 4, 36 + len(loud) + len(quiet))
    body = bytes(h) + loud + quiet

    assert client.post("/api/v1/upload", content=body,
                       headers=headers(body)).status_code == 201

    import db as db_module, json
    with db_module.connect() as conn:
        values = json.loads(conn.execute("SELECT envelope FROM parts").fetchone()[0])
    assert len(values) == 16                       # полсекунды на значение
    assert values[:8] == [100] * 8
    assert all(2 <= value <= 10 for value in values[8:]), values


def test_broken_marks_header_is_rejected(client):
    body = wav()
    for value in ("abc", "12.5;30", "-1", "4000"):
        response = client.post("/api/v1/upload", content=body,
                               headers=headers(body, marks=value))
        assert response.status_code == 400, value
    # Отказ не оставил ни строки, ни файла: часть остаётся на карте диктофона.
    import db as db_module
    with db_module.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 0


def test_declared_part_count_is_stored(client):
    """X-Session-Parts: сколько частей диктофон закрыл при STOP. Без заголовка — NULL."""
    body = wav()
    assert client.post("/api/v1/upload", content=body,
                       headers=headers(body, part=1, session_parts="3")).status_code == 201
    assert client.post("/api/v1/upload", content=wav(marker=2),
                       headers=headers(wav(marker=2), session=8, part=1)).status_code == 201
    import db as db_module
    with db_module.connect() as conn:
        rows = conn.execute(
            "SELECT session, expected_parts FROM parts ORDER BY session").fetchall()
    assert [tuple(row) for row in rows] == [(7, 3), (8, None)]


def test_broken_part_count_is_rejected(client):
    body = wav()
    for part, value in ((1, "abc"), (1, "0"), (1, "100"), (5, "3")):
        response = client.post("/api/v1/upload", content=body,
                               headers=headers(body, part=part, session_parts=value))
        assert response.status_code == 400, (part, value)
    import db as db_module
    with db_module.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 0


def test_recovered_flag_is_kept(client):
    body = wav()
    client.post(
        "/api/v1/upload", content=body, headers=headers(body, recovered="1")
    )
    assert client.get("/api/v1/sessions", auth=AUTH).json()[0]["recovered_parts"] == 1


def test_reply_matches_what_firmware_greps_for(client):
    """Прошивка не разбирает JSON, а ищет подстроки в ответе.

    Uploader.ino sendPart(): reply.indexOf("\\"crc32\\":\\"<hex>\\"") и
    reply.indexOf("\\"bytes\\":<число>"). Любой пробел после двоеточия или
    другой порядок полей молча превратит доставку в вечный повтор.
    """
    body = wav()
    crc = f"{zlib.crc32(body) & 0xFFFFFFFF:08x}"

    for _ in range(2):  # 201 и затем 200: прошивка одинаково верит обоим
        response = client.post("/api/v1/upload", content=body, headers=headers(body))
        raw = response.text
        assert f'"crc32":"{crc}"' in raw, raw
        assert f'"bytes":{len(body)}' in raw, raw


def test_parts_of_one_session_are_grouped(client):
    for part in (1, 2, 3):
        body = wav(marker=part)
        client.post("/api/v1/upload", content=body, headers=headers(body, part=part))

    sessions = client.get("/api/v1/sessions", auth=AUTH).json()
    assert sessions[0]["parts"] == 3
    assert sessions[0]["bytes"] == 3 * len(wav())
