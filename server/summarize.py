"""Саммари транскрипта через Claude Code в headless-режиме.

Проверено на VPS 12 сентября 2026, подробности в docs/step0-claude-headless-check.md.
Два факта оттуда важны для кода:
  * структурированный ответ формируется отдельным ходом, поэтому --max-turns
    меньше двух даёт subtype=error_max_turns и пустой structured_output;
  * истёкший OAuth даёт 401 и одиннадцать повторов с паузами, а не быстрый отказ,
    поэтому снаружи обязателен таймаут.
"""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

import db
import textnorm

CLAUDE_BIN = os.environ.get("RECORDER_CLAUDE_BIN", "claude")
MODEL = os.environ.get("RECORDER_SUMMARY_MODEL", "claude-opus-5")
TIMEOUT_SEC = int(os.environ.get("RECORDER_SUMMARY_TIMEOUT", 900))
# claude — Claude Code по подписке; gemini — Gemini API (summarize_gemini).
ENGINE = os.environ.get("RECORDER_SUMMARY_ENGINE", "claude")
GEMINI_MODELS = os.environ.get(
    "RECORDER_SUMMARY_GEMINI_MODELS", "gemini-3.8-flash,gemini-3.5-flash-lite").split(",")

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "shared" / "summary.schema.json"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "summary_ru.txt"


class SummaryError(Exception):
    """Ошибка саммари. retryable=False означает, что повтор не поможет."""

    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


def build_command():
    schema = json.dumps(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
                        ensure_ascii=False, separators=(",", ":"))
    return [
        CLAUDE_BIN,
        "-p",
        "--tools", "",                  # никаких инструментов: только текст на вход
        # Структурированный ответ — отдельный ход. На живом вызове 12.09 модель
        # заняла ровно 3 хода; при лимите 3 запаса не было бы. 6 — с запасом.
        "--max-turns", "6",
        "--no-session-persistence",
        "--model", MODEL,
        "--output-format", "json",
        "--json-schema", schema,
        "--system-prompt", PROMPT_PATH.read_text(encoding="utf-8"),
    ]


