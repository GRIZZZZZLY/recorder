"""Gemini: порции под минутный лимит, сшивка времени и говорящих, ошибки."""
import io
import wave

import httpx
import pytest

import gemini
import transcribe


def silent_wav(path, seconds, rate=16000):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\0\0" * int(seconds * rate))
    return path


def answer(words):
    return {"id": "interactions/x", "steps": [{"content": [{"type": "text", "annotations": [
        {"type": "word_info", "text": t, "speaker": f"spk:{spk}",
         "start_offset": f"{s}s", "end_offset": f"{s + 0.4}s"} for t, spk, s in words]}]}]}


@pytest.fixture
def api(monkeypatch):
    """Поддельный Google: отвечает по адресу, считает вызовы и паузы."""
    calls, sleeps, replies = [], [], []

    def handler(request):
        url = str(request.url)
        calls.append((request.method, url))
        if url.endswith("/upload/v1beta/files"):
            return httpx.Response(200, headers={"x-goog-upload-url": "https://up/1"})
        if url == "https://up/1":
            return httpx.Response(200, json={"file": {"name": "files/a", "uri": "u",
                                                      "state": "ACTIVE"}})
        if url.endswith("/interactions"):
            return replies.pop(0)
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gemini.httpx, "request",
                        lambda method, url, timeout=None, **kw: client.request(method, url, **kw))
    monkeypatch.setattr(gemini.time, "sleep", sleeps.append)
    monkeypatch.setattr(gemini, "API_KEY", "k" * 39)
    monkeypatch.setattr(gemini, "_last_request", None)
    monkeypatch.setattr(transcribe, "LOUDNORM", False)
    return calls, sleeps, replies


def test_ten_minute_part_goes_in_two_paced_chunks(api, tmp_path):
    calls, sleeps, replies = api
    replies += [httpx.Response(200, json=answer([("Привет.", 0, 1.0), ("Да", 1, 2.0)])),
                httpx.Response(200, json=answer([("Пока", 0, 3.0)]))]

    result = gemini.transcribe_file(silent_wav(tmp_path / "p.wav", 600))

    assert [c for c in calls if c[1].endswith("/interactions")] == [("POST", gemini.BASE + "/v1beta/interactions")] * 2
    assert sleeps and sleeps[-1] > gemini.GAP_SEC - 5  # вторая порция ждёт минуту
    assert result.duration_sec == 600
    # Время второй порции — от начала части; её говорящие не сливаются с первой.
    assert [(u["s"], u["spk"], u["text"]) for u in result.utterances] == [
        (1.0, 0, "Привет."), (2.0, 1, "Да"), (303.0, 10, "Пока")]
    assert result.text == "Привет. Да Пока"
    assert sum(1 for m, _ in calls if m == "DELETE") == 2  # звук у Google не остаётся


def test_split_keeps_every_sample(tmp_path):
    data = bytearray(silent_wav(tmp_path / "p.wav", 601).read_bytes())
    # Как ffmpeg в трубу: длина в заголовке «неизвестна».
    data[4:8] = data[40:44] = b"\xff\xff\xff\xff"
    data = bytes(data)
    pieces, duration = gemini.split_wav(data, 300)
    assert [round(start, 3) for start, _ in pieces] == [0, 200.333, 400.667]
    frames = sum(wave.open(io.BytesIO(p), "rb").getnframes() for _, p in pieces)
    assert frames == 601 * 16000 and duration == 601


