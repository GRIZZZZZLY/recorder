"""Аутентификация владельца. Basic поверх HTTPS: пользователь один.

Модуль отдельный, потому что зависимость `owner` нужна и страницам, и JSON-API,
и отдаче статики, а страницы уходят вместе с web.py.
"""
import os
import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

OWNER_USER = os.environ.get("RECORDER_OWNER_USER", "owner")
OWNER_PASSWORD = os.environ.get("RECORDER_OWNER_PASSWORD", "")

security = HTTPBasic()


def owner(credentials: HTTPBasicCredentials = Depends(security)):
    """Пустой пароль в настройках означает незакрытый сервер: отдавать разговоры
    в таком состоянии нельзя, поэтому это отказ, а не свободный доступ."""
    if not OWNER_PASSWORD:
        raise HTTPException(503, "RECORDER_OWNER_PASSWORD не задан, доступ закрыт")
    # Сравнение постоянного времени: иначе пароль подбирается по задержке ответа.
    user_ok = secrets.compare_digest(credentials.username, OWNER_USER)
    password_ok = secrets.compare_digest(credentials.password, OWNER_PASSWORD)
    if not (user_ok and password_ok):
        raise HTTPException(401, "неверный логин или пароль",
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username
