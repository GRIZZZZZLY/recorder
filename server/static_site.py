"""Отдача собранного фронтенда из web/out.

Статика закрыта тем же паролем, что и данные: без этого страница с
разговорами открывалась бы любому, кто знает адрес. Каталог задаётся
переменной окружения, потому что на VPS он лежит не рядом с исходниками.
"""
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasicCredentials

import auth
import login
from auth import security

WEB_DIR = Path(os.environ.get(
    "RECORDER_WEB_DIR",
    Path(__file__).resolve().parent.parent / "web" / "out",
))

router = APIRouter()


def resolve(path):
    """Файл внутри сборки или None.

    Каталог отдаётся своим index.html: статический экспорт кладёт страницу
    /session/ в session/index.html. Выход за пределы каталога отвергается:
    путь приходит из запроса, а значит, может прийти каким угодно.
    """
    base = WEB_DIR.resolve()
    try:
        candidate = (base / path).resolve()
    except (OSError, ValueError):
        return None
    if base != candidate and base not in candidate.parents:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if candidate.is_file():
        return candidate
    # Предзагрузка разделов: Next просит /records/__next.records.__PAGE__.txt,
    # а сборка кладёт файл в records/__next.records/__PAGE__.txt. Без этого
    # каждая предзагрузка давала 404: переходы шли без неё, консоль краснела.
    if candidate.name.startswith("__next."):
        parts = candidate.name.split(".")
        for cut in range(2, len(parts) - 1):
            nested = candidate.parent / ".".join(parts[:cut]) / ".".join(parts[cut:])
            if nested.is_file():
                return nested
    with_html = candidate.with_suffix(".html")
    return with_html if with_html.is_file() else None


# HEAD тоже: Next.js проверяет ссылки HEAD-запросом, и 405 на них сыпался в консоль.
@router.api_route("/{path:path}", methods=["GET", "HEAD"])
def site(request: Request, response: Response, path: str = "",
         credentials: HTTPBasicCredentials = Depends(security)):
    # Страница ведёт на форму входа, а не отвечает 401: в режиме «на домашний
    # экран» Safari окно Basic-авторизации не показывает, и владелец видел бы
    # пустоту. В обычной вкладке пароль из браузера по-прежнему принимается —
    # `verify` сначала смотрит метку, потом Basic-заголовок.
    if auth.verify(request, response, credentials) is None:
        return login.redirect_to_login(request)

    if not WEB_DIR.is_dir():
        raise HTTPException(503, f"фронтенд не собран: нет каталога {WEB_DIR}")
    hit = resolve(path or "index.html")
    if hit is None:
        fallback = WEB_DIR / "404.html"
        if fallback.is_file():
            return FileResponse(fallback, status_code=404)
        raise HTTPException(404, "нет такой страницы")
    return FileResponse(hit)
