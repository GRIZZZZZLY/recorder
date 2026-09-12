"""JSON для фронтенда. Роуты тонкие: считает db.py, здесь только разбор и сборка.

Адреса под /api/ui, чтобы не смешивать с /api/v1/upload — контрактом диктофона.
"""
import json
from contextlib import closing
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
from auth import owner

router = APIRouter(prefix="/api/ui")


class Speakers(BaseModel):
    names: dict[str, str] = {}
    me: int | None = None


class TaskStatus(BaseModel):
    status: Literal["open", "done", "dismissed"]


class QuestionState(BaseModel):
    state: Literal["open", "answered", "dropped"]


class Alias(BaseModel):
    alias: str
    canonical: str


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


@router.get("/tasks")
def tasks(show: str = "open", session: int | None = None, _=Depends(owner)):
    with closing(db.connect()) as conn:
        rows = with_source(db.task_rows(conn, only_open=show != "all", mine=show == "mine",
                                        overdue=show == "overdue", session=session))
        return {"tasks": rows, "has_me": bool(db.my_names(conn))}


@router.post("/tasks/{device_id}/{session}/{task_id}")
def set_task(device_id: str, session: int, task_id: str, body: TaskStatus, _=Depends(owner)):
    with closing(db.connect()) as conn:
        db.set_task_status(conn, device_id, session, task_id, body.status)
    return {"ok": True}


@router.get("/questions")
def questions(show: str = "open", _=Depends(owner)):
    with closing(db.connect()) as conn:
        rows = db.question_rows(conn, only_open=show != "all")
    for row in rows:
        row["day"] = row["day"].isoformat() if row["day"] else None
    return rows


@router.post("/questions/{device_id}/{session}/{question_id}")
def set_question(device_id: str, session: int, question_id: str,
                 body: QuestionState, _=Depends(owner)):
    with closing(db.connect()) as conn:
        db.set_question_state(conn, device_id, session, question_id, body.state)
    return {"ok": True}


@router.get("/projects")
def projects(_=Depends(owner)):
    with closing(db.connect()) as conn:
        found = db.project_sessions(conn)
        aliases = db.alias_map(conn)
    listing = []
    for name in sorted(found):
        items = found[name]
        listing.append({
            "name": name,
            "sessions": len(items),
            "last_day": items[-1]["day"].isoformat() if items[-1]["day"] else None,
        })
    return {"projects": listing,
            "aliases": [{"alias": a, "canonical": c} for a, c in sorted(aliases.items())]}


# ВАЖНО: /projects/aliases объявлен раньше /projects/{name}. При одинаковом
# методе более ранний маршрут забрал бы себе POST/GET на "aliases" как имя
# проекта; здесь методы разные (POST и GET), но порядок сохраняем как
# документированное намерение, а не как случайное совпадение.
@router.post("/projects/aliases")
def add_alias(body: Alias, _=Depends(owner)):
    with closing(db.connect()) as conn:
        db.set_alias(conn, body.alias[:80], body.canonical[:80])
    return {"ok": True}


@router.get("/projects/{name}")
def project(name: str, _=Depends(owner)):
    with closing(db.connect()) as conn:
        found = db.project_sessions(conn)
        canonical = db.canonical_topic(db.alias_map(conn), name)
    items = found.get(canonical) or found.get(name) or []
    return {
        "name": canonical,
        "sessions": [{
            "device_id": item["device_id"], "session": item["session"],
            "day": item["day"].isoformat() if item["day"] else None,
            "summary": item["result"].get("summary"),
            "decisions": item["result"].get("decisions") or [],
            "tasks": item["result"].get("tasks") or [],
            "open_questions": item["result"].get("open_questions") or [],
        } for item in items],
    }


@router.get("/review")
def review(_=Depends(owner)):
    with closing(db.connect()) as conn:
        items = db.review_items(conn)
    for item in items:
        item["day"] = item["day"].isoformat() if item["day"] else None
    return items


@router.get("/search")
def search(q: str = "", _=Depends(owner)):
    if not q.strip():
        return []
    with closing(db.connect()) as conn:
        return db.search_segments(conn, q)


@router.get("/status")
def status(_=Depends(owner)):
    with closing(db.connect()) as conn:
        return db.processing_status(conn)
