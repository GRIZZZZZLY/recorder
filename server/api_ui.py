"""JSON для фронтенда. Роуты тонкие: считает db.py, здесь только разбор и сборка.

Адреса под /api/ui, чтобы не смешивать с /api/v1/upload — контрактом диктофона.
"""
import json
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
from auth import owner

router = APIRouter(prefix="/api/ui")


class Speakers(BaseModel):
    names: dict[str, str] = {}
    me: int | None = None


def with_source(tasks):
    """Разбирает поле source из строки JSON в объект.

    В базе оно лежит строкой, а /review уже отдаёт источник объектом. Одна форма
    на оба роута: иначе фронтенду пришлось бы знать, где парсить, а где нет.
    """
    for task in tasks:
        raw = task.get("source")
        if isinstance(raw, str):
            try:
                task["source"] = json.loads(raw)
            except json.JSONDecodeError:
                task["source"] = None
    return tasks


@router.get("/digest")
def digest(_=Depends(owner)):
    with closing(db.connect()) as conn:
        tasks = with_source(db.task_rows(conn, only_open=True))
        return {
            "day": db.digest(conn, 1),
            "week": db.digest(conn, 7),
            "my_tasks": [t for t in tasks if t["mine"]],
            "has_me": bool(db.my_names(conn)),
            "recent": db.session_rows(conn, limit=5),
        }


@router.get("/sessions")
def sessions(_=Depends(owner)):
    with closing(db.connect()) as conn:
        return db.session_rows(conn)


@router.get("/sessions/{device_id}/{session}")
def session_detail(device_id: str, session: int, _=Depends(owner)):
    with closing(db.connect()) as conn:
        parts = conn.execute(
            "SELECT p.part, p.recovered, t.status AS transcript_status, t.last_error"
            " FROM parts p LEFT JOIN transcripts t"
            "   ON t.device_id=p.device_id AND t.session=p.session AND t.part=p.part"
            " WHERE p.device_id=? AND p.session=? ORDER BY p.part",
            (device_id, session),
        ).fetchall()
        if not parts:
            raise HTTPException(404, "нет такой сессии")

        summary_row = conn.execute(
            "SELECT result, status, last_error FROM summaries"
            " WHERE device_id=? AND session=?", (device_id, session)).fetchone()
        timeline, failed = db.session_timeline(conn, device_id, session)
        names = db.speaker_names(conn, device_id, session)
        speakers = sorted({u["speaker"] for u in timeline if u["speaker"] is not None})
        tasks = with_source(db.task_rows(conn, only_open=False, session=session))
        questions = [q for q in db.question_rows(conn, only_open=False)
                     if q["device_id"] == device_id and q["session"] == session]
        day = db.session_day(conn, device_id, session)

    summary = None
    if summary_row and summary_row["result"]:
        try:
            summary = json.loads(summary_row["result"])
        except json.JSONDecodeError:
            summary = None

    return {
        "device_id": device_id, "session": session, "day": day.isoformat(),
        "parts": [dict(p) for p in parts],
        "failed_parts": failed,
        "summary": summary,
        "summary_status": summary_row["status"] if summary_row else "pending",
        "summary_error": summary_row["last_error"] if summary_row else None,
        "timeline": timeline,
        "speakers": [{"speaker": s,
                      "name": names.get(s, {}).get("name", ""),
                      "is_me": bool(names.get(s, {}).get("is_me"))} for s in speakers],
        "tasks": tasks,
        "questions": questions,
    }


@router.post("/sessions/{device_id}/{session}/speakers")
def save_speakers(device_id: str, session: int, body: Speakers, _=Depends(owner)):
    with closing(db.connect()) as conn:
        for key, name in body.names.items():
            try:
                speaker = int(key)
            except ValueError:
                continue
            db.set_speaker(conn, device_id, session, speaker, name[:60], body.me == speaker)
    return {"ok": True}
