"""Веб-интерфейс владельца: память разговоров.

Список сессий, поиск по репликам, страница сессии с саммари, источниками
каждого вывода, перемоткой по клику и правкой говорящих, страница состояния
обработки. Готовый HTML и десяток строк JS: сборка фронтенда ради одного
владельца не окупается.
"""
import html
import json
from contextlib import closing
from datetime import date
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

import db
import textnorm
from auth import owner

router = APIRouter()

# Страницы владельца смонтированы в app.py под этим префиксом. Ссылки, формы и
# редиректы внутри интерфейса собираются через BASE: /legacy работает как
# страховка до паритета нового фронтенда, значит навигация в нём должна
# оставаться рабочей, а не улетать в catch-all статики.
BASE = "/legacy"

NAV = (
    f'<nav><a href="{BASE}/">Записи</a><a href="{BASE}/tasks">Задачи</a>'
    f'<a href="{BASE}/questions">Вопросы</a>'
    f'<a href="{BASE}/projects">Проекты</a><a href="{BASE}/review">Спорные</a>'
    f'<a href="{BASE}/digest">Дайджест</a>'
    f'<a href="{BASE}/search">Поиск</a><a href="{BASE}/status">Состояние</a></nav>'
)

PAGE = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; --line: color-mix(in oklab, currentColor 15%, transparent);
           --soft: color-mix(in oklab, currentColor 7%, transparent); }}
  body {{ margin: 0 auto; max-width: 46rem; padding: 1rem max(1rem, env(safe-area-inset-left)) 3rem;
         font: 17px/1.5 -apple-system, system-ui, sans-serif; }}
  h1 {{ font-size: 1.35rem; margin: .2rem 0 1rem; }}
  h2 {{ font-size: 1.05rem; margin: 1.6rem 0 .5rem; }}
  a {{ color: inherit; }}
  nav {{ display: flex; gap: 1rem; flex-wrap: wrap; font-size: .9rem; margin-bottom: 1rem; }}
  .card {{ display: block; padding: .8rem 0; border-bottom: 1px solid var(--line);
           text-decoration: none; }}
  .muted {{ opacity: .65; font-size: .85rem; }}
  audio {{ width: 100%; margin: .4rem 0 1rem; }}
  ul {{ padding-left: 1.1rem; margin: .3rem 0; }}
  li {{ margin: .45rem 0; }}
  .t {{ font-variant-numeric: tabular-nums; opacity: .6; }}
  .u {{ margin: .2rem 0; padding: .25rem .5rem; border-radius: .4rem; cursor: pointer; }}
  .u:hover, .u:target, .u.active {{ background: var(--soft); }}
  .u .who {{ font-weight: 600; }}
  .warn {{ padding: .6rem .8rem; border-left: 3px solid currentColor; opacity: .8; margin: .5rem 0; }}
  .src {{ font-size: .8rem; opacity: .75; }}
  button, input[type=text], input[type=search] {{ font: inherit; padding: .35rem .6rem;
           border: 1px solid var(--line); border-radius: .4rem; background: transparent; color: inherit; }}
  button {{ cursor: pointer; }}
  button.link {{ border: none; padding: 0 .3rem; text-decoration: underline; opacity: .8; }}
  form.search {{ display: flex; gap: .5rem; margin: .5rem 0 1rem; }}
  form.search input {{ flex: 1; min-width: 0; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .9rem; }}
  td, th {{ text-align: left; padding: .3rem .4rem; border-bottom: 1px solid var(--line); vertical-align: top; }}
  mark {{ background: color-mix(in oklab, currentColor 25%, transparent); color: inherit; border-radius: .2rem; }}
  .spk {{ display: grid; grid-template-columns: auto 1fr auto; gap: .5rem; align-items: center; margin: .3rem 0; }}
