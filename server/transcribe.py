"""Расшифровка части записи через Deepgram (готовые файлы, не поток).

Контракт сверен с developers.deepgram.com 12 сентября 2026:
  POST https://api.deepgram.com/v1/listen
  Authorization: Token <ключ>            (Bearer — только для JWT из /v1/auth/grant)
  Content-Type: audio/wav, тело — байты файла

Русский язык на nova-3 в одноязычном режиме поддерживается, отдельная
многоязычная модель не нужна и стоит дороже.
"""
import json
import os
from dataclasses import dataclass, field

import httpx

API_URL = os.environ.get("DEEPGRAM_URL", "https://api.deepgram.com/v1/listen")
API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
# Deepgram обрывает запрос на 504, если обработка идёт дольше 10 минут.
# Ждать дольше бессмысленно.
TIMEOUT_SEC = int(os.environ.get("DEEPGRAM_TIMEOUT", 660))

PARAMS = {
    "model": "nova-3",
    "language": "ru",
    # diarize=true помечен устаревшим и закрепляет диаризатор первой версии.
    "diarize_model": "latest",
    "punctuate": "true",
    "paragraphs": "true",
    "utterances": "true",
    "smart_format": "true",
}

# Повтор бессмысленен: ключ, права, оплата, испорченное аудио.
FATAL_STATUSES = {400, 401, 402, 403, 404}


class TranscribeError(Exception):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Transcript:
    text: str                       # чистый текст без меток
    words: list                     # [{w, s, e, spk}]
    utterances: list                # [{s, e, spk, text}], секунды от начала части
    duration_sec: float
    request_id: str = ""


def _first_alternative(payload):
    channels = payload.get("results", {}).get("channels") or []
    if not channels:
        return {}
    alternatives = channels[0].get("alternatives") or []
    return alternatives[0] if alternatives else {}


def plain_text(payload):
    return (_first_alternative(payload).get("transcript") or "").strip()


def extract_utterances(payload):
    """Реплики с говорящим и временем. Без диаризации — одна реплика на часть.

    Номер говорящего — метка диаризации, а не личность. Имена подставляются
    позже из ручной разметки владельца.
    """
    utterances = payload.get("results", {}).get("utterances") or []
    result = []
    for item in utterances:
        said = (item.get("transcript") or "").strip()
        if said:
            result.append({
                "s": float(item.get("start") or 0.0),
                "e": float(item.get("end") or 0.0),
                "spk": item.get("speaker"),
                "text": said,
            })
    if result:
        return result

    text = plain_text(payload)
    if text:
        duration = float(payload.get("metadata", {}).get("duration") or 0.0)
        return [{"s": 0.0, "e": duration, "spk": None, "text": text}]
    return []


def extract_words(payload):
    words = _first_alternative(payload).get("words") or []
    # Держим только то, что нужно интерфейсу и поиску: целиком ответ Deepgram
    # раздувает базу в разы без пользы. c — уверенность распознавания слова:
    # по ней помечаются сомнительные числа в спорных местах.
    return [
        {
            "w": word.get("punctuated_word") or word.get("word"),
            "s": word.get("start"),
            "e": word.get("end"),
            "spk": word.get("speaker"),
            "c": word.get("confidence"),
        }
        for word in words
    ]


def parse_payload(payload):
    metadata = payload.get("metadata", {})
    return Transcript(
        text=plain_text(payload),
        words=extract_words(payload),
        utterances=extract_utterances(payload),
        duration_sec=float(metadata.get("duration") or 0.0),
        request_id=metadata.get("request_id", ""),
    )


def transcribe_file(path):
    if not API_KEY:
        raise TranscribeError("не задан DEEPGRAM_API_KEY", retryable=False)

    try:
        audio = open(path, "rb").read()
    except OSError as error:
        raise TranscribeError(f"аудио не читается: {error}", retryable=False)

    if len(audio) <= 44:
        raise TranscribeError("в файле нет аудио", retryable=False)

    headers = {
        "Authorization": f"Token {API_KEY}",
        "Content-Type": "audio/wav",
    }

    try:
        response = httpx.post(
            API_URL, params=PARAMS, headers=headers, content=audio,
            timeout=TIMEOUT_SEC,
        )
    except httpx.TimeoutException:
        raise TranscribeError(f"Deepgram не ответил за {TIMEOUT_SEC} с", retryable=True)
    except httpx.HTTPError as error:
        raise TranscribeError(f"сеть недоступна: {error}", retryable=True)

    if response.status_code != 200:
        detail = response.text[:300]
        if response.status_code in FATAL_STATUSES:
            raise TranscribeError(
                f"Deepgram отказал {response.status_code}: {detail}", retryable=False
            )
        # 429 и 5xx повторяем, паузу выбирает воркер.
        raise TranscribeError(
            f"Deepgram вернул {response.status_code}: {detail}", retryable=True
        )

    try:
        payload = response.json()
    except json.JSONDecodeError:
        raise TranscribeError("ответ Deepgram не JSON", retryable=True)

    return parse_payload(payload)
