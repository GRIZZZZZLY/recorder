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
