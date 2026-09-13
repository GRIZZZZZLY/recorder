"""Аутентификация владельца. Basic поверх HTTPS: пользователь один.

Модуль отдельный, потому что зависимость `owner` нужна и страницам, и JSON-API,
и отдаче статики, а страницы уходят вместе с web.py.
"""
import hmac
import os
import secrets
import time
from hashlib import sha256

from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

OWNER_USER = os.environ.get("RECORDER_OWNER_USER", "owner")
OWNER_PASSWORD = os.environ.get("RECORDER_OWNER_PASSWORD", "")
COOKIE_NAME = "recorder_owner"
COOKIE_SECONDS = int(os.environ.get("RECORDER_COOKIE_DAYS", "30")) * 86400

security = HTTPBasic(auto_error=False)


def _sign(expires: int) -> str:
    """Подпись на пароле владельца: смена пароля разом обесценивает все выданные
    метки, поэтому отдельный список отзыва держать не нужно."""
    body = f"{OWNER_USER}.{expires}".encode()
    return hmac.new(OWNER_PASSWORD.encode(), body, sha256).hexdigest()


def _valid_until(cookie: str | None) -> int:
    """Срок годности метки, если она наша и не просрочена, иначе 0."""
    if not cookie or "." not in cookie:
        return 0
    stamp, _, signature = cookie.partition(".")
    if not stamp.isdigit():
        return 0
    expires = int(stamp)
    if expires <= time.time():
        return 0
    return expires if hmac.compare_digest(signature, _sign(expires)) else 0


def _scheme(request: Request) -> str:
    """Заголовку верим, потому что снаружи к приложению не подключиться: uvicorn
    слушает 127.0.0.1, а заголовок переписывает nginx на каждом запросе."""
    return request.headers.get("x-forwarded-proto", request.url.scheme)


def _issue(request: Request, response: Response) -> None:
    """Метка на месяц: иначе Safari на телефоне спрашивает пароль после каждого
    своего перезапуска."""
    expires = int(time.time()) + COOKIE_SECONDS
    response.set_cookie(
        COOKIE_NAME, f"{expires}.{_sign(expires)}",
        max_age=COOKIE_SECONDS, httponly=True, samesite="lax",
        # Secure только на https: по http браузер метку не вернёт, и локальная
        # проверка сломается на ровном месте. Схему берём из заголовка nginx —
        # uvicorn запущен без --proxy-headers, и сам по себе за прокси он видит
        # http даже там, где снаружи https.
        secure=_scheme(request) == "https",
    )


def owner(request: Request, response: Response,
          credentials: HTTPBasicCredentials = Depends(security)):
    """Пустой пароль в настройках означает незакрытый сервер: отдавать разговоры
    в таком состоянии нельзя, поэтому это отказ, а не свободный доступ."""
    if not OWNER_PASSWORD:
        raise HTTPException(503, "RECORDER_OWNER_PASSWORD не задан, доступ закрыт")

    expires = _valid_until(request.cookies.get(COOKIE_NAME))
    if expires:
        # Продлеваем, когда прошла половина срока: иначе Set-Cookie уходит в каждый
        # ответ, включая картинки и скрипты.
        if expires - time.time() < COOKIE_SECONDS / 2:
            _issue(request, response)
        return OWNER_USER

    if credentials is None:
        raise HTTPException(401, "нужен логин и пароль",
                            headers={"WWW-Authenticate": "Basic"})
    # Сравнение постоянного времени: иначе пароль подбирается по задержке ответа.
    user_ok = secrets.compare_digest(credentials.username, OWNER_USER)
    password_ok = secrets.compare_digest(credentials.password, OWNER_PASSWORD)
    if not (user_ok and password_ok):
        raise HTTPException(401, "неверный логин или пароль",
                            headers={"WWW-Authenticate": "Basic"})
    _issue(request, response)
    return credentials.username
