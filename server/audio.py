"""Отдача аудио владельцу.

Отдельный модуль, а не часть web.py: маршрут `GET /audio/{device_id}/{session}/{part}`
— часть протокола плеера (им пользуется и старый интерфейс, и новый фронтенд),
а не legacy-страница. Подключается без префикса и до catch-all — переезд под
/legacy вместе с остальными страницами владельца сломал бы воспроизведение.
"""
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

import db
from auth import owner

router = APIRouter()


@router.get("/audio/{device_id}/{session}/{part}")
def audio(device_id: str, session: int, part: int, _=Depends(owner)):
    with closing(db.connect()) as conn:
        found = db.audio_file(conn, device_id, session, part)
    if found is None:
        raise HTTPException(404, "нет такой части")
    path, media_type = found
    # FileResponse отдаёт Range, без него Safari на iPhone не проигрывает аудио.
    # Тип берётся из расширения: часть, уже сжатая в архив, приходит как FLAC.
    return FileResponse(path, media_type=media_type)
