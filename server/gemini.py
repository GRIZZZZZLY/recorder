"""Распознавание через Gemini 3.5 Transcribe, порциями под бесплатный тариф.

Контракт сверен с ai.google.dev/gemini-api/docs/transcribe 7 октября 2026:
  POST {BASE}/upload/v1beta/files            загрузка звука (resumable)
  POST {BASE}/v1beta/interactions            расшифровка, ответ синхронный
  слова: steps[].content[].annotations[] type=word_info
         {text, speaker: "spk:N", start_offset: "1.5s", end_offset}

Бесплатный тариф — 10 000 входных токенов в минуту, звук стоит около 25
токенов в секунду плюс ~1000 на служебный текст. Десятиминутная часть
(~16 000) не пролезает никогда, поэтому часть режется на равные порции не
длиннее CHUNK_SEC, а запросы идут не чаще раза в GAP_SEC. Дневной лимит
бесплатного тарифа по отзывам около 100 запросов: при порциях по 5 минут это
около 8 часов записи в сутки.

Замер 7 октября 2026 на четырёх записях (тот же выровненный звук): Gemini
различает говорящих (0015: 3–4 вместо одного у Deepgram) и слова («вашу
семью» вместо «вашу 7»). На тихом невыровненном звуке Gemini молча обрывал
текст на середине — поэтому звук всегда идёт через transcribe.loudness_normalized.
"""
import io
import math
import os
import re
import time
import wave

import httpx

import transcribe
from stt import TranscribeError, Transcript

BASE = os.environ.get("GEMINI_URL", "https://generativelanguage.googleapis.com")
API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("RECORDER_GEMINI_MODEL", "gemini-3.5-transcribe")
# Ручки под тариф. На платном: CHUNK_SEC=1800 (предел с диаризацией), GAP_SEC=0.
CHUNK_SEC = float(os.environ.get("RECORDER_GEMINI_CHUNK_SEC", 300))
GAP_SEC = float(os.environ.get("RECORDER_GEMINI_GAP_SEC", 65))
TIMEOUT_SEC = 300
# Минутный лимит ждём на месте: ответ 429 сам говорит, сколько («retry in 11s»).
# Дольше двух минут — это уже не минутный лимит, часть уходит в очередь на час.
MINUTE_WAIT_MAX_SEC = 120
MINUTE_RETRIES = 3
ACCOUNT_RETRY_MINUTES = 60
# Номера говорящих у Gemini свои в каждой порции: spk:0 второй порции — не
# обязательно spk:0 первой. Сдвигаем, чтобы разные люди не слились в одного:
# порция 0 даёт 0, 1, 2…, порция 1 — 10, 11…
SPEAKERS_PER_CHUNK = 10
# Новая реплика: другой говорящий, пауза или конец предложения — как у Deepgram.
PAUSE_SEC = 1.5
SENTENCE_END = re.compile(r"[.!?…]$")

_last_request = None  # monotonic-время прошлой расшифровки, общее на процесс


def split_wav(data, chunk_sec=None):
    """([(начало в секундах, WAV-байты)], длина в секундах): равные порции
    не длиннее chunk_sec."""
    chunk_sec = chunk_sec or CHUNK_SEC
    with wave.open(io.BytesIO(data), "rb") as src:
        params = src.getparams()
        frames = src.readframes(src.getnframes())
    width = params.sampwidth * params.nchannels
    total = len(frames) // width
    count = max(1, math.ceil(total / params.framerate / chunk_sec))
    step = math.ceil(total / count)
    pieces = []
    for i in range(count):
        out = io.BytesIO()
        with wave.open(out, "wb") as dst:
            # ffmpeg в трубу пишет длину 0xFFFFFFFF («неизвестна»): берём
            # всё, кроме длины, а её wave проставит сам при закрытии.
            dst.setparams(params._replace(nframes=0))
            dst.writeframes(frames[i * step * width:(i + 1) * step * width])
        pieces.append((i * step / params.framerate, out.getvalue()))
    return pieces, total / params.framerate


def _seconds(value):
    return float(str(value or "0").rstrip("s"))


def _speaker(value, base):
    if not value:
        return None
    try:
        return int(str(value).rsplit(":", 1)[-1]) + base
    except ValueError:
        return None


def parse_words(payload, offset=0.0, speaker_base=0):
    """Слова ответа в формате Transcript.words, время от начала части."""
    return [
        {"w": a["text"], "s": offset + _seconds(a.get("start_offset")),
         "e": offset + _seconds(a.get("end_offset")),
         "spk": _speaker(a.get("speaker"), speaker_base),
         "c": None}  # уверенности Gemini не даёт: числа спорными не помечаются
        for step in payload.get("steps") or []
        for content in step.get("content") or []
        for a in content.get("annotations") or []
        if a.get("type") == "word_info" and a.get("text")
    ]


