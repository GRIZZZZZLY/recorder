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
        row = conn.execute(
            "SELECT audio_path FROM parts WHERE device_id=? AND session=? AND part=?",
            (device_id, session, part),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "нет такой части")
    # FileResponse отдаёт Range, без него Safari на iPhone не проигрывает аудио.
    return FileResponse(row["audio_path"], media_type="audio/wav")
