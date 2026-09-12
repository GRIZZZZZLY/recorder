"""Проверки приёмника. Запуск: python -m pytest server/ -q"""
import importlib
import struct
import zlib

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"


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


def headers(body, session=7, part=1, crc=None, device="xiao-01", recovered="0"):
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/octet-stream",
        "X-Device-Id": device,
        "X-Session": str(session),
        "X-Part": str(part),
        "X-Recovered": recovered,
        "X-Crc32": crc or f"{zlib.crc32(body) & 0xFFFFFFFF:08x}",
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDER_DATA", str(tmp_path))
    monkeypatch.setenv("RECORDER_DEVICE_TOKEN", TOKEN)
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
    sessions = client.get("/api/v1/sessions").json()
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


def test_wrong_crc_is_rejected_and_leaves_nothing_behind(client):
    body = wav()
    response = client.post(
        "/api/v1/upload", content=body, headers=headers(body, crc="deadbeef")
    )

    assert response.status_code == 422
    assert list(client.audio_dir.iterdir()) == []
    assert client.get("/api/v1/sessions").json() == []


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


def test_recovered_flag_is_kept(client):
    body = wav()
    client.post(
        "/api/v1/upload", content=body, headers=headers(body, recovered="1")
    )
    assert client.get("/api/v1/sessions").json()[0]["recovered_parts"] == 1


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

    sessions = client.get("/api/v1/sessions").json()
    assert sessions[0]["parts"] == 3
    assert sessions[0]["bytes"] == 3 * len(wav())