def utterances_from(words):
    result = []
    for word in words:
        last = result[-1] if result else None
        if (last and last["spk"] == word["spk"] and word["s"] - last["e"] < PAUSE_SEC
                and not SENTENCE_END.search(last["text"])):
            last["text"] += " " + word["w"]
            last["e"] = word["e"]
        else:
            result.append({"s": word["s"], "e": word["e"], "spk": word["spk"],
                           "text": word["w"]})
    return result


def _call(method, url, headers=None, **kwargs):
    headers = {"x-goog-api-key": API_KEY, **(headers or {})}
    for _ in range(MINUTE_RETRIES):
        try:
            response = httpx.request(method, url, headers=headers,
                                     timeout=TIMEOUT_SEC, **kwargs)
        except httpx.TimeoutException:
            raise TranscribeError(f"Gemini не ответил за {TIMEOUT_SEC} с", retryable=True)
        except httpx.HTTPError as error:
            raise TranscribeError(f"сеть недоступна: {error}", retryable=True)
        if response.status_code != 429:
            break
        found = re.search(r"retry in ([\d.]+)s", response.text)
        if not found or float(found.group(1)) > MINUTE_WAIT_MAX_SEC:
            break
        time.sleep(float(found.group(1)) + 1)

    if response.status_code < 300:
        return response
    detail = response.text[:300]
    status = response.status_code
    # Лимит, ключ, права, страна: лечится временем или руками владельца.
    if status in (401, 403, 429) or "location is not supported" in detail:
        raise TranscribeError(f"Gemini отказал {status}: {detail}", retryable=True,
                              wait_minutes=ACCOUNT_RETRY_MINUTES)
    if status in (400, 404):
        raise TranscribeError(f"Gemini отказал {status}: {detail}", retryable=False)
    raise TranscribeError(f"Gemini вернул {status}: {detail}", retryable=True)


def _transcribe_chunk(data):
    global _last_request
    start = _call("POST", f"{BASE}/upload/v1beta/files",
                  json={"file": {"display_name": "recorder-part"}},
                  headers={"X-Goog-Upload-Protocol": "resumable",
                           "X-Goog-Upload-Command": "start",
                           "X-Goog-Upload-Header-Content-Length": str(len(data)),
                           "X-Goog-Upload-Header-Content-Type": "audio/wav"})
    upload_url = start.headers.get("x-goog-upload-url")
    if not upload_url:
        raise TranscribeError("Gemini не дал адрес загрузки", retryable=True)
    file = _call("POST", upload_url, content=data,
                 headers={"X-Goog-Upload-Offset": "0",
                          "X-Goog-Upload-Command": "upload, finalize"}).json()["file"]
    try:
        while file.get("state") == "PROCESSING":
            time.sleep(2)
            file = _call("GET", f"{BASE}/v1beta/{file['name']}").json()

        if _last_request is not None:
            time.sleep(max(0.0, _last_request + GAP_SEC - time.monotonic()))
        _last_request = time.monotonic()
        # ponytail: воркер стоит, пока ждёт паузу (до GAP_SEC на порцию);
        # если сжатие и саммари начнут опаздывать — очередь порций в базе.
        return _call("POST", f"{BASE}/v1beta/interactions", json={
            "model": MODEL,
            "input": [{"type": "audio", "uri": file["uri"], "mime_type": "audio/wav"}],
            "generation_config": {"transcription_config": {
                "language_codes": ["ru-RU"],
                "mode": {"type": "verbatim", "diarization_mode": "speaker",
                         "timestamp_granularities": ["word"]}}},
        }).json()
    finally:
        # Звук у Google не оставляем; не удалился — сам пропадёт через 48 часов.
        try:
            _call("DELETE", f"{BASE}/v1beta/{file['name']}")
        except TranscribeError:
            pass


def transcribe_file(path):
    if not API_KEY:
        raise TranscribeError("не задан GEMINI_API_KEY", retryable=True,
                              wait_minutes=ACCOUNT_RETRY_MINUTES)
    data = transcribe.loudness_normalized(path)
    if data is None:
        try:
            data = open(path, "rb").read()
        except OSError as error:
            raise TranscribeError(f"аудио не читается: {error}", retryable=False)
    try:
        pieces, duration = split_wav(data)
    except (wave.Error, EOFError) as error:
        # Без ffmpeg FLAC не разрезать. Ставим ffmpeg — часть пойдёт сама.
        raise TranscribeError(f"звук не разрезать на порции: {error}", retryable=True,
                              wait_minutes=ACCOUNT_RETRY_MINUTES)

    # ponytail: сбой на второй порции повторяет и первую (лишний запрос из
    # дневного лимита); хранить готовые порции — если лимит начнёт кончаться.
    words, ids = [], []
    for index, (offset, piece) in enumerate(pieces):
        payload = _transcribe_chunk(piece)
        ids.append(payload.get("id", ""))
        words += parse_words(payload, offset, index * SPEAKERS_PER_CHUNK)

    utterances = utterances_from(words)
    return Transcript(
        text=" ".join(u["text"] for u in utterances),
        words=words,
        utterances=utterances,
        duration_sec=duration,
        request_id=",".join(ids),
    )