def summarize(transcript):
    """Возвращает разобранный результат саммари. Бросает SummaryError.

    Транскрипт подаётся на stdin, а не аргументом: часовой разговор не влезет
    в командную строку.
    """
    if not transcript.strip():
        raise SummaryError("пустой транскрипт", retryable=False)
    if ENGINE == "gemini":
        return summarize_gemini(transcript)

    try:
        completed = subprocess.run(
            build_command(),
            input=transcript,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_SEC,
            # Выкладка стирает и заново создаёт /opt/recorder/server. Воркер,
            # не перезапущенный после неё, сидит в удалённом каталоге, и claude
            # (node) падает на старте с ENOENT uv_cwd — так 7 октября 2026
            # молча не строились саммари 0014–0016. Каталог времянок не стирается.
            cwd=tempfile.gettempdir(),
        )
    except FileNotFoundError:
        raise SummaryError(f"не найден исполняемый файл {CLAUDE_BIN}", retryable=False)
    except subprocess.TimeoutExpired:
        raise SummaryError(f"нет ответа за {TIMEOUT_SEC} с", retryable=True)

    if completed.returncode != 0:
        raise SummaryError(
            f"claude вернул код {completed.returncode}: {completed.stderr.strip()[:300]}"
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise SummaryError(f"ответ не JSON: {completed.stdout[:300]}")

    if payload.get("is_error") or payload.get("subtype") != "success":
        raise SummaryError(
            f"claude не справился: subtype={payload.get('subtype')},"
            f" result={str(payload.get('result'))[:200]}"
        )

    result = payload.get("structured_output")
    if not isinstance(result, dict) or "summary" not in result:
        raise SummaryError(f"пустой structured_output: {str(payload)[:300]}")

    # Стоимость и расход возвращаются справочно: оплата идёт по подписке.
    result["_meta"] = {
        "model": MODEL,
        "cost_usd": payload.get("total_cost_usd"),
        "duration_ms": payload.get("duration_ms"),
    }
    return result


def summarize_gemini(transcript):
    """То же саммари через Gemini API тем же ключом, что и расшифровка.

    Бесплатный тариф, замер 7 октября 2026: Pro отвечает 429 (не бесплатна),
    3.8 Flash — 503 «high demand», 3.5 Flash-Lite отвечает. Поэтому модели
    идут списком: перегружена или кончился лимит — берём следующую.
    """
    import gemini

    if not gemini.API_KEY:
        raise SummaryError("не задан GEMINI_API_KEY", retryable=False)
    body = {
        "systemInstruction": {"parts": [{"text": PROMPT_PATH.read_text(encoding="utf-8")}]},
        "contents": [{"role": "user", "parts": [{"text": transcript}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
        },
    }
    errors = []
    for model in GEMINI_MODELS:
        started = time.monotonic()
        try:
            response = httpx.post(
                f"{gemini.BASE}/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": gemini.API_KEY}, json=body, timeout=TIMEOUT_SEC,
            )
        except httpx.HTTPError as error:
            errors.append(f"{model}: {error}")
            continue
        if response.status_code in (429, 500, 503):
            errors.append(f"{model}: {response.status_code}")
            continue
        if response.status_code != 200:
            raise SummaryError(f"Gemini {model} отказал {response.status_code}:"
                               f" {response.text[:300]}",
                               retryable=response.status_code not in (400, 404))
        try:
            parts = response.json()["candidates"][0]["content"]["parts"]
            result = json.loads("".join(p.get("text", "") for p in parts
                                        if not p.get("thought")))
        except (KeyError, IndexError, ValueError):
            raise SummaryError(f"ответ Gemini {model} не разобран: {response.text[:300]}")
        if not isinstance(result, dict) or "summary" not in result:
            raise SummaryError(f"в ответе Gemini {model} нет summary: {str(result)[:300]}")
        result["_meta"] = {"model": model, "cost_usd": 0,
                           "duration_ms": int((time.monotonic() - started) * 1000)}
        return result
    raise SummaryError("все модели Gemini заняты: " + "; ".join(errors))


def is_auth_failure(message):
    """Истёкший токен лечится только руками, повторять бессмысленно."""
    lowered = message.lower()
    return "authentication_error" in lowered or "oauth" in lowered or "401" in lowered


# --- источник каждого вывода ------------------------------------------------

SOURCED_LISTS = db.SOURCED_LISTS
# Метка времени у модели может отличаться от реплики на соседнюю: ищем рядом.
SOURCE_WINDOW_SEC = 90


def _normalize(text):
    return textnorm.normalize(text)


def find_source(item, timeline):
    """Реплика, на которой основан пункт саммари.

    Сначала дословная цитата рядом с указанным временем, затем цитата где
    угодно, затем просто ближайшая по времени реплика. Возвращает словарь с
    полем confirmed: True только при найденной цитате. Ложь вместо
    подтверждения хуже честного «источник не подтверждён».
    """
    if not timeline:
        return None
    secs = db.parse_abs(item.get("time"))
    quote = _normalize(item.get("quote"))

    def nearby(u):
        return secs is None or abs(u["start_abs"] - secs) <= SOURCE_WINDOW_SEC

    def contains(u):
        return len(quote) >= 6 and quote in _normalize(u["text"])

    for pool in ([u for u in timeline if nearby(u)], timeline):
        hits = [u for u in pool if contains(u)]
        if hits:
            best = hits[0] if secs is None else min(hits, key=lambda u: abs(u["start_abs"] - secs))
            return {"part": best["part"], "idx": best["idx"], "start": best["start"],
                    "start_abs": best["start_abs"], "confirmed": True}

    if secs is None:
        return None
    best = min(timeline, key=lambda u: abs(u["start_abs"] - secs))
    return {"part": best["part"], "idx": best["idx"], "start": best["start"],
            "start_abs": best["start_abs"], "confirmed": False}


def attach_sources(result, timeline):
    """Добавляет каждому пункту поле source. Пункты без источника остаются,
    но помечены: удалять выводы модели молча нельзя."""
    for name in SOURCED_LISTS:
        for item in result.get(name) or []:
            item["source"] = find_source(item, timeline)
    return result
