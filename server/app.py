"""Приёмник записей диктофона. Контракт: shared/protocol.md.

Одна задача: принять часть записи, проверить целостность, положить на диск и
завести строку в SQLite. Транскрибация и саммари живут отдельно и читают ту же
базу; здесь их нет намеренно.
"""
import os
import re
import zlib
from contextlib import asynccontextmanager, closing

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

import api_ui
import audio
import envelope
import login
import static_site
import wavfmt
from auth import owner
from db import AUDIO_DIR, connect, init_storage, save_marks, set_device_state

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


MAX_MARKS = 200


def _int_header(value, name, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name}: ожидается целое число")
    if not low <= number <= high:
        raise HTTPException(400, f"{name}: вне диапазона {low}..{high}")
    return number


def _marks_header(value):
    """X-Marks: секунды от начала части через запятую. Пусто — меток нет.

    Разбор строгий: кривое значение — отказ 400, а не молча выброшенная метка.
    Владелец нажал кнопку, и потеря нажатия должна быть видна в логе диктофона,
    а не превратиться в тишину.
    """
    if not value or not value.strip():
        return []
    pieces = [piece.strip() for piece in value.split(",") if piece.strip()]
    if len(pieces) > MAX_MARKS:
        raise HTTPException(400, f"X-Marks: больше {MAX_MARKS} меток в одной части")
    marks = []
    for piece in pieces:
        try:
            offset = float(piece)
        except ValueError:
            raise HTTPException(400, f"X-Marks: не число — {piece!r}")
        # Верхняя граница — час: часть протокола длится до 10 минут, запас
        # десятикратный, но метка за пределами части означает ошибку прошивки.
        if not 0 <= offset <= 3600:
            raise HTTPException(400, f"X-Marks: вне диапазона 0..3600 — {piece}")
        marks.append(offset)
    return marks


@app.post("/api/v1/upload")
async def upload(
    request: Request,
    authorization: str = Header(None),
    x_device_id: str = Header(None),
    x_session: str = Header(None),
    x_part: str = Header(None),
    x_crc32: str = Header(None),
    x_recovered: str = Header("0"),
    x_marks: str = Header(None),
    x_session_parts: str = Header(None),
    x_battery: str = Header(None),
    x_firmware: str = Header(None),
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

    marks = _marks_header(x_marks)
    # Сколько частей в сессии, если диктофон знает. Меньше номера этой части
    # быть не может — такое значение означает ошибку прошивки, отказ 400.
    expected_parts = None
    if x_session_parts is not None and x_session_parts.strip():
        expected_parts = _int_header(x_session_parts, "X-Session-Parts", part, 99)

    # Заряд и версия прошивки — необязательные: прошивка на плате их ещё не
    # шлёт. Кривое значение отвергаем, как и остальные заголовки: молча принять
    # мусор хуже, чем отказать и показать это в логе диктофона.
    battery = None
    if x_battery is not None and x_battery.strip():
        battery = _int_header(x_battery, "X-Battery", 0, 100)
    firmware = None
    if x_firmware is not None and x_firmware.strip():
        firmware = x_firmware.strip()[:32]
        if not re.fullmatch(r"[0-9A-Za-z.\-_+]+", firmware):
            raise HTTPException(400, "X-Firmware: допустимы буквы, цифры и .-_+")

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
                # Тот же файл пришёл ещё раз: подтверждаем, аудио не трогаем.
                # Метки пишем и здесь: повтор идемпотентен, а первая попытка
                # могла оборваться после записи файла, но до их сохранения.
                save_marks(conn, x_device_id, session, part, marks)
                set_device_state(conn, x_device_id, battery, firmware)
                conn.commit()
                return JSONResponse({"status": "duplicate", **body}, status_code=200)
            # Уже принятое аудио не перезаписываем молча.
            raise HTTPException(
                409,
                f"часть уже принята с другими данными: "
                f"{row['bytes']} байт, crc {row['crc32']}",
            )

        tmp_path.replace(final_path)
        # Формат — из заголовка самого файла: у моно с XIAO Sense секунда весит
        # 32 000 байт, у стерео с Recorder RevA 64 000. Шкала времени и волна
        # считаются из размера, поэтому множитель обязан быть свой у каждой
        # части. None (заголовок не прочитался) означает «считать как моно».
        bps = wavfmt.bytes_per_second(final_path)
        conn.execute(
            "INSERT INTO parts (device_id, session, part, bytes, crc32, recovered,"
            " audio_path, expected_parts, bytes_per_second) VALUES (?,?,?,?,?,?,?,?,?)",
            (x_device_id, session, part, received, actual_crc, recovered,
             str(final_path), expected_parts, bps),
        )
        save_marks(conn, x_device_id, session, part, marks)
        set_device_state(conn, x_device_id, battery, firmware)
        # Огибающая считается сразу: файл уже на диске и ещё горячий в кэше.
        envelope.store(conn, x_device_id, session, part, final_path, bps)
        conn.commit()

    return JSONResponse({"status": "stored", **body}, status_code=201)


@app.get("/api/v1/sessions")
def sessions(_=Depends(owner)):
    """Что уже принято. Нужен для проверки руками и для очереди обработки.

    Список сессий, размеры и время выгрузки — не для чужих глаз, поэтому
    роут закрыт тем же паролем владельца, что и остальной JSON-API. Прошивка
    диктофона этот роут не вызывает (только POST /api/v1/upload с токеном
    устройства), так что владелец не пострадает."""
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT device_id, session, COUNT(*) AS parts, SUM(bytes) AS bytes,"
            " SUM(recovered) AS recovered_parts, MAX(received_at) AS last"
            " FROM parts GROUP BY device_id, session ORDER BY device_id, session"
        ).fetchall()
    return [dict(row) for row in rows]


app.include_router(api_ui.router)
# Форма входа — до catch-all и без пароля: иначе за паролем оказалась бы сама
# страница ввода пароля.
app.include_router(login.router)
# Catch-all регистрируется последним из всех: /api/v1/... объявлены декораторами
# выше в этом же файле, а значит попадают в app.router раньше него — но легко
# забыть при следующей правке, поэтому регистрация вынесена в самый конец файла.
app.include_router(static_site.router)
