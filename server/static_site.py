"""Отдача собранного фронтенда из web/out.

Статика закрыта тем же паролем, что и данные: без этого страница с
разговорами открывалась бы любому, кто знает адрес. Каталог задаётся
переменной окружения, потому что на VPS он лежит не рядом с исходниками.
"""
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from auth import owner

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
    with_html = candidate.with_suffix(".html")
    return with_html if with_html.is_file() else None


@router.get("/{path:path}")
def site(path: str = "", _=Depends(owner)):
    if not WEB_DIR.is_dir():
        raise HTTPException(503, f"фронтенд не собран: нет каталога {WEB_DIR}")
    hit = resolve(path or "index.html")
    if hit is None:
        fallback = WEB_DIR / "404.html"
        if fallback.is_file():
            return FileResponse(fallback, status_code=404)
        raise HTTPException(404, "нет такой страницы")
    return FileResponse(hit)
