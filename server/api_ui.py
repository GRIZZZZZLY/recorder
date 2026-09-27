"""JSON для фронтенда. Роуты тонкие: считает db.py, здесь только разбор и сборка.

Адреса под /api/ui, чтобы не смешивать с /api/v1/upload — контрактом диктофона.
"""
import json
from contextlib import closing
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import db
from auth import owner

router = APIRouter(prefix="/api/ui")


class Speakers(BaseModel):
    """Имена говорящих. Ключ — "часть:номер": номер принадлежит своей части."""

    names: dict[str, str] = {}
    me: str | None = None


class RecordingDay(BaseModel):
    """День разговора, уточнённый владельцем. null — вернуть день выгрузки."""

    day: str | None = None


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
            "SELECT p.part, p.recovered, p.received_at, p.envelope, p.bytes, p.bytes_per_second,"
            "       t.status AS transcript_status, t.last_error"
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
        # Та же шкала, что у реплик: плеер показывает время разговора, а не
        # части, и сам переходит на следующую часть.
        offsets = db.part_offsets(conn, device_id, session)
        marks = db.session_marks(conn, device_id, session)
        names = db.speaker_names(conn, device_id, session)
        # Номер говорящего живёт внутри своей части: части уходят в Deepgram
        # поодиночке, и нумерация в каждой своя.
        speakers = sorted({(u["part"], u["speaker"]) for u in timeline
                           if u["speaker"] is not None})
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
        # Время первой части — час начала разговора в шапке страницы.
        "first_at": min(p["received_at"] for p in parts),
        # Огибающая части — волна в плеере. Не посчитана (старая часть или
        # нечитаемый файл) — пустой список, плеер нарисует ровную линию.
        "parts": [{**dict(p), "envelope": json.loads(p["envelope"]) if p["envelope"] else [],
                   "offset": offsets[p["part"]],
                   "seconds": db.part_seconds(p["bytes"], p["bytes_per_second"])}
                  for p in parts],
        "failed_parts": failed,
        "summary": summary,
        "summary_status": summary_row["status"] if summary_row else "pending",
        "summary_error": summary_row["last_error"] if summary_row else None,
        "timeline": timeline,
        "marks": marks,
        "speakers": [{"part": part, "speaker": speaker,
                      "name": names.get((part, speaker), {}).get("name", ""),
                      "is_me": bool(names.get((part, speaker), {}).get("is_me"))}
                     for part, speaker in speakers],
        "tasks": tasks,
        "questions": questions,
    }


def _speaker_key(raw):
    """"часть:номер" -> (часть, номер). Кривой ключ — отказ, а не тихий пропуск:
    молча потерянное имя владелец заметит нескоро."""
    part, _, speaker = raw.partition(":")
    try:
        return int(part), int(speaker)
    except ValueError:
        raise HTTPException(400, f"ключ говорящего: ожидается «часть:номер», а не {raw!r}")


@router.post("/sessions/{device_id}/{session}/speakers")
def save_speakers(device_id: str, session: int, body: Speakers, _=Depends(owner)):
    me = _speaker_key(body.me) if body.me else None
    with closing(db.connect()) as conn:
        for key, name in body.names.items():
            part, speaker = _speaker_key(key)
            db.set_speaker(conn, device_id, session, part, speaker, name[:60],
                           me == (part, speaker))
    return {"ok": True}


@router.post("/sessions/{device_id}/{session}/day")
def save_day(device_id: str, session: int, body: RecordingDay, _=Depends(owner)):
    """Поправить день разговора и пересчитать сроки задач этой сессии.

    По умолчанию днём считается день выгрузки первой части: часов у диктофона
    нет. Запись понедельника, выгруженная в среду, делает «завтра» четвергом.
    """
    day = None
    if body.day:
        try:
            day = date.fromisoformat(body.day)
        except ValueError:
            raise HTTPException(400, "дата ожидается в виде ГГГГ-ММ-ДД")
        if day > date.today():
            raise HTTPException(400, "разговор не мог состояться в будущем")
    with closing(db.connect()) as conn:
        result = db.set_recording_day(conn, device_id, session, day)
    return {"day": result.isoformat()}


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
        open_tasks = db.task_rows(conn, only_open=True)
    # Открытые задачи проекта — задачи его разговоров. Считаем здесь, а не в
    # db.project_sessions: там сессии, а связь «задача — проект» идёт только
    # через сессию, и второго источника правды заводить незачем.
    by_session = {}
    for task in open_tasks:
        by_session.setdefault((task["device_id"], task["session"]), 0)
        by_session[(task["device_id"], task["session"])] += 1
    listing = []
    for name in sorted(found):
        items = found[name]
        listing.append({
            "name": name,
            "sessions": len(items),
            "open_tasks": sum(by_session.get((i["device_id"], i["session"]), 0) for i in items),
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


@router.get("/search/notes")
def search_notes(q: str = "", _=Depends(owner)):
    """То же слово — в решениях, фактах, задачах и вопросах саммари."""
    if not q.strip():
        return []
    with closing(db.connect()) as conn:
        return db.search_notes(conn, q)


@router.post("/parts/{device_id}/{session}/{part}/retry")
def retry_part(device_id: str, session: int, part: int, _=Depends(owner)):
    """Вернуть упавшую часть в очередь расшифровки.

    Ошибки бывают временные (Deepgram лёг, сеть моргнула), и до этой кнопки
    единственным способом был поход на сервер руками.
    """
    with closing(db.connect()) as conn:
        ok = db.retry_part(conn, device_id, session, part)
        conn.commit()
    if not ok:
        raise HTTPException(404, "нечего повторять: части нет или она уже расшифрована")
    return {"ok": True}


@router.post("/sessions/{device_id}/{session}/retry-summary")
def retry_summary(device_id: str, session: int, _=Depends(owner)):
    """Вернуть саммари в очередь: та же причина, что и у части."""
    with closing(db.connect()) as conn:
        ok = db.retry_summary(conn, device_id, session)
        conn.commit()
    if not ok:
        raise HTTPException(404, "нечего повторять: саммари нет или оно готово")
    return {"ok": True}


@router.get("/status")
def status(_=Depends(owner)):
    with closing(db.connect()) as conn:
        return db.processing_status(conn)
