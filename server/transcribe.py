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
import subprocess

import httpx

# Форма результата и класс ошибки общие для всех движков: stt.py. Deepgram —
# одна из реализаций, а не определение того, как выглядит расшифровка.
from stt import Transcript, TranscribeError

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

# Ключ, права или оплата: лечится руками владельца, поэтому повтор раз в час,
# а не отказ навсегда. Владелец вписал ключ — части уходят в Deepgram сами.
ACCOUNT_STATUSES = {401, 402, 403}
ACCOUNT_RETRY_MINUTES = 60
# Сам запрос или файл: повторять нечего.
FATAL_STATUSES = {400, 404}


def _first_alternative(payload):
    channels = payload.get("results", {}).get("channels") or []
    if not channels:
        return {}
    alternatives = channels[0].get("alternatives") or []
    return alternatives[0] if alternatives else {}


def plain_text(payload):
    return (_first_alternative(payload).get("transcript") or "").strip()


def _sentences(payload):
    """Реплики из предложений абзацев.

    Замерено на живой записи 13 сентября 2026: готовая нарезка Deepgram
    (`results.utterances`) режет по паузе, поэтому фраза с запинкой рвётся на
    три строки, а быстрый вопрос-ответ слепляется в одну строку с чужим
    говорящим. Предложения абзацев держат оба случая: «Что смотрела?» и «На
    YouTube песни всякие.» достались разным номерам, как и было сказано.

    Говорящий лежит у абзаца, у предложения его нет.
    """
    paragraphs = (
        _first_alternative(payload).get("paragraphs") or {}
    ).get("paragraphs") or []
    result = []
    for paragraph in paragraphs:
        speaker = paragraph.get("speaker")
        for sentence in paragraph.get("sentences") or []:
            said = (sentence.get("text") or "").strip()
            if said:
                result.append({
                    "s": float(sentence.get("start") or 0.0),
                    "e": float(sentence.get("end") or 0.0),
                    "spk": speaker,
                    "text": said,
                })
    return result


def extract_utterances(payload):
    """Реплики с говорящим и временем. Без диаризации — одна реплика на часть.

    Номер говорящего — метка диаризации, а не личность. Имена подставляются
    позже из ручной разметки владельца.
    """
    result = _sentences(payload)
    if result:
        return result

    # Абзацев нет: старый ответ или язык без их поддержки. Берём нарезку по паузам.
    utterances = payload.get("results", {}).get("utterances") or []
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


FFMPEG = os.environ.get("RECORDER_FFMPEG", "ffmpeg")
# Выравнивание громкости перед расшифровкой. Микрофон XIAO Sense пишет тихо:
# средняя громкость -37..-41 дБ при норме для речи около -20. Замер 7 октября
# 2026 на записи 0014 (4 мин, офис): с выравниванием 137 слов вместо 106,
# вернулись пропущенные тихие фразы и потерянное «не» («не будут»). Файл на
# диске не меняется — только то, что слышит Deepgram. 0 выключает.
LOUDNORM = os.environ.get("RECORDER_STT_LOUDNORM", "1") != "0"
LOUDNORM_FILTER = "highpass=f=80,loudnorm=I=-20:TP=-2:LRA=11"


def loudness_normalized(path):
    """WAV с выровненной громкостью той же длины, или None — тогда уходит
    исходный файл. Сбой ffmpeg расшифровку не останавливает."""
    if not LOUDNORM:
        return None
    try:
        done = subprocess.run(
            [FFMPEG, "-nostdin", "-loglevel", "error", "-i", str(path),
             "-af", LOUDNORM_FILTER, "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"],
            capture_output=True, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0 or len(done.stdout) <= 44:
        return None
    return done.stdout


def transcribe_file(path):
    if not API_KEY:
        raise TranscribeError("не задан DEEPGRAM_API_KEY", retryable=True,
                              wait_minutes=ACCOUNT_RETRY_MINUTES)

    try:
        audio = open(path, "rb").read()
    except OSError as error:
        raise TranscribeError(f"аудио не читается: {error}", retryable=False)

    if len(audio) <= 44:
        raise TranscribeError("в файле нет аудио", retryable=False)

    # Часть, уже переведённую в архив, отдаём как FLAC: Deepgram принимает
    # оба формата, но чужой Content-Type — повод для отказа 400.
    content_type = "audio/flac" if str(path).endswith(".flac") else "audio/wav"
    louder = loudness_normalized(path)
    if louder is not None:
        audio, content_type = louder, "audio/wav"
    headers = {"Authorization": f"Token {API_KEY}", "Content-Type": content_type}

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
        if response.status_code in ACCOUNT_STATUSES:
            raise TranscribeError(
                f"Deepgram отказал {response.status_code}: {detail}", retryable=True,
                wait_minutes=ACCOUNT_RETRY_MINUTES,
            )
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
