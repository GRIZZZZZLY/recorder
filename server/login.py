"""Форма входа владельца.

Нужна одному случаю, зато важному: сайт, добавленный на домашний экран
iPhone, открывается без адресной строки, и Safari в этом режиме окно
Basic-авторизации не показывает вовсе — вместо сайта владелец видит пустоту.
Форма ставит ту же подписанную метку в cookie, что и Basic, поэтому дальше всё
работает как раньше.

Разбор тела руками, без python-multipart: одна форма из двух полей не стоит
новой зависимости на сервере.
"""
from urllib.parse import parse_qs, quote, urlparse

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

import auth

router = APIRouter()

PAGE = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#15171c">
<title>Вход · Диктофон</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    margin: 0; min-height: 100dvh; display: grid; place-items: center;
    background: #15171c; color: #e4e4e8; padding: 24px;
    font: 400 15px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  }}
  form {{
    width: 100%; max-width: 320px; display: flex; flex-direction: column; gap: 14px;
    background: #1d2027; border-radius: 12px; padding: 24px;
    box-shadow: inset 0 1px 0 rgb(255 255 255 / .05), 0 1px 0 rgb(0 0 0 / .22);
  }}
  .mark {{
    width: 12px; height: 12px; border-radius: 999px; background: #ff453a;
    box-shadow: inset 0 1px 0 rgb(255 255 255 / .35), inset 0 -1px 1px rgb(0 0 0 / .3);
  }}
  h1 {{ margin: 0; font-size: 17px; font-weight: 600; }}
  .head {{ display: flex; align-items: center; gap: 10px; }}
  label {{ display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: #a3a3a9; }}
  input {{
    font: inherit; color: #e4e4e8; background: #15171c;
    border: 1px solid #72757e; border-radius: 8px; padding: 10px 12px;
  }}
  input:focus-visible {{ outline: 2px solid #e4e4e8; outline-offset: 2px; }}
  button {{
    font: inherit; font-weight: 500; color: #15171c; background: #d6d6db;
    border: 0; border-radius: 999px; padding: 11px; cursor: pointer;
    box-shadow: inset 0 1px 0 rgb(255 255 255 / .55), 0 2px 4px rgb(6 7 9 / .45);
  }}
  button:active {{ transform: translateY(1px); box-shadow: inset 0 1px 3px rgb(0 0 0 / .3); }}
  .error {{ margin: 0; font-size: 13px; color: #ff6961; }}
</style>
</head><body>
<form method="post" action="/login">
  <div class="head"><span class="mark"></span><h1>Диктофон</h1></div>
  {error}
  <input type="hidden" name="next" value="{next}">
  <label>Имя<input name="user" autocomplete="username" autocapitalize="none"
                   autocorrect="off" spellcheck="false" required></label>
  <label>Пароль<input name="password" type="password"
                      autocomplete="current-password" required></label>
  <button type="submit">Войти</button>
</form>
</body></html>
"""


def safe_next(raw: str | None) -> str:
    """Только путь внутри этого сайта. Чужой адрес здесь превратил бы форму
    входа в удобную пересылку на чужую страницу под нашим именем."""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return "/"
    return raw


def page(next_url: str, error: str | None = None) -> HTMLResponse:
    html = PAGE.format(
        next=next_url.replace('"', "&quot;"),
        error=f'<p class="error">{error}</p>' if error else "",
    )
    # Форму входа не кэшируем: иначе после выхода из строя пароля браузер
    # покажет старую страницу вместо новой.
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/login")
def login_form(request: Request, response: Response, next: str = "/"):
    # Уже вошёл — форму не показываем: это тупик, из которого владелец
    # возвращался бы кнопкой «назад».
    if auth.verify(request, response, None):
        return RedirectResponse(safe_next(next), status_code=303)
    return page(safe_next(next))


@router.post("/login")
async def login_submit(request: Request):
    body = parse_qs((await request.body()).decode("utf-8", "replace"))
    user = (body.get("user") or [""])[0]
    password = (body.get("password") or [""])[0]
    next_url = safe_next((body.get("next") or ["/"])[0])

    if not auth.password_matches(user, password):
        return page(next_url, "Неверное имя или пароль")

    answer = RedirectResponse(next_url, status_code=303)
    auth.issue(request, answer)
    return answer


def redirect_to_login(request: Request) -> RedirectResponse:
    """Куда отправлять незалогиненного владельца со страницы сайта."""
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
