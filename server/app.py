"""Приёмник записей диктофона. Контракт: shared/protocol.md.

Одна задача: принять часть записи, проверить целостность, положить на диск и
завести строку в SQLite. Транскрибация и саммари живут отдельно и читают ту же
базу; здесь их нет намеренно.
"""
import os
import re
import zlib
from contextlib import asynccontextmanager, closing

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

import api_ui
import audio
import static_site
from db import AUDIO_DIR, connect, init_storage

MAX_BYTES = int(os.environ.get("RECORDER_MAX_BYTES", 64 * 1024 * 1024))
# Токен обязателен. Пустое значение означает незакрытый сервер, это отказ.
DEVICE_TOKEN = os.environ.get("RECORDER_DEVICE_TOKEN", "")

DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_storage()
    yield


app = FastAPI(title="Recorder", version="1", lifespan=lifespan)
# Часть протокола плеера, должна остаться на GET /audio/{device_id}/{session}/{part}.
# Регистрируется до catch-all — см. конец файла.
app.include_router(audio.router)


def _int_header(value, name, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name}: ожидается целое число")
    if not low <= number <= high:
        raise HTTPException(400, f"{name}: вне диапазона {low}..{high}")
    return number


@app.post("/api/v1/upload")
async def upload(
    request: Request,
    authorization: str = Header(None),
    x_device_id: str = Header(None),
    x_session: str = Header(None),
    x_part: str = Header(None),
    x_crc32: str = Header(None),
    x_recovered: str = Header("0"),
):
    if not DEVICE_TOKEN or authorization != f"Bearer {DEVICE_TOKEN}":
        raise HTTPException(401, "неверный токен устройства")

    if not x_device_id or not DEVICE_ID_RE.match(x_device_id):
        raise HTTPException(400, "X-Device-Id: пусто или недопустимые символы")
    session = _int_header(x_session, "X-Session", 1, 9999)
    part = _int_header(x_part, "X-Part", 1, 99)
    recovered = 1 if x_recovered == "1" else 0

    if not x_crc32 or not re.fullmatch(r"[0-9a-f]{8}", x_crc32):
        raise HTTPException(400, "X-Crc32: ожидается 8 шестнадцатеричных цифр")

    declared = _int_header(
        request.headers.get("content-length"), "Content-Length", 1, MAX_BYTES
    )

    name = f"{x_device_id}_S{session:04d}_P{part:02d}.wav"
    final_path = AUDIO_DIR / name
    # Пишем во временный файл: незавершённая загрузка не должна выглядеть как
    # готовый файл, ровно по той же причине, что и .TMP на карте диктофона.
    tmp_path = final_path.with_suffix(".part")

    received = 0
    crc = 0
    try:
        with open(tmp_path, "wb") as handle:
            async for chunk in request.stream():
                received += len(chunk)
                if received > declared:
                    raise HTTPException(400, "тело длиннее Content-Length")
                crc = zlib.crc32(chunk, crc)
                handle.write(chunk)
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise

    actual_crc = f"{crc & 0xFFFFFFFF:08x}"

    if received != declared:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(400, f"получено {received} байт вместо {declared}")
    if actual_crc != x_crc32:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(422, f"CRC не совпал: {actual_crc} вместо {x_crc32}")

    body = {
        "session": session,
        "part": part,
        "bytes": received,
        "crc32": actual_crc,
    }

    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT bytes, crc32 FROM parts WHERE device_id=? AND session=? AND part=?",
            (x_device_id, session, part),
        ).fetchone()

        if row is not None:
            tmp_path.unlink(missing_ok=True)
            if row["bytes"] == received and row["crc32"] == actual_crc:
                # Тот же файл пришёл ещё раз: подтверждаем, ничего не трогаем.
                return JSONResponse({"status": "duplicate", **body}, status_code=200)
            # Уже принятое аудио не перезаписываем молча.
            raise HTTPException(
                409,
                f"часть уже принята с другими данными: "
                f"{row['bytes']} байт, crc {row['crc32']}",
            )

        tmp_path.replace(final_path)
        conn.execute(
            "INSERT INTO parts (device_id, session, part, bytes, crc32, recovered,"
            " audio_path) VALUES (?,?,?,?,?,?,?)",
            (x_device_id, session, part, received, actual_crc, recovered,
             str(final_path)),
        )
        conn.commit()

    return JSONResponse({"status": "stored", **body}, status_code=201)


@app.get("/api/v1/sessions")
def sessions():
    """Что уже принято. Нужен для проверки руками и для очереди обработки."""
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT device_id, session, COUNT(*) AS parts, SUM(bytes) AS bytes,"
            " SUM(recovered) AS recovered_parts, MAX(received_at) AS last"
            " FROM parts GROUP BY device_id, session ORDER BY device_id, session"
        ).fetchall()
    return [dict(row) for row in rows]


app.include_router(api_ui.router)
# Catch-all регистрируется последним из всех: /api/v1/... объявлены декораторами
# выше в этом же файле, а значит попадают в app.router раньше него — но легко
# забыть при следующей правке, поэтому регистрация вынесена в самый конец файла.
app.include_router(static_site.router)
