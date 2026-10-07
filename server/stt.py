"""Точка расширения для распознавания речи.

Движков два: Deepgram (по умолчанию) и Gemini 3.5 Transcribe (gemini.py,
RECORDER_STT=gemini). Этот модуль существует ради одного: остальной сервер
не должен знать имени движка. Воркер
берёт движок через `backend()`, а форма результата описана здесь, а не внутри
реализации.

Добавить движок = добавить класс с методом `transcribe(path)` и строку в
`BACKENDS`. Локального Whisper здесь намеренно нет: пока он не нужен, его код
только мешал бы читать.
"""
import os
from dataclasses import dataclass, field


class TranscribeError(Exception):
    """Сбой распознавания.

    retryable=False — повтор не поможет (битое аудио, отказ по сути запроса).
    wait_minutes — пауза, которую назначает сама ошибка; None — паузу выбирает
    воркер по числу попыток.
    """

    def __init__(self, message, retryable=True, wait_minutes=None):
        super().__init__(message)
        self.retryable = retryable
        self.wait_minutes = wait_minutes


@dataclass
class Transcript:
    """Что обязан вернуть любой движок.

    utterances — реплики с секундами от начала своей части: абсолютную шкалу
    сессии сервер считает сам из размеров частей. Номер говорящего — метка
    диаризации внутри части, а не личность.
    """

    text: str                       # чистый текст без меток
    words: list                     # [{w, s, e, spk, c}]
    utterances: list                # [{s, e, spk, text}]
    duration_sec: float
    request_id: str = ""


class TranscriptionBackend:
    """Интерфейс движка. Реализация обязана поднимать TranscribeError."""

    name = "?"

    def transcribe(self, path) -> Transcript:
        raise NotImplementedError


class DeepgramBackend(TranscriptionBackend):
    name = "deepgram"

    def transcribe(self, path):
        # Импорт внутри метода: transcribe.py берёт типы отсюда, и на уровне
        # модуля это был бы круг.
        import transcribe

        return transcribe.transcribe_file(path)


class GeminiBackend(TranscriptionBackend):
    name = "gemini"

    def transcribe(self, path):
        import gemini

        return gemini.transcribe_file(path)


BACKENDS = {"deepgram": DeepgramBackend, "gemini": GeminiBackend}


def backend():
    name = os.environ.get("RECORDER_STT", "deepgram")
    factory = BACKENDS.get(name)
    if factory is None:
        known = ", ".join(sorted(BACKENDS))
        raise TranscribeError(
            f"неизвестный RECORDER_STT={name!r}, известны: {known}", retryable=False
        )
    return factory()