</style></head><body>
{nav}
{body}
</body></html>"""

SEEK_JS = """<script>
(function () {
  var players = {};
  document.querySelectorAll('audio[data-part]').forEach(function (a) { players[a.dataset.part] = a; });
  function seek(part, start) {
    var target = players[part]; if (!target) return;
    Object.keys(players).forEach(function (k) { if (k !== part) players[k].pause(); });
    target.currentTime = parseFloat(start) || 0;
    target.play();
    target.scrollIntoView({ block: 'nearest' });
  }
  document.querySelectorAll('[data-seek-part]').forEach(function (el) {
    el.addEventListener('click', function () {
      document.querySelectorAll('.u.active').forEach(function (x) { x.classList.remove('active'); });
      if (el.classList.contains('u')) el.classList.add('active');
      seek(el.dataset.seekPart, el.dataset.seekStart);
    });
  });
  function applyHash() {
    if (!location.hash) return;
    var hit = document.querySelector(location.hash.replace(/[^#\\w-]/g, ''));
    if (!hit || !hit.dataset.seekPart) return;
    document.querySelectorAll('.u.active').forEach(function (x) { x.classList.remove('active'); });
    hit.classList.add('active');
    hit.scrollIntoView();
  }
  applyHash();
  // Переход по якорю внутри уже открытой страницы не перезагружает документ.
  window.addEventListener('hashchange', applyHash);
})();
</script>"""


def page(title, body, script=""):
    return HTMLResponse(PAGE.format(title=html.escape(title), body=body + script, nav=NAV))


def esc(value):
    # Ноль — это значение, а не пустота: «or» здесь стёр бы его из дайджеста.
    return html.escape("" if value is None else str(value))


def duration_label(audio_bytes):
    """Длительность из размера PCM. Короткая запись не должна читаться как «0 мин»."""
    seconds = max(0, (audio_bytes or 0) - db.WAV_HEADER_BYTES) / db.BYTES_PER_SECOND
    if seconds < 60:
        return f"{seconds:.0f} с"
    return f"{seconds / 60:.0f} мин"


def search_form(query=""):
    return (f"<form class='search' action='{BASE}/search' method='get'>"
            f"<input type='search' name='q' value='{esc(query)}' placeholder='Слово или фраза'"
            f" autocomplete='off'><button>Найти</button></form>")


# --- список ------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def index(_=Depends(owner)):
    with closing(db.connect()) as conn:
        rows = conn.execute(
            "SELECT p.device_id, p.session, COUNT(*) AS parts, SUM(p.bytes) AS bytes,"
            "       MAX(p.received_at) AS last,"
            "       (SELECT status FROM summaries s"
            "         WHERE s.device_id=p.device_id AND s.session=p.session) AS summary_status,"
            "       (SELECT COUNT(*) FROM transcripts t"
            "         WHERE t.device_id=p.device_id AND t.session=p.session"
            "           AND t.status='done') AS done_parts"
            " FROM parts p GROUP BY p.device_id, p.session"
            " ORDER BY p.received_at DESC"
        ).fetchall()

    body = "<h1>Записи</h1>" + search_form()
    if not rows:
        return page("Записи", body + "<p class='muted'>Пока пусто.</p>")

    for row in rows:
        state = "саммари готово" if row["summary_status"] == "done" else "обрабатывается"
        body += (
            f"<a class='card' href='{BASE}/session/{esc(row['device_id'])}/{row['session']}'>"
            f"<strong>Сессия {row['session']:04d}</strong><br>"
            f"<span class='muted'>{esc(row['last'])} · {duration_label(row['bytes'])} · "
            f"частей {row['parts']}, расшифровано {row['done_parts']} · {state}</span></a>"
        )
    return page("Записи", body)


# --- поиск -------------------------------------------------------------------

def highlight(text, query_lemmas):
    """Подсветка слов, чья лемма совпала с леммой запроса."""
    if not query_lemmas:
        return esc(text)
    out, pos = [], 0
    for match in textnorm.TOKEN_RE.finditer(text):
        out.append(esc(text[pos:match.start()]))
        word = match.group(0)
        if textnorm.lemma(word) in query_lemmas:
            out.append(f"<mark>{esc(word)}</mark>")
        else:
            out.append(esc(word))
        pos = match.end()
    out.append(esc(text[pos:]))
    return "".join(out)


@router.get("/search", response_class=HTMLResponse)
def search_page(q: str = "", _=Depends(owner)):
    body = "<h1>Поиск</h1>" + search_form(q)
    if not q.strip():
        note = "" if textnorm.available() else (
            "<p class='warn'>Морфология не установлена: поиск идёт по точной форме слова.</p>")
        return page("Поиск", body + note)

    with closing(db.connect()) as conn:
        hits = db.search(conn, q)

    if not hits:
        return page("Поиск", body + "<p class='muted'>Ничего не найдено.</p>")

    query_lemmas = {textnorm.lemma(t) for t in textnorm.tokens(q)}
    current = None
    for hit in hits:
        key = (hit["device_id"], hit["session"])
        if key != current:
            current = key
            body += (f"<h2><a href='{BASE}/session/{esc(hit['device_id'])}/{hit['session']}'>"
                     f"Сессия {hit['session']:04d}</a></h2>")
        anchor = f"u-{hit['part']}-{hit['idx']}"
        body += (
            f"<p class='u'><a href='{BASE}/session/{esc(hit['device_id'])}/{hit['session']}#{anchor}'"
            f" style='text-decoration:none'>"
            f"<span class='t'>{db.format_abs(hit['start_abs'])}</span> "
            f"<span class='who'>{esc(hit['label'])}:</span> "
            f"{highlight(hit['text'], query_lemmas)}</a></p>"
        )
    body += f"<p class='muted'>Совпадений: {len(hits)}</p>"
    return page("Поиск", body)


# --- сессия ------------------------------------------------------------------

def render_item(item):
    src = item.get("source")
    time_label = esc(item.get("time"))
    text = esc(item.get("text"))
    if src:
        button = (f"<button class='link' data-seek-part='{src['part']}'"
                  f" data-seek-start='{src['start']}'>Послушать основание</button>")
        flag = "" if src.get("confirmed") else " <span class='src'>источник не подтверждён</span>"
    else:
        button = ""
        flag = " <span class='src'>источник не найден</span>"
    return f"<span class='t'>{time_label}</span> {text}<br><span class='src'>{button}{flag}</span>"


def render_summary(raw):
    if not raw:
        return "<p class='muted'>Саммари ещё не готово.</p>"
    data = json.loads(raw)
    blocks = [f"<p>{esc(data.get('summary'))}</p>"]

    topics = [str(t).strip() for t in (data.get("topics") or []) if str(t).strip()]
    if topics:
        links = " · ".join(f"<a href='{BASE}/project/{quote(t)}'>{esc(t)}</a>" for t in topics)
        blocks.append(f"<p class='muted'>Темы: {links}</p>")

    for title, key in (("Решения", "decisions"), ("Факты", "facts")):
        items = data.get(key) or []
        if items:
            blocks.append(f"<h2>{title}</h2><ul>"
                          + "".join(f"<li>{render_item(i)}</li>" for i in items) + "</ul>")

    tasks = data.get("tasks") or []
    if tasks:
        rows = []
        for task in tasks:
            who = esc(task.get("owner")) or "<span class='muted'>исполнитель не назван</span>"
            due = esc(task.get("due")) or "<span class='muted'>срок не назван</span>"
            rows.append(f"<li>{render_item(task)}<br><span class='muted'>{who} · {due}</span></li>")
        blocks.append(f"<h2>Задачи</h2><ul>{''.join(rows)}</ul>")

    questions = data.get("open_questions") or []
    if questions:
        blocks.append("<h2>Вопросы без ответа</h2><ul>"
                      + "".join(f"<li>{render_item(i)}</li>" for i in questions) + "</ul>")
    return "".join(blocks)


def render_speakers(device_id, session, timeline, names):
    speakers = sorted({u["speaker"] for u in timeline if u["speaker"] is not None})
    if not speakers:
        return ""
    rows = []
    for spk in speakers:
        entry = names.get(spk, {"name": "", "is_me": False})
        checked = " checked" if entry["is_me"] else ""
        rows.append(
            f"<div class='spk'><span class='muted'>Говорящий {spk + 1}</span>"
            f"<input type='text' name='name_{spk}' value='{esc(entry['name'])}' placeholder='Имя'>"
            f"<label><input type='radio' name='me' value='{spk}'{checked}> это я</label></div>"
        )
    none_checked = "" if any(n["is_me"] for n in names.values()) else " checked"
    return (
        f"<h2>Говорящие</h2>"
        f"<form method='post' action='{BASE}/session/{esc(device_id)}/{session}/speakers'>"
        + "".join(rows)
        + f"<div class='spk'><span></span><span class='muted'>Номера — метки диаризации, не личности."
          f" Исходный транскрипт не меняется.</span>"
          f"<label><input type='radio' name='me' value=''{none_checked}> меня нет</label></div>"
          f"<p><button>Сохранить</button></p></form>"
    )


@router.get("/session/{device_id}/{session}", response_class=HTMLResponse)
def session_page(device_id: str, session: int, _=Depends(owner)):
    with closing(db.connect()) as conn:
        parts = conn.execute(
            "SELECT p.part, p.recovered, t.status AS t_status, t.last_error"
            " FROM parts p LEFT JOIN transcripts t"
            "   ON t.device_id=p.device_id AND t.session=p.session AND t.part=p.part"
            " WHERE p.device_id=? AND p.session=? ORDER BY p.part",
            (device_id, session),
        ).fetchall()
        if not parts:
            raise HTTPException(404, "нет такой сессии")
        summary_row = conn.execute(
            "SELECT result, status, last_error FROM summaries WHERE device_id=? AND session=?",
            (device_id, session),
        ).fetchone()
        timeline, failed = db.session_timeline(conn, device_id, session)
        names = db.speaker_names(conn, device_id, session)

    players = []
    for row in parts:
        mark = " · восстановлена" if row["recovered"] else ""
        players.append(
            f"<div class='muted'>Часть {row['part']:02d}{mark}</div>"
            f"<audio controls preload='none' data-part='{row['part']}'"
            f" src='/audio/{esc(device_id)}/{session}/{row['part']}'></audio>"
        )
        if row["t_status"] == "failed":
            players.append(f"<div class='warn'>Расшифровка части {row['part']:02d} не удалась: "
                           f"{esc(row['last_error'])}</div>")

    summary_html = render_summary(summary_row["result"] if summary_row else None)
    if summary_row and summary_row["status"] != "done" and summary_row["last_error"]:
        summary_html += (f"<div class='warn'>Последняя ошибка саммари: "
                         f"{esc(summary_row['last_error'])}</div>")

    lines = []
    current_part = None
    for u in timeline:
        if u["part"] != current_part:
            current_part = u["part"]
            lines.append(f"<div class='muted'>часть {current_part:02d}</div>")
        lines.append(
            f"<p class='u' id='u-{u['part']}-{u['idx']}' data-seek-part='{u['part']}'"
            f" data-seek-start='{u['start']}'>"
            f"<span class='t'>{db.format_abs(u['start_abs'])}</span> "
            f"<span class='who'>{esc(u['label'])}:</span> {esc(u['text'])}</p>"
        )
    transcript_html = "".join(lines) or "<p class='muted'>Пока нет.</p>"
    if failed and not timeline:
        transcript_html = "<p class='muted'>Расшифровка не удалась.</p>"

    body = (
        f"<h1>Сессия {session:04d}</h1>"
        f"{summary_html}"
        f"<h2>Аудио</h2>{''.join(players)}"
        f"{render_speakers(device_id, session, timeline, names)}"
        f"<h2>Транскрипт</h2><p class='muted'>Нажмите на реплику, чтобы послушать.</p>"
        f"{transcript_html}"
    )
    return page(f"Сессия {session:04d}", body, SEEK_JS)


@router.post("/session/{device_id}/{session}/speakers")
async def save_speakers(device_id: str, session: int, request: Request, _=Depends(owner)):
    # Разбор формы без python-multipart: одна зависимость меньше на сервере.
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    me = (form.get("me") or [""])[0]
    with closing(db.connect()) as conn:
        for key, values in form.items():
            if key.startswith("name_"):
                try:
                    spk = int(key[5:])
                except ValueError:
                    continue
                name = (values[0] if values else "")[:60]
                db.set_speaker(conn, device_id, session, spk, name, me == str(spk))
    return RedirectResponse(f"{BASE}/session/{device_id}/{session}", status_code=303)


# --- задачи, вопросы, проекты, спорные места ----------------------------------

def session_link(device_id, session, source=None, label=None):
    """Ссылка на сессию, при наличии источника — сразу на нужную реплику."""
    anchor = ""
    if source and source.get("part") is not None:
        anchor = f"#u-{source['part']}-{source['idx']}"
    text = label or f"Сессия {session:04d}"
    return f"<a href='{BASE}/session/{esc(device_id)}/{session}{anchor}'>{esc(text)}</a>"


def day_label(value):
    return value.strftime("%d.%m.%Y") if value else "дата неизвестна"


def due_label(task):
    if not task["due_said"]:
        return "срок не назван"
    if not task["due_date"]:
        return f"{esc(task['due_said'])} · <span class='src'>дату не разобрали</span>"
    stamp = day_label(date.fromisoformat(task["due_date"]))
    tail = " · <span class='src'>просрочено</span>" if task["overdue"] else ""
    return f"{esc(task['due_said'])} · {stamp}{tail}"


def safe_back(value, fallback):
    """Куда вернуться после формы. Чужой адрес сюда попасть не должен."""
    if value.startswith("/") and not value.startswith("//"):
        return value
    return fallback


def filter_links(current, options, base):
    parts = []
    for key, title in options:
        mark = "<strong>%s</strong>" if key == current else "%s"
        parts.append(f"<a href='{base}?show={key}'>{mark % esc(title)}</a>")
    return f"<nav>{''.join(parts)}</nav>"


TASK_FILTERS = [("open", "Открытые"), ("mine", "Мои"), ("overdue", "Просроченные"),
                ("all", "Все")]


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(show: str = "open", session: int = None, _=Depends(owner)):
    if show not in {key for key, _ in TASK_FILTERS}:
        show = "open"
    with closing(db.connect()) as conn:
        rows = db.task_rows(conn, only_open=show != "all", mine=show == "mine",
                            overdue=show == "overdue", session=session)
        has_me = bool(db.my_names(conn))

    back = f"{BASE}/tasks?show={show}" + (f"&session={session}" if session is not None else "")
    body = "<h1>Задачи</h1>" + filter_links(show, TASK_FILTERS, f"{BASE}/tasks")
    if show == "mine" and not has_me:
        body += ("<p class='warn'>Ни один говорящий не отмечен как «это я»: "
                 "отметьте себя на странице сессии, иначе «мои» ничего не найдут.</p>")
    if session is not None:
        body += f"<p class='muted'>Только сессия {session:04d}. <a href='{BASE}/tasks?show={show}'>Все сессии</a></p>"
    if not rows:
        return page("Задачи", body + "<p class='muted'>Ничего нет.</p>")

    for task in rows:
        source = json.loads(task["source"]) if task["source"] else None
        owner_label = esc(task["owner"]) or "<span class='muted'>исполнитель не назван</span>"
        marks = []
        if task["mine"]:
            marks.append("моя")
        if task["vanished"]:
            marks.append("исчезла из саммари")
        if task["status"] != "open":
            marks.append({"done": "закрыта", "dismissed": "снята"}[task["status"]])
        note = f" · {esc(' · '.join(marks))}" if marks else ""
        buttons = ""
        for status, title in (("done", "Закрыть"), ("dismissed", "Снять"), ("open", "Вернуть")):
            if status == task["status"]:
                continue
            buttons += (
                f"<form method='post' style='display:inline'"
                f" action='{BASE}/tasks/{esc(task['device_id'])}/{task['session']}/{task['task_id']}'>"
                f"<input type='hidden' name='status' value='{status}'>"
                f"<input type='hidden' name='back' value='{esc(back)}'>"
                f"<button class='link'>{title}</button></form>"
            )
        body += (
            f"<p class='u'>{esc(task['text'])}<br>"
            f"<span class='muted'>{owner_label} · {due_label(task)} · "
            f"{session_link(task['device_id'], task['session'], source)}{note}</span><br>"
            f"<span class='src'>{buttons}</span></p>"
        )
    body += f"<p class='muted'>Всего: {len(rows)}</p>"
    return page("Задачи", body)


@router.post("/tasks/{device_id}/{session}/{task_id}")
async def update_task(device_id: str, session: int, task_id: str, request: Request,
                      _=Depends(owner)):
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    status = (form.get("status") or ["done"])[0]
    back = safe_back((form.get("back") or [f"{BASE}/tasks"])[0], f"{BASE}/tasks")
    with closing(db.connect()) as conn:
        db.set_task_status(conn, device_id, session, task_id, status)
    return RedirectResponse(back, status_code=303)


QUESTION_FILTERS = [("open", "Без ответа"), ("all", "Все")]


@router.get("/questions", response_class=HTMLResponse)
def questions_page(show: str = "open", _=Depends(owner)):
    show = show if show in {"open", "all"} else "open"
    with closing(db.connect()) as conn:
        rows = db.question_rows(conn, only_open=show == "open")

    body = "<h1>Вопросы без ответа</h1>" + filter_links(show, QUESTION_FILTERS, f"{BASE}/questions")
    if not rows:
        return page("Вопросы", body + "<p class='muted'>Ничего нет.</p>")

    for item in rows:
        marks = {"answered": " · ответ получен", "dropped": " · снят"}.get(item["state"], "")
        buttons = ""
        for state, title in (("answered", "Ответ получен"), ("dropped", "Снять"),
                             ("open", "Вернуть")):
            if state == item["state"]:
                continue
            buttons += (
                f"<form method='post' style='display:inline'"
                f" action='{BASE}/questions/{esc(item['device_id'])}/{item['session']}/{item['question_id']}'>"
                f"<input type='hidden' name='state' value='{state}'>"
                f"<input type='hidden' name='back' value='{BASE}/questions?show={show}'>"
                f"<button class='link'>{title}</button></form>"
            )
        body += (
            f"<p class='u'>{esc(item['text'])}<br>"
            f"<span class='muted'>{day_label(item['day'])} · "
            f"{session_link(item['device_id'], item['session'], item['source'])}"
            f"{esc(marks)}</span><br><span class='src'>{buttons}</span></p>"
        )
    return page("Вопросы", body)


@router.post("/questions/{device_id}/{session}/{question_id}")
async def update_question(device_id: str, session: int, question_id: str, request: Request,
                          _=Depends(owner)):
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    state = (form.get("state") or ["answered"])[0]
    back = safe_back((form.get("back") or [f"{BASE}/questions"])[0], f"{BASE}/questions")
    with closing(db.connect()) as conn:
        db.set_question_state(conn, device_id, session, question_id, state)
    return RedirectResponse(back, status_code=303)


@router.get("/projects", response_class=HTMLResponse)
def projects_page(_=Depends(owner)):
    with closing(db.connect()) as conn:
        projects = db.project_sessions(conn)
        aliases = db.alias_map(conn)

    body = "<h1>Проекты</h1>"
    if not projects:
        body += ("<p class='muted'>Пока пусто: тема разговора появляется в саммари "
                 "после обработки следующих записей.</p>")
    for name in sorted(projects):
        items = projects[name]
        body += (f"<a class='card' href='{BASE}/project/{quote(name)}'><strong>{esc(name)}</strong><br>"
                 f"<span class='muted'>разговоров: {len(items)} · "
                 f"последний {day_label(items[-1]['day'])}</span></a>")

    body += (
        "<h2>Псевдонимы</h2>"
        "<p class='muted'>«Пози флора» и «Posiflora» — один проект: сведите их сюда.</p>"
        f"<form method='post' action='{BASE}/projects/alias' class='search'>"
        "<input type='text' name='alias' placeholder='Как прозвучало'>"
        "<input type='text' name='canonical' placeholder='Название проекта'>"
        "<button>Сохранить</button></form>"
    )
    if aliases:
        body += table([{"alias": a, "canonical": c} for a, c in sorted(aliases.items())],
                      [("alias", "Прозвучало"), ("canonical", "Проект")])
    return page("Проекты", body)


@router.post("/projects/alias")
async def add_alias(request: Request, _=Depends(owner)):
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    with closing(db.connect()) as conn:
        db.set_alias(conn, (form.get("alias") or [""])[0][:80],
                     (form.get("canonical") or [""])[0][:80])
    return RedirectResponse(f"{BASE}/projects", status_code=303)


@router.get("/project/{name}", response_class=HTMLResponse)
def project_page(name: str, _=Depends(owner)):
    with closing(db.connect()) as conn:
        projects = db.project_sessions(conn)
        canonical = db.canonical_topic(db.alias_map(conn), name)
    items = projects.get(canonical) or projects.get(name) or []

    body = f"<h1>{esc(canonical)}</h1>"
    if not items:
        return page(canonical, body + "<p class='muted'>Разговоров по этой теме нет.</p>")

    # Лента идёт по дате разговора, а не по дате выгрузки записи.
    for entry in items:
        result = entry["result"]
        body += (f"<h2>{day_label(entry['day'])} · "
                 f"{session_link(entry['device_id'], entry['session'])}</h2>"
                 f"<p>{esc(result.get('summary'))}</p>")
        for title, key in (("Решения", "decisions"), ("Задачи", "tasks"),
                           ("Вопросы", "open_questions")):
            rows = result.get(key) or []
            if rows:
                body += (f"<p class='muted'>{title}</p><ul>"
                         + "".join(f"<li>{esc(i.get('text'))}</li>" for i in rows) + "</ul>")
    return page(canonical, body)


@router.get("/review", response_class=HTMLResponse)
def review_page(_=Depends(owner)):
    with closing(db.connect()) as conn:
        items = db.review_items(conn)

    body = ("<h1>Спорные места</h1>"
            "<p class='muted'>Обработка ничего из этого не ждёт: это подсказка, "
            "а не блокировка.</p>")
    if not items:
        return page("Спорные места", body + "<p class='muted'>Нечего проверять.</p>")

    for item in items:
        body += (
            f"<p class='u'><span class='src'>{esc(item['kind'])}</span><br>"
            f"{esc(item['text'])}<br>"
            f"<span class='muted'>{esc(item['detail'])} · "
            f"{session_link(item['device_id'], item['session'], item.get('source'))}</span></p>"
        )
    body += f"<p class='muted'>Всего: {len(items)}</p>"
    return page("Спорные места", body)


@router.get("/digest", response_class=HTMLResponse)
def digest_page(_=Depends(owner)):
    with closing(db.connect()) as conn:
        day = db.digest(conn, 1)
        week = db.digest(conn, 7)

    rows = [
        ("Записано минут", f"{day['minutes']:.1f}", f"{week['minutes']:.1f}"),
        ("Разговоров", day["sessions"], week["sessions"]),
        ("Новых задач", day["new_tasks"], week["new_tasks"]),
    ]
    now = [
        ("Моих открытых задач", week["my_open_tasks"]),
        ("Открытых задач всего", week["open_tasks"]),
        ("Просроченных", week["overdue_tasks"]),
        ("Вопросов без ответа", week["open_questions"]),
        ("Спорных мест", week["review"]),
        ("Частей с ошибкой расшифровки", week["failed_parts"]),
        ("Саммари с ошибкой", week["failed_summaries"]),
    ]

    body = ("<h1>Дайджест</h1><table><tr><th></th><th>За сутки</th><th>За неделю</th></tr>"
            + "".join(f"<tr><td>{esc(t)}</td><td>{esc(d)}</td><td>{esc(w)}</td></tr>"
                      for t, d, w in rows)
            + "</table><h2>Сейчас</h2><table>"
            + "".join(f"<tr><td>{esc(t)}</td><td>{esc(v)}</td></tr>" for t, v in now)
            + "</table>")
    return page("Дайджест", body)


# --- состояние ---------------------------------------------------------------

def table(rows, columns):
    if not rows:
        return "<p class='muted'>Нет.</p>"
    head = "".join(f"<th>{esc(title)}</th>" for _, title in columns)
    body = ""
    for row in rows:
        body += "<tr>" + "".join(f"<td>{esc(row.get(key))}</td>" for key, _ in columns) + "</tr>"
    return f"<table><tr>{head}</tr>{body}</table>"


@router.get("/status", response_class=HTMLResponse)
def status_page(_=Depends(owner)):
    with closing(db.connect()) as conn:
        s = db.processing_status(conn)

    for row in s["gaps"]:
        row["missing"] = ", ".join(str(p) for p in row["missing"])
    for row in s["duration_mismatch"]:
        row["by_size"] = f"{row['by_size']:.1f} с"
        row["by_deepgram"] = f"{row['by_deepgram']:.1f} с"

    body = (
        "<h1>Состояние обработки</h1>"
        f"<p>В очереди на расшифровку: <strong>{s['transcripts_pending']}</strong>. "
        f"Ждут саммари: <strong>{s['summaries_pending']}</strong>. "
        f"Расшифровано Deepgram: <strong>{s['deepgram_minutes']:.1f} мин</strong> "
        f"<span class='muted'>(оценка расхода кредита)</span>.</p>"
        "<h2>Последняя выгрузка с диктофона</h2>"
        + table(s["last_upload"], [("device_id", "Устройство"), ("at", "Когда"), ("parts", "Частей всего")])
        + "<h2>Части с ошибкой расшифровки</h2>"
        + table(s["failed_parts"], [("session", "Сессия"), ("part", "Часть"),
                                     ("attempts", "Попыток"), ("last_error", "Ошибка")])
        + "<h2>Саммари с ошибкой</h2>"
        + table(s["stuck_summaries"], [("session", "Сессия"), ("attempts", "Попыток"),
                                        ("last_error", "Ошибка")])
        + "<h2>Пропуски частей</h2>"
        "<p class='muted'>Номера отсутствуют: запись потеряна или ещё не выгружена.</p>"
        + table(s["gaps"], [("session", "Сессия"), ("missing", "Нет частей")])
        + "<h2>Расхождение длительности</h2>"
        "<p class='muted'>Размер файла и ответ Deepgram расходятся больше чем на 2%: часть аудио могла не обработаться.</p>"
        + table(s["duration_mismatch"], [("session", "Сессия"), ("part", "Часть"),
                                          ("by_size", "По файлу"), ("by_deepgram", "По Deepgram")])
        + "<h2>Восстановленные части</h2>"
        "<p class='muted'>Питание пропало во время записи, заголовок починен при старте диктофона.</p>"
        + table(s["recovered_parts"], [("session", "Сессия"), ("part", "Часть")])
    )
    return page("Состояние", body)


@router.get("/healthz")
def healthz():
    return Response("ok", media_type="text/plain")
