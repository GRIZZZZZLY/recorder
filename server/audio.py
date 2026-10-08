"""Отдача аудио владельцу.

Отдельный модуль, а не часть web.py: маршрут `GET /audio/{device_id}/{session}/{part}`
— часть протокола плеера (им пользуется и старый интерфейс, и новый фронтенд),
а не legacy-страница. Подключается без префикса и до catch-all — переезд под
/legacy вместе с остальными страницами владельца сломал бы воспроизведение.
"""
import os
import subprocess
import uuid
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

import db
from auth import owner
from transcribe import FFMPEG

router = APIRouter()

# Слушать запись с XIAO Sense как есть почти нельзя: средняя громкость
# -37..-41 дБ, на телефоне на максимуме приходится подносить динамик к уху.
# Владелец слушает копию с выровненной громкостью (громче, чем копия для
# расшифровки); исходник не меняется. 0 выключает.
PLAY_LOUDNORM = os.environ.get("RECORDER_PLAY_LOUDNORM", "1") != "0"
# Громче, чем для расшифровки (-20): -16 LUFS — привычная громкость речи
# на телефоне. Пик не выше -1,5 дБ, чтобы не хрипело. speechnorm поднимает
# тихие фразы (дальний собеседник) не больше чем вдвое (+6 дБ); шум в паузах
# растёт так же. Шумодавы (arnndn, noisereduce) владелец на слух отверг —
# звучат грязнее исходника. Сменив фильтр, удалить кэш *.loud.flac на VPS.
_NORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
PLAY_FILTER = f"highpass=f=80,{_NORM},speechnorm=e=2:r=0.0005:l=1,{_NORM}"


def loud_copy(path):
    """Громкая копия части рядом с исходником: <часть>.loud.flac. Делается при
    первом прослушивании и дальше берётся готовой. None — отдать исходник:
    сбой ffmpeg не должен лишать владельца записи."""
    if not PLAY_LOUDNORM:
        return None
    loud = path.with_name(path.name.split(".")[0] + ".loud.flac")
    if loud.exists():
        return loud
    tmp = loud.with_name(f"{loud.name}.{uuid.uuid4().hex}.tmp")
    try:
        done = subprocess.run(
            [FFMPEG, "-nostdin", "-loglevel", "error", "-i", str(path),
             "-af", PLAY_FILTER, "-ar", "16000", "-ac", "1",
             "-c:a", "flac", "-f", "flac", str(tmp)],
            capture_output=True, timeout=300,
        )
        if done.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            return None
        # Два одновременных запроса делают по своей копии; последняя просто
        # заменит первую — содержимое одинаковое.
        tmp.replace(loud)
        return loud
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        tmp.unlink(missing_ok=True)


def serve(device_id, session, part, headers=None):
    """Часть записи: громкая копия, если получилась, иначе исходник. Общая для
    владельца и для ссылки гостя (share.py); `headers` — заголовки гостя."""
    with closing(db.connect()) as conn:
        found = db.audio_file(conn, device_id, session, part)
    if found is None:
        raise HTTPException(404, "нет такой части", headers=headers)
    path, media_type = found
    loud = loud_copy(path)
    if loud is not None:
        path, media_type = loud, "audio/flac"
    # FileResponse отдаёт Range, без него Safari на iPhone не проигрывает аудио.
    # Тип берётся из расширения: часть, уже сжатая в архив, приходит как FLAC.
    return FileResponse(path, media_type=media_type, headers=headers)


@router.get("/audio/{device_id}/{session}/{part}")
def audio(device_id: str, session: int, part: int, _=Depends(owner)):
    return serve(device_id, session, part)
