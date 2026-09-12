# Новый фронтенд на BoardUI — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить строковый HTML в `server/web.py` на интерфейс из компонентов BoardUI в тёмной теме, который FastAPI отдаёт как статику под тем же паролем.

**Architecture:** Next.js 16 со статическим экспортом собирается на машине разработчика в `web/out`; FastAPI отдаёт этот каталог catch-all маршрутом под `Depends(owner)` и добавляет JSON-роуты `/api/ui/*` — тонкие обёртки над существующими функциями `db.py`. Логика (задачи, сроки, спорные места, поиск) не переписывается: она уже в `db.py` и покрыта 50 тестами. Старый интерфейс живёт под `/legacy` до паритета.

**Tech Stack:** Python 3.13, FastAPI, SQLite. Node 24.13.1, Next.js 16.2.x, React 19.2.x, TypeScript 5, Tailwind CSS v4, BoardUI (MIT, исходники в проекте), react-aria-components, @remixicon/react, tailwind-merge.

**Spec:** `docs/superpowers/specs/2026-09-12-boardui-frontend-design.md`

## Global Constraints

- Тема только тёмная. Класс `dark` стоит на `<html>` без условий, переключателя нет.
- Язык интерфейса русский. Технические термины, имена файлов и команды не переводятся.
- Цвета только семантическими токенами BoardUI (`text-text-primary`, `bg-background-primary-default`, `border-separator-border`, `accent-*`). Сырые классы палитры (`text-gray-500`, `bg-white`) и литералы hex/oklch в компонентах запрещены. Токены переопределяются один раз в `web/styles/recorder-theme.css`.
- Типографика только составными утилитами BoardUI (`text-title-1-medium`, `text-body-regular`, `text-caption-1-semibold` и другие из `styles/typography.css`). Не собирать тип из `text-sm font-medium leading-5`.
- Классы объединять через `cx()` из `@/utils/cx`. Иконки — ссылки на компоненты из `@remixicon/react` (`leadingIcon={RiCheckLine}`), не элементы.
- `dangerouslySetInnerHTML` запрещён везде: транскрипт и саммари — чужой текст из распознавания.
- Файлы BoardUI под `web/components/base/` и `web/components/application/` не правятся. Нужен другой вид — своя обёртка в `web/components/recorder/`.
- Контракт диктофона `/api/v1/upload` и отдача аудио `/audio/{device_id}/{session}/{part}` не меняются.
- Пароль один на всё: `RECORDER_OWNER_PASSWORD`, Basic поверх HTTPS. Статика закрыта тем же `Depends(owner)`, что и API.
- Адреса страниц с параметрами — строка запроса: `/session/?d=xiao-01&n=7`, `/project/?name=…`, `/search/?q=…`, `/tasks/?show=mine`. Динамические сегменты пути статический экспорт без `generateStaticParams` не умеет.
- Точные значения тёмной темы: канвас `#080e33`, карточка `#0c1754`, вложенное `#132070`, текст `#ffffff` и `#eaebf8`, линия `rgba(240, 233, 225, 0.14)`, действие `#2545ff` (ховер `#3d5aff`, нажатие `#1c37d9`), коралл `#ff6b5c`.
- Радиусы: карточки и поля 16px (`rounded-2xl`), пилюли `rounded-full`. Тень только у плеера: `0 8px 32px rgba(0, 0, 0, 0.4)`.
- Каждая задача заканчивается зелёным `cd server && python -m pytest -q` (50 проверок на старте плана) и, начиная с задачи 2, зелёными `npm run typecheck` и `npm run build` в `web/`.
- Работа идёт в ветке `boardui-frontend` (базовый коммит `361b50c`). Коммит в конце каждой задачи обязателен; сообщение на русском в формате `<тип>: <что сделано>`, типы `feat`, `fix`, `refactor`, `test`, `docs`, `chore`. Собранный `web/out`, `node_modules` и `demo-data` в историю не попадают — они в `.gitignore`.

---

## Структура файлов

**Создаются на стороне сервера:**

| Файл | Ответственность |
| --- | --- |
| `server/api_ui.py` | JSON-роуты `/api/ui/*`: разбор параметров, вызов `db.*`, сериализация. Логики нет |
| `server/static_site.py` | Отдача `web/out`: разрешение пути, `index.html` для каталогов, 503 без сборки |
| `server/test_api_ui.py` | Проверки JSON-роутов и отдачи статики |

**Создаются на стороне фронтенда:**

| Файл | Ответственность |
| --- | --- |
| `web/next.config.ts` | `output: 'export'`, `trailingSlash`, `rewrites` для разработки |
| `web/app/layout.tsx` | `<html class="dark">`, шрифты, импорт стилей, каркас страницы |
| `web/styles/recorder-theme.css` | Переопределение токенов BoardUI под тёмную тему референса |
| `web/lib/api.ts` | `apiGet`, `apiPost`, типы ответов, класс `AuthError` |
| `web/lib/use-api.ts` | Хук загрузки: `{ data, error, loading, reload }` |
| `web/lib/format.ts` | `formatDay`, `formatClock`, `formatMinutes` — общие форматтеры |
| `web/components/recorder/shell.tsx` | Каркас: боковая панель BoardUI, пункты меню, бейджи |
| `web/components/recorder/states.tsx` | `Loading`, `Empty`, `Failed` — три общих состояния страницы |
| `web/components/recorder/eyebrow.tsx` | Надзаголовок и дисплейный заголовок с курсивной фразой |
| `web/components/recorder/tile.tsx` | Плитка числа для сводки и состояния |
| `web/components/recorder/task-row.tsx` | Строка задачи с чипами и действиями; общая для сводки и `/tasks` |
| `web/components/recorder/source-link.tsx` | Точка состояния источника и ссылка «Послушать» |
| `web/components/recorder/player.tsx` | Нижний плеер и контекст `useSeek()` |
| `web/app/*/page.tsx` | Девять страниц, каждая — только раскладка и вызовы `useApi` |

**Изменяются:**

| Файл | Изменение |
| --- | --- |
| `server/db.py` | Добавляются `session_rows` (SQL из `web.index`) и `search_segments` |
| `server/textnorm.py` | Добавляется `segments` — разбиение текста на совпавшие и обычные куски |
| `server/app.py` | Подключение `api_ui.router`, `static_site.router` последним, `web.router` под `/legacy` |
| `server/web.py` | На последней задаче удаляется вместе с тестами страниц |

---

### Задача 1: Проверка статического экспорта Next 16

Цель: узнать за 20 минут, работает ли `output: 'export'` вместе с BoardUI и `useSearchParams`. Код задачи выбрасывается.

**Files:**
- Create: `C:\Temp\claude\d--PROJECTS-Recorder-v0-1\32085d6a-fd25-43b9-b2db-fa89a499f340\scratchpad\export-probe\` (временный проект, в репозиторий не попадает)

**Interfaces:**
- Consumes: ничего
- Produces: решение «путь А работает» или «переходим на путь Б (Vite)». Остальные задачи предполагают путь А

- [ ] **Step 1: Создать пробный проект**

```bash
cd /c/Temp/claude/d--PROJECTS-Recorder-v0-1/32085d6a-fd25-43b9-b2db-fa89a499f340/scratchpad
npx create-next-app@latest export-probe --ts --tailwind --eslint --app --src-dir=false --import-alias "@/*" --no-turbopack --yes
cd export-probe
npx boardui@latest init
npx boardui@latest add button badge
```

- [ ] **Step 2: Включить экспорт**

`export-probe/next.config.ts`:

```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
```

- [ ] **Step 3: Страница с параметром запроса и компонентом BoardUI**

`export-probe/app/probe/page.tsx`:

```tsx
"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Button } from "@/components/base/buttons/button";

function Body() {
  const params = useSearchParams();
  return (
    <main className="p-8">
      <p className="text-body-regular">n = {params.get("n") ?? "нет"}</p>
      <Button variant="primary">Кнопка BoardUI</Button>
    </main>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<p>…</p>}>
      <Body />
    </Suspense>
  );
}
```

- [ ] **Step 4: Собрать и проверить результат**

```bash
npm run build
ls out/probe/index.html out/_next/static
```

Ожидается: сборка без ошибок, файл `out/probe/index.html` существует, каталог `out/_next/static` не пуст.

Если сборка падает на `useSearchParams` — значит, не хватает `Suspense`, вернуться к шагу 3. Если падает на `output: 'export'` — записать точный текст ошибки и остановить план: дальше идёт путь Б из спека, он требует переписывания задач 2, 6 и 14.

- [ ] **Step 5: Проверить отдачу собранного каталога обычным сервером**

```bash
cd out && python -m http.server 8123 &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8123/probe/index.html"
```

Ожидается: `200`.

- [ ] **Step 6: Записать вывод и удалить пробу**

Дописать в `docs/superpowers/specs/2026-09-12-boardui-frontend-design.md` в раздел «Риски» одну строку: «Статический экспорт Next 16.x с BoardUI проверен <дата>: собирается, `useSearchParams` требует `Suspense`.» Затем удалить каталог `export-probe`.

---

### Задача 2: Каркас web/ с тёмной темой и шрифтами

**Files:**
- Create: `web/` (проект Next.js целиком), `web/styles/recorder-theme.css`, `web/app/layout.tsx`, `web/app/page.tsx`, `web/.gitignore`
- Create: `web/lib/format.ts`
- Test: `web/app/page.tsx` служит проверкой сборки; автотестов UI нет по решению владельца

**Interfaces:**
- Consumes: решение задачи 1
- Produces: `npm run build` в `web/` даёт `web/out/index.html`; токены тёмной темы доступны как классы BoardUI; `web/lib/format.ts` экспортирует `formatDay(iso: string | null): string`, `formatClock(seconds: number): string`, `formatMinutes(value: number): string`

- [ ] **Step 1: Создать проект и поставить BoardUI**

```bash
cd /d/PROJECTS/Recorder_v0_1
npx create-next-app@latest web --ts --tailwind --eslint --app --src-dir=false --import-alias "@/*" --no-turbopack --yes
cd web
npx boardui@latest init
npx boardui@latest add button badge button-group input radio table dashboard-sidebar
```

- [ ] **Step 2: Включить экспорт и прокси для разработки**

`web/next.config.ts`:

```ts
import type { NextConfig } from "next";

const API = "http://127.0.0.1:8099";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  // Экспорт эти правила игнорирует: они нужны только `next dev`, чтобы
  // браузер видел API и аудио на том же origin и сам слал Basic-заголовок.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/audio/:path*", destination: `${API}/audio/:path*` },
    ];
  },
};

export default nextConfig;
```

- [ ] **Step 3: Записать тёмную тему**

`web/styles/recorder-theme.css`:

```css
/* ---------------------------------------------------------------------------
 * Тёмная тема диктофона поверх токенов BoardUI.
 *
 * Референс Officevibe светлый; здесь взят его принцип двух синих: Ink Navy
 * держит вес (канвас и карточки), Electric Cobalt отвечает за действие.
 * Файл импортируется после globals.css, поэтому перекрывает значения .dark.
 * Компоненты трогать не нужно: они читают эти же семантические токены.
 * --------------------------------------------------------------------------- */

.dark {
  /* Поверхности: канвас темнее карточки, карточка темнее вложенного блока. */
  --color-background-full: #080e33;
  --color-background-primary-default: #0c1754;
  --color-background-primary-hover: #132070;
  --color-background-primary-active: #132070;
  --color-background-inner-default: #0c1754;
  --color-background-secondary-default: #132070;
  --color-background-secondary-hover: #1b2a8a;
  --color-background-tertiary-default: #132070;
  --color-background-tertiary-hover: #1b2a8a;

  /* Текст. */
  --color-text-primary: #ffffff;
  --color-text-secondary: #eaebf8;
  --color-text-tertiary: rgba(234, 235, 248, 0.6);
  --color-text-placeholder: rgba(234, 235, 248, 0.45);
  --color-text-white: #ffffff;

  /* Иконки. */
  --color-foreground-icon-primary: #ffffff;
  --color-foreground-icon-secondary: #eaebf8;
  --color-foreground-icon-tertiary: rgba(234, 235, 248, 0.6);

  /* Линии: тёплый кремовый волос референса вместо холодного серого. */
  --color-separator-border: rgba(240, 233, 225, 0.14);
  --color-separator-border-strong: rgba(240, 233, 225, 0.24);
  --color-border-button-default: rgba(240, 233, 225, 0.14);
  --color-border-button-hover: rgba(240, 233, 225, 0.28);
  --color-border-button-active: rgba(240, 233, 225, 0.34);
  --color-border-table: rgba(240, 233, 225, 0.14);
  --color-border-checkbox-default: rgba(240, 233, 225, 0.28);

  /* Действие: единственная хроматическая точка на странице. */
  --color-accent-400: #3d5aff;
  --color-accent-500: #2545ff;
  --color-accent-600: #1c37d9;
  --color-accent-700: #1730b0;
  --color-border-focus-ring: #3d5aff;
}

/* Состояния источника и просрочки. Три цвета, больше не вводить. */
:root {
  --color-source-confirmed: #34d399;
  --color-source-unconfirmed: #fbbf24;
  --color-source-missing: #ff6b5c;
}

/* Подсветка поиска: лаванда на нави, как mark в референсе. */
mark {
  background-color: #eaebf8;
  color: #0c1754;
  border-radius: 0.25rem;
  padding: 0 0.15em;
}

/* Числа и время не должны прыгать по ширине при обновлении. */
.nums {
  font-variant-numeric: tabular-nums;
}
```

- [ ] **Step 4: Подключить шрифты и тему в макете**

`web/app/layout.tsx`:

```tsx
import type { Metadata } from "next";
import { Inter, Inter_Tight, Literata } from "next/font/google";
import "@/styles/globals.css";
import "@/styles/recorder-theme.css";

// subsets с cyrillic обязателен: интерфейс русский. Сборка падает, если
// у семейства нет кириллического субсета, — это и есть проверка.
const inter = Inter({ subsets: ["latin", "cyrillic"], variable: "--font-inter" });
const interTight = Inter_Tight({
  subsets: ["latin", "cyrillic"],
  weight: ["500"],
  variable: "--font-inter-tight",
});
const literata = Literata({
  subsets: ["latin", "cyrillic"],
  weight: ["400"],
  style: ["normal", "italic"],
  variable: "--font-literata",
});

export const metadata: Metadata = { title: "Диктофон" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={`dark ${inter.variable} ${interTight.variable} ${literata.variable}`}>
      <body className="bg-background-full text-text-primary">{children}</body>
    </html>
  );
}
```

- [ ] **Step 5: Собрать и убедиться, что кириллица есть**

Run: `cd web && npm run build`
Expected: сборка проходит, создан `web/out/index.html`.

Если сборка падает на `Inter_Tight` с сообщением про неизвестный subset `cyrillic` — заменить его на `Inter` с классом `tracking-[-0.02em]` в местах подзаголовков и записать это отклонение в спек. Выдуманного шрифта в интерфейсе быть не должно.

- [ ] **Step 6: Добавить форматтеры**

`web/lib/format.ts`:

```ts
/** Общие форматтеры. Дата и время показываются одинаково на всех страницах. */

const MONTHS = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

/** "2026-09-12" → "12 сентября 2026". Пусто → "дата неизвестна". */
export function formatDay(iso: string | null | undefined): string {
  if (!iso) return "дата неизвестна";
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return "дата неизвестна";
  return `${day} ${MONTHS[month - 1]} ${year}`;
}

/** Секунды от начала сессии → "Ч:ММ:СС". Часы без ведущего нуля. */
export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const mm = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${Math.floor(total / 3600)}:${mm}:${ss}`;
}

/** Минуты с одним знаком: "4.0". */
export function formatMinutes(value: number): string {
  return value.toFixed(1);
}
```

- [ ] **Step 7: Проверить типы и зафиксировать**

Run: `cd web && npx tsc --noEmit && npm run build`
Expected: обе команды без ошибок.

Добавить в `web/package.json` скрипт `"typecheck": "tsc --noEmit"`.

Корневой `.gitignore` уже покрывает `node_modules`, `web/.next` и `web/out`. Собранный каталог в историю не попадает: `tools/deploy.sh` собирает его сам и кладёт на VPS через rsync из локального каталога, а в истории он забивал бы diff каждой задачи тысячами строк.

```bash
git add -A && git commit -m "feat: каркас фронтенда на BoardUI с тёмной темой"
```

---

### Задача 3: Отдача статики из FastAPI и перенос старых страниц под /legacy

**Files:**
- Create: `server/static_site.py`
- Create: `server/test_api_ui.py`
- Modify: `server/app.py`

**Interfaces:**
- Consumes: `web.owner` (зависимость Basic-аутентификации), каталог `web/out` из задачи 2
- Produces: `static_site.router`, `static_site.WEB_DIR: Path`, переменная окружения `RECORDER_WEB_DIR`. Старые страницы доступны под `/legacy/...`

- [ ] **Step 1: Написать падающие тесты**

Добавить в новый `server/test_api_ui.py`:

```python
"""Проверки JSON-API и отдачи собранного фронтенда."""
import importlib
import json
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from test_pipeline import AUTH, PASSWORD, TOKEN, env, fake_transcribe, run_all, summary_stub, upload  # noqa: F401


@pytest.fixture
def site(env, tmp_path, monkeypatch):
    """Каталог сборки с двумя страницами: главной и страницей сессии."""
    out = tmp_path / "out"
    (out / "session").mkdir(parents=True)
    (out / "index.html").write_text("<!doctype html><title>Сводка</title>", encoding="utf-8")
    (out / "session" / "index.html").write_text("<!doctype html><title>Сессия</title>", encoding="utf-8")
    (out / "_next").mkdir()
    (out / "_next" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("RECORDER_WEB_DIR", str(out))

    import static_site, app as app_module
    importlib.reload(static_site)
    importlib.reload(app_module)
    env.app = app_module.app
    return env


def test_site_serves_index_and_nested_page(site):
    with TestClient(site.app) as client:
        assert client.get("/", auth=AUTH).text.startswith("<!doctype html>")
        assert "Сессия" in client.get("/session/?d=xiao-01&n=7", auth=AUTH).text
        assert client.get("/_next/app.js", auth=AUTH).status_code == 200


def test_site_is_closed_by_the_same_password(site):
    with TestClient(site.app) as client:
        assert client.get("/", auth=("owner", "wrong")).status_code == 401
        assert client.get("/_next/app.js").status_code == 401


def test_site_without_build_says_so_and_keeps_legacy(env, monkeypatch, tmp_path):
    monkeypatch.setenv("RECORDER_WEB_DIR", str(tmp_path / "нет-такого"))
    import static_site, app as app_module
    importlib.reload(static_site)
    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        response = client.get("/", auth=AUTH)
        assert response.status_code == 503
        assert "не собран" in response.text
        assert client.get("/legacy/", auth=AUTH).status_code == 200


def test_site_does_not_serve_files_outside_the_build(site):
    with TestClient(site.app) as client:
        assert client.get("/../../server/db.py", auth=AUTH).status_code in (400, 404)
```

- [ ] **Step 2: Запустить и увидеть падение**

Run: `cd server && python -m pytest test_api_ui.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'static_site'`.

- [ ] **Step 3: Вынести аутентификацию в отдельный модуль**

Новые модули не должны зависеть от `web.py`, который на последней задаче удаляется. Создать `server/auth.py`, перенеся в него из `web.py` без изменений: `OWNER_USER`, `OWNER_PASSWORD`, `security = HTTPBasic()` и функцию `owner` целиком вместе с комментариями. В `web.py` удалить перенесённое и добавить `from auth import owner` — остальные его страницы продолжают работать.

`server/auth.py`:

```python
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
```

- [ ] **Step 4: Написать отдачу статики**

`server/static_site.py`:

```python
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
    except OSError:
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
```

- [ ] **Step 5: Подключить маршруты в нужном порядке**

В `server/app.py` заменить подключение `web.router` на три строки. Catch-all идёт последним, иначе он перехватит `/api` и `/audio`:

```python
import static_site

app.include_router(web.router, prefix="/legacy")
# Catch-all регистрируется последним: иначе он перехватит /api и /audio.
app.include_router(static_site.router)
```

- [ ] **Step 6: Запустить тесты**

Run: `cd server && python -m pytest -q`
Expected: PASS. Тесты страниц из `test_pipeline.py` теперь бьют по старым адресам — поправить в них пути на `/legacy/...` (например, `client.get("/legacy/tasks", auth=AUTH)`).

- [ ] **Step 7: Проверить живьём**

```bash
cd server && RECORDER_DATA=../demo-data RECORDER_OWNER_PASSWORD=demo RECORDER_WEB_DIR=../web/out \
  python -m uvicorn app:app --port 8099 --host 127.0.0.1 &
sleep 4
curl -s -o /dev/null -w "статика: %{http_code}\n" -u owner:demo http://127.0.0.1:8099/
curl -s -o /dev/null -w "legacy: %{http_code}\n" -u owner:demo http://127.0.0.1:8099/legacy/tasks
```

Expected: обе строки `200`.

```bash
git add -A && git commit -m "<тип>: <что сделано в этой задаче>"
```

---

### Задача 4: JSON-API — сводка, список сессий, сессия, говорящие

**Files:**
- Create: `server/api_ui.py`
- Modify: `server/db.py` (добавить `session_rows`)
- Modify: `server/app.py` (подключить `api_ui.router`)
- Test: `server/test_api_ui.py`

**Interfaces:**
- Consumes: `db.digest`, `db.session_days`, `db.session_timeline`, `db.speaker_names`, `db.task_rows`, `db.question_rows`, `db.review_items`, `db.set_speaker`
- Produces: `db.session_rows(conn, limit=None) -> list[dict]`; роуты `GET /api/ui/digest`, `GET /api/ui/sessions`, `GET /api/ui/sessions/{device_id}/{session}`, `POST /api/ui/sessions/{device_id}/{session}/speakers`

- [ ] **Step 1: Написать падающие тесты**

Добавить в `server/test_api_ui.py`:

```python
def prepared(env, monkeypatch):
    """Сессия 7 с расшифровкой, саммари, задачей и вопросом."""
    upload(env)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)
    return env


def test_digest_gives_day_week_and_recent_sessions(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        body = client.get("/api/ui/digest", auth=AUTH).json()
    assert body["day"]["sessions"] == 1
    assert body["week"]["new_tasks"] == 1
    assert body["recent"][0]["session"] == 7
    assert body["my_tasks"] == []          # «это я» ещё не отмечен
    assert body["has_me"] is False


def test_sessions_list_has_progress_and_state(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        rows = client.get("/api/ui/sessions", auth=AUTH).json()
    assert rows[0]["session"] == 7
    assert rows[0]["parts"] == 1 and rows[0]["done_parts"] == 1
    assert rows[0]["summary_status"] == "done"
    assert rows[0]["day"].startswith("20")


def test_session_detail_carries_timeline_summary_and_tasks(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        body = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()
    assert body["summary"]["topics"] == ["смета для заказчика"]
    assert body["timeline"][0]["label"] == "Говорящий 1"
    assert body["timeline"][0]["start_abs"] == 1.0
    assert body["parts"][0]["part"] == 1
    assert body["tasks"][0]["text"] == "Выслать смету"
    assert body["speakers"] == [{"speaker": 0, "name": "", "is_me": False},
                                {"speaker": 1, "name": "", "is_me": False}]


def test_session_detail_404_for_unknown_session(env):
    with TestClient(env.app) as client:
        assert client.get("/api/ui/sessions/xiao-01/999", auth=AUTH).status_code == 404


def test_speakers_are_saved_as_json(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        response = client.post("/api/ui/sessions/xiao-01/7/speakers", auth=AUTH,
                               json={"names": {"0": "Игорь", "1": "Мария"}, "me": 0})
        assert response.status_code == 200
        body = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()
    assert body["timeline"][0]["label"] == "Игорь"
    with closing(env.db.connect()) as conn:
        assert env.db.speaker_names(conn, "xiao-01", 7)[0] == {"name": "Игорь", "is_me": True}


def test_api_requires_password(env):
    with TestClient(env.app) as client:
        assert client.get("/api/ui/digest").status_code == 401
```

- [ ] **Step 2: Запустить и увидеть падение**

Run: `cd server && python -m pytest test_api_ui.py -q`
Expected: FAIL — 404 на `/api/ui/digest`, роутов нет.

- [ ] **Step 3: Вынести список сессий в db.py**

Добавить в `server/db.py` в раздел «задачи, вопросы, проекты»:

```python
def session_rows(conn, limit=None):
    """Список сессий для интерфейса: свежие первыми.

    SQL жил в странице списка; вынесен сюда, потому что тот же список нужен
    сводке. Дублировать запрос в двух местах — способ получить две правды.
    """
    sql = (
        "SELECT p.device_id, p.session, COUNT(*) AS parts, SUM(p.bytes) AS bytes,"
        "       MIN(p.received_at) AS first_at, MAX(p.received_at) AS last_at,"
        "       (SELECT status FROM summaries s"
        "         WHERE s.device_id=p.device_id AND s.session=p.session) AS summary_status,"
        "       (SELECT COUNT(*) FROM transcripts t"
        "         WHERE t.device_id=p.device_id AND t.session=p.session"
        "           AND t.status='done') AS done_parts"
        " FROM parts p GROUP BY p.device_id, p.session"
        " ORDER BY p.received_at DESC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = []
    for row in conn.execute(sql).fetchall():
        item = dict(row)
        item["day"] = item["first_at"][:10] if item["first_at"] else None
        item["seconds"] = max(0, (item["bytes"] or 0)
                              - WAV_HEADER_BYTES * item["parts"]) / BYTES_PER_SECOND
        rows.append(item)
    return rows
```

- [ ] **Step 4: Написать роуты**

`server/api_ui.py`:

```python
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
```

- [ ] **Step 5: Подключить роутер до catch-all**

В `server/app.py` добавить `import api_ui` и строку `app.include_router(api_ui.router)` **перед** `app.include_router(static_site.router)`.

- [ ] **Step 6: Запустить тесты**

Run: `cd server && python -m pytest -q`
Expected: PASS.

```bash
git add -A && git commit -m "<тип>: <что сделано в этой задаче>"
```

---

### Задача 5: JSON-API — задачи, вопросы, проекты, спорные места, поиск, состояние

**Files:**
- Modify: `server/api_ui.py`
- Modify: `server/textnorm.py` (добавить `segments`)
- Modify: `server/db.py` (добавить `search_segments`)
- Test: `server/test_api_ui.py`

**Interfaces:**
- Consumes: `db.task_rows`, `db.set_task_status`, `db.question_rows`, `db.set_question_state`, `db.project_sessions`, `db.alias_map`, `db.set_alias`, `db.review_items`, `db.processing_status`, `db.search`
- Produces: `textnorm.segments(text, lemmas) -> list[dict]`; `db.search_segments(conn, query, limit=50) -> list[dict]`; роуты `GET|POST /api/ui/tasks…`, `…/questions…`, `…/projects…`, `GET /api/ui/review`, `GET /api/ui/status`, `GET /api/ui/search`

- [ ] **Step 1: Написать падающие тесты**

Добавить в `server/test_api_ui.py`:

```python
def test_tasks_filters_and_status_change(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        rows = client.get("/api/ui/tasks?show=open", auth=AUTH).json()
        assert rows["has_me"] is False and len(rows["tasks"]) == 1
        task_id = rows["tasks"][0]["task_id"]
        assert rows["tasks"][0]["due_date"]          # «пятница» разобрана
        # source приходит объектом, как в /review, а не строкой JSON из базы
        assert rows["tasks"][0]["source"]["confirmed"] is True

        assert client.post(f"/api/ui/tasks/xiao-01/7/{task_id}", auth=AUTH,
                           json={"status": "done"}).status_code == 200
        assert client.get("/api/ui/tasks?show=open", auth=AUTH).json()["tasks"] == []
        assert len(client.get("/api/ui/tasks?show=all", auth=AUTH).json()["tasks"]) == 1


def test_task_status_is_validated(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        task_id = client.get("/api/ui/tasks", auth=AUTH).json()["tasks"][0]["task_id"]
        assert client.post(f"/api/ui/tasks/xiao-01/7/{task_id}", auth=AUTH,
                           json={"status": "мусор"}).status_code == 422


def test_questions_state_roundtrip(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
    result = json.loads(json.dumps(SUMMARY_WITH_QUESTION))
    summary_stub(env, monkeypatch, result)
    run_all(env)
    with TestClient(env.app) as client:
        rows = client.get("/api/ui/questions?show=open", auth=AUTH).json()
        assert len(rows) == 1
        question_id = rows[0]["question_id"]
        client.post(f"/api/ui/questions/xiao-01/7/{question_id}", auth=AUTH,
                    json={"state": "answered"})
        assert client.get("/api/ui/questions?show=open", auth=AUTH).json() == []
        assert len(client.get("/api/ui/questions?show=all", auth=AUTH).json()) == 1


def test_projects_list_feed_and_alias(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.worker.transcribe, "transcribe_file", fake_transcribe(env))
    result = json.loads(json.dumps(SUMMARY_WITH_QUESTION))
    result["topics"] = ["Posiflora"]
    summary_stub(env, monkeypatch, result)
    run_all(env)
    with TestClient(env.app) as client:
        assert client.get("/api/ui/projects", auth=AUTH).json()["projects"][0]["name"] == "Posiflora"
        client.post("/api/ui/projects/aliases", auth=AUTH,
                    json={"alias": "posiflora", "canonical": "Пози флора"})
        listing = client.get("/api/ui/projects", auth=AUTH).json()
        assert listing["projects"][0]["name"] == "Пози флора"
        assert listing["aliases"] == [{"alias": "posiflora", "canonical": "Пози флора"}]
        feed = client.get("/api/ui/projects/Posiflora", auth=AUTH).json()
        assert feed["name"] == "Пози флора" and feed["sessions"][0]["session"] == 7


def test_search_returns_segments_not_html(env, monkeypatch):
    prepared(env, monkeypatch)
    if not env.db.textnorm.available():
        pytest.skip("pymorphy3 не установлен")
    with TestClient(env.app) as client:
        hits = client.get("/api/ui/search?q=сметы", auth=AUTH).json()
    assert hits[0]["session"] == 7
    assert any(part["hit"] for part in hits[0]["segments"])
    assert "".join(part["text"] for part in hits[0]["segments"]) == hits[0]["text"]
    assert "<mark>" not in json.dumps(hits, ensure_ascii=False)


def test_search_without_query_is_empty(env):
    with TestClient(env.app) as client:
        assert client.get("/api/ui/search?q=", auth=AUTH).json() == []


def test_review_and_status_are_exposed(env, monkeypatch):
    prepared(env, monkeypatch)
    with TestClient(env.app) as client:
        review = client.get("/api/ui/review", auth=AUTH).json()
        status = client.get("/api/ui/status", auth=AUTH).json()
    assert isinstance(review, list)
    assert status["transcripts_pending"] == 0
    assert "failed_parts" in status
```

Вверху `server/test_api_ui.py` добавить постоянную:

```python
SUMMARY_WITH_QUESTION = {
    "summary": "Обсудили смету.",
    "topics": ["смета для заказчика"],
    "decisions": [],
    "facts": [],
    "tasks": [{"text": "Выслать смету", "owner": "Мария", "due": "пятница",
               "time": "0:00:01", "quote": "к пятнице выслать смету заказчику"}],
    "open_questions": [{"text": "Кто смотрит договор", "time": "0:00:05",
                        "quote": "кто смотрит"}],
}
```

- [ ] **Step 2: Запустить и увидеть падение**

Run: `cd server && python -m pytest test_api_ui.py -q`
Expected: FAIL — 404 на `/api/ui/tasks`.

- [ ] **Step 3: Разбить текст на сегменты вместо HTML**

Добавить в `server/textnorm.py`:

```python
def segments(text, query_lemmas):
    """Текст кусками: {"text": ..., "hit": True} у слов, чья лемма совпала.

    Подсветку считает сервер, но отдаёт данными: HTML из распознанной речи
    в интерфейс попадать не должен ни в каком виде.
    """
    if not text:
        return []
    if not query_lemmas:
        return [{"text": text, "hit": False}]
    parts, position = [], 0
    for match in TOKEN_RE.finditer(text):
        if lemma(match.group(0)) not in query_lemmas:
            continue
        if match.start() > position:
            parts.append({"text": text[position:match.start()], "hit": False})
        parts.append({"text": match.group(0), "hit": True})
        position = match.end()
    if position < len(text):
        parts.append({"text": text[position:], "hit": False})
    return parts
```

Добавить в `server/db.py` рядом с `search`:

```python
def search_segments(conn, query, limit=50):
    """Попадания поиска с готовой разбивкой текста на совпавшие куски."""
    query_lemmas = {textnorm.lemma(token) for token in textnorm.tokens(query)}
    hits = search(conn, query, limit)
    for hit in hits:
        hit["segments"] = textnorm.segments(hit["text"], query_lemmas)
    return hits
```

- [ ] **Step 4: Дописать роуты**

Добавить в `server/api_ui.py`:

```python
from typing import Literal


class TaskStatus(BaseModel):
    status: Literal["open", "done", "dismissed"]


class QuestionState(BaseModel):
    state: Literal["open", "answered", "dropped"]


class Alias(BaseModel):
    alias: str
    canonical: str


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


@router.post("/projects/aliases")
def add_alias(body: Alias, _=Depends(owner)):
    with closing(db.connect()) as conn:
        db.set_alias(conn, body.alias[:80], body.canonical[:80])
    return {"ok": True}


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
```

- [ ] **Step 5: Запустить тесты**

Run: `cd server && python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Проверить живьём на демо-данных**

```bash
curl -s -u owner:demo "http://127.0.0.1:8099/api/ui/tasks?show=open" | python -c "import json,sys; d=json.load(sys.stdin); print(len(d['tasks']), d['tasks'][0]['due_date'])"
```

Expected: `2 2026-09-18`.

```bash
git add -A && git commit -m "<тип>: <что сделано в этой задаче>"
```

---

### Задача 6: Клиент API, каркас страницы, общие состояния

**Files:**
- Create: `web/lib/api.ts`, `web/lib/use-api.ts`
- Create: `web/components/recorder/shell.tsx`, `web/components/recorder/states.tsx`, `web/components/recorder/eyebrow.tsx`, `web/components/recorder/tile.tsx`
- Modify: `web/app/layout.tsx` (обернуть детей в каркас)

**Interfaces:**
- Consumes: роуты задач 4 и 5
- Produces:
  - `apiGet<T>(path: string): Promise<T>`, `apiPost<T>(path: string, body: unknown): Promise<T>`, `class AuthError extends Error`
  - `useApi<T>(path: string | null): { data: T | null; error: Error | null; loading: boolean; reload: () => void }`
  - `<Shell>{children}</Shell>` — боковая панель и навигация
  - `<Loading />`, `<Empty text="…" />`, `<Failed error={error} onRetry={fn} />`
  - `<PageTitle eyebrow="…" plain="…" italic="…" />`
  - `<Tile label="…" value={…} hint="…" />`

- [ ] **Step 1: Клиент API**

`web/lib/api.ts`:

```ts
/** Клиент JSON-API. Basic-заголовок ставит браузер: origin тот же. */

export class AuthError extends Error {
  constructor() {
    super("нужен вход заново");
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/ui${path}`, { credentials: "same-origin", ...init });
  if (response.status === 401) throw new AuthError();
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail.slice(0, 200)}`);
  }
  return (await response.json()) as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function apiPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export type Task = {
  device_id: string;
  session: number;
  task_id: string;
  text: string;
  owner: string;
  due_said: string;
  due_date: string | null;
  source: Source;
  status: "open" | "done" | "dismissed";
  vanished: number;
  mine: boolean;
  overdue: boolean;
};

export type Source = {
  part: number;
  idx: number;
  start: number;
  start_abs: number;
  confirmed: boolean;
} | null;

export type SummaryItem = {
  text: string;
  time: string;
  quote: string;
  owner?: string;
  due?: string;
  source?: Source;
};

export type Summary = {
  summary: string;
  topics: string[];
  decisions: SummaryItem[];
  facts: SummaryItem[];
  tasks: SummaryItem[];
  open_questions: SummaryItem[];
};

export type Utterance = {
  part: number;
  idx: number;
  start: number;
  end: number;
  start_abs: number;
  speaker: number | null;
  label: string;
  text: string;
};

export type SessionRow = {
  device_id: string;
  session: number;
  parts: number;
  done_parts: number;
  seconds: number;
  day: string | null;
  summary_status: string | null;
};

export type Counts = {
  minutes: number;
  sessions: number;
  new_tasks: number;
  my_open_tasks: number;
  open_tasks: number;
  overdue_tasks: number;
  open_questions: number;
  review: number;
  failed_parts: number;
  failed_summaries: number;
};

export type DigestBody = {
  day: Counts;
  week: Counts;
  my_tasks: Task[];
  has_me: boolean;
  recent: SessionRow[];
};

export type Question = {
  device_id: string;
  session: number;
  question_id: string;
  state: "open" | "answered" | "dropped";
  text: string;
  time: string;
  source: Source;
  day: string | null;
};

export type ReviewItem = {
  kind: string;
  device_id: string;
  session: number;
  day: string | null;
  text: string;
  detail: string;
  source: Source;
};

export type SearchHit = {
  device_id: string;
  session: number;
  part: number;
  idx: number;
  start_abs: number;
  label: string;
  text: string;
  segments: { text: string; hit: boolean }[];
};
```

- [ ] **Step 2: Хук загрузки**

`web/lib/use-api.ts`:

```ts
"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet } from "@/lib/api";

/** Загрузка одного адреса. path === null — не загружать (ждём параметр). */
export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (path === null) return;
    let alive = true;
    setLoading(true);
    apiGet<T>(path)
      .then((body) => alive && (setData(body), setError(null)))
      .catch((problem) => alive && setError(problem as Error))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path, tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { data, error, loading, reload };
}
```

- [ ] **Step 3: Общие состояния**

`web/components/recorder/states.tsx`:

```tsx
import { RiRefreshLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { AuthError } from "@/lib/api";

/** Скелетон: три полосы вместо прыжка пустой страницы. */
export function Loading() {
  return (
    <div className="flex flex-col gap-3" aria-busy="true">
      {[0, 1, 2].map((row) => (
        <div key={row} className="h-16 animate-pulse rounded-2xl bg-background-primary-default" />
      ))}
    </div>
  );
}

export function Empty({ text }: { text: string }) {
  return <p className="text-body-regular text-text-tertiary">{text}</p>;
}

export function Failed({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const auth = error instanceof AuthError;
  return (
    <div className="rounded-2xl border border-separator-border p-6">
      <p className="text-body-medium text-text-primary">
        {auth ? "Нужен вход заново: обновите страницу." : "Не удалось загрузить данные."}
      </p>
      <p className="mt-1 text-body-2-regular text-text-tertiary">{error.message}</p>
      {!auth && (
        <Button className="mt-4" variant="secondary" size="small"
                leadingIcon={RiRefreshLine} onClick={onRetry}>
          Повторить
        </Button>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Заголовок с курсивной фразой и плитка**

`web/components/recorder/eyebrow.tsx`:

```tsx
/**
 * Заголовок страницы. Курсивная фраза — подпись референса: серифный курсив
 * внутри обычной строки.
 */
export function PageTitle({ eyebrow, plain, italic }:
  { eyebrow: string; plain: string; italic?: string }) {
  return (
    <header className="mb-8">
      <p className="text-caption-1-semibold uppercase tracking-[0.12em] text-text-tertiary">
        {eyebrow}
      </p>
      <h1 className="mt-2 font-[family-name:var(--font-literata)] text-[40px] leading-[1.1] text-text-primary sm:text-5xl">
        {plain}
        {italic ? <> <em className="italic">{italic}</em></> : null}
      </h1>
    </header>
  );
}
```

`web/components/recorder/tile.tsx`:

```tsx
export function Tile({ label, value, hint }:
  { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-2xl border border-separator-border bg-background-primary-default p-6">
      <p className="text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">{label}</p>
      <p className="nums mt-2 text-title-1-medium text-text-primary">{value}</p>
      {hint ? <p className="mt-1 text-body-2-regular text-text-tertiary">{hint}</p> : null}
    </div>
  );
}
```

- [ ] **Step 5: Каркас с боковой панелью**

`web/components/recorder/shell.tsx`:

```tsx
"use client";

import { usePathname } from "next/navigation";
import {
  RiAlertLine, RiCheckboxLine, RiFolderLine, RiHome4Line, RiMicLine,
  RiPulseLine, RiQuestionLine, RiSearchLine,
} from "@remixicon/react";
import { DashboardSidebar } from "@/components/application/dashboard/dashboard-sidebar";
import { Badge } from "@/components/base/badges/badge";
import { useApi } from "@/lib/use-api";
import type { DigestBody } from "@/lib/api";

const NAV = [
  { key: "digest", label: "Сводка", icon: RiHome4Line, href: "/" },
  { key: "records", label: "Записи", icon: RiMicLine, href: "/records/" },
  { key: "tasks", label: "Задачи", icon: RiCheckboxLine, href: "/tasks/" },
  { key: "questions", label: "Вопросы", icon: RiQuestionLine, href: "/questions/" },
  { key: "projects", label: "Проекты", icon: RiFolderLine, href: "/projects/" },
  { key: "review", label: "Спорные", icon: RiAlertLine, href: "/review/" },
  { key: "search", label: "Поиск", icon: RiSearchLine, href: "/search/" },
  { key: "status", label: "Состояние", icon: RiPulseLine, href: "/status/" },
];

/**
 * Каркас всех страниц. Пункты меню живут здесь, а не в app-shell BoardUI:
 * тот зашит под их стартер (чат, дашборд, префикс сайта). Берём только
 * боковую панель.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const { data } = useApi<DigestBody>("/digest");

  const items = NAV.map((item) => {
    const selected = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
    let badge: React.ReactNode = null;
    if (item.key === "tasks" && data && data.week.my_open_tasks > 0) {
      badge = <Badge color="primary">{data.week.my_open_tasks}</Badge>;
    }
    if (item.key === "review" && data && data.week.review > 0) {
      badge = <Badge color="neutral">{data.week.review}</Badge>;
    }
    return { ...item, isSelected: selected, badge };
  });

  return (
    <div className="flex min-h-dvh gap-0 lg:gap-6">
      <DashboardSidebar items={items} />
      <main className="mx-auto w-full max-w-[1200px] px-6 py-8 sm:px-8">{children}</main>
    </div>
  );
}
```

Если состав свойств `DashboardSidebar` в установленной версии другой — подогнать вызов под установленный файл (читать `web/components/application/dashboard/dashboard-sidebar.tsx`), сам файл не править.

- [ ] **Step 6: Подключить каркас и проверить сборку**

В `web/app/layout.tsx` обернуть `{children}` в `<Shell>`.

Run: `cd web && npm run typecheck && npm run build`
Expected: без ошибок.

- [ ] **Step 7: Живая проверка**

Запустить `cd server && RECORDER_DATA=../demo-data RECORDER_OWNER_PASSWORD=demo python -m uvicorn app:app --port 8099 --host 127.0.0.1 &` и `cd web && npm run dev`. Открыть `http://127.0.0.1:3000/`, ввести owner/demo. Ожидается: тёмный фон `#080e33`, боковая панель, пункты меню, бейдж у «Спорных».

```bash
git add -A && git commit -m "<тип>: <что сделано в этой задаче>"
```

---

### Задача 7: Сводка `/`

**Files:**
- Create: `web/app/page.tsx`
- Create: `web/components/recorder/task-row.tsx`
- Create: `web/components/recorder/source-link.tsx`

**Interfaces:**
- Consumes: `/api/ui/digest`, `/api/ui/tasks/{d}/{n}/{id}`
- Produces:
  - `<TaskRow task={Task} onChange={(status) => void} />` — строка задачи с чипами и действиями
  - `<SourceDot source={Source} />` и `<ListenButton source={Source} device_id session />`

- [ ] **Step 1: Точка источника и кнопка «Послушать»**

`web/components/recorder/source-link.tsx`:

```tsx
import Link from "next/link";
import { RiPlayCircleLine } from "@remixicon/react";
import { cx } from "@/utils/cx";
import type { Source } from "@/lib/api";

/** Цвет точки — состояние источника: подтверждён, не подтверждён, не найден. */
export function SourceDot({ source }: { source: Source }) {
  const title = !source
    ? "источник не найден"
    : source.confirmed
      ? "цитата найдена в записи"
      : "источник не подтверждён";
  const color = !source
    ? "bg-[var(--color-source-missing)]"
    : source.confirmed
      ? "bg-[var(--color-source-confirmed)]"
      : "bg-[var(--color-source-unconfirmed)]";
  return <span title={title} aria-label={title} className={cx("inline-block size-2 rounded-full", color)} />;
}

/** Ссылка на реплику-основание внутри страницы сессии. */
export function ListenLink({ source, deviceId, session }:
  { source: Source; deviceId: string; session: number }) {
  if (!source) return null;
  return (
    <Link
      href={`/session/?d=${encodeURIComponent(deviceId)}&n=${session}#u-${source.part}-${source.idx}`}
      className="inline-flex items-center gap-1 text-body-2-medium text-text-secondary underline-offset-2 hover:underline"
    >
      <RiPlayCircleLine className="size-4" aria-hidden />
      Послушать основание
    </Link>
  );
}
```

- [ ] **Step 2: Строка задачи**

`web/components/recorder/task-row.tsx`:

```tsx
"use client";

import { useState } from "react";
import { Button } from "@/components/base/buttons/button";
import { ListenLink, SourceDot } from "@/components/recorder/source-link";
import { apiPost, type Task } from "@/lib/api";
import { formatDay } from "@/lib/format";
import { cx } from "@/utils/cx";

const ACTIONS: { status: Task["status"]; title: string }[] = [
  { status: "done", title: "Закрыть" },
  { status: "dismissed", title: "Снять" },
  { status: "open", title: "Вернуть" },
];

function due(task: Task) {
  if (!task.due_said) return "срок не назван";
  if (!task.due_date) return `${task.due_said} · дату не разобрали`;
  return `${task.due_said} · ${formatDay(task.due_date)}`;
}

export function TaskRow({ task, onChanged }: { task: Task; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);

  async function change(status: Task["status"]) {
    setBusy(true);
    try {
      await apiPost(`/tasks/${encodeURIComponent(task.device_id)}/${task.session}/${task.task_id}`,
                    { status });
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  const chips = [
    task.mine ? "моя" : null,
    task.overdue ? "просрочено" : null,
    task.vanished ? "исчезла из саммари" : null,
    task.status === "done" ? "закрыта" : task.status === "dismissed" ? "снята" : null,
  ].filter(Boolean) as string[];

  return (
    <li className="rounded-2xl border border-separator-border bg-background-primary-default p-6">
      <p className="text-headline-medium text-text-primary">{task.text}</p>
      <p className="mt-2 text-body-2-regular text-text-tertiary">
        {task.owner || "исполнитель не назван"} ·{" "}
        <span className={cx(task.overdue && "text-[var(--color-source-missing)]")}>{due(task)}</span>{" "}
        · сессия {String(task.session).padStart(4, "0")}
      </p>
      {chips.length > 0 && (
        <p className="mt-1 text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">
          {chips.join(" · ")}
        </p>
      )}
      <div className="mt-3 flex items-center gap-2">
        <SourceDot source={task.source} />
        <ListenLink source={task.source} deviceId={task.device_id} session={task.session} />
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        {ACTIONS.filter((action) => action.status !== task.status).map((action) => (
          <Button key={action.status} variant="ghost" size="small" isDisabled={busy}
                  onClick={() => change(action.status)}>
            {action.title}
          </Button>
        ))}
      </div>
    </li>
  );
}
```

`task.source` приходит из API уже объектом: `/api/ui/tasks` и `/api/ui/digest` разбирают его на сервере функцией `with_source` (задача 4). Парсить JSON на клиенте не нужно.

- [ ] **Step 3: Страница сводки**

`web/app/page.tsx`:

```tsx
"use client";

import Link from "next/link";
import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { TaskRow } from "@/components/recorder/task-row";
import { Tile } from "@/components/recorder/tile";
import { useApi } from "@/lib/use-api";
import type { DigestBody } from "@/lib/api";
import { formatDay, formatMinutes } from "@/lib/format";

/** Заголовок собирается из чисел дня; курсивом идёт последний член перечисления. */
function headline(body: DigestBody): { plain: string; italic: string } {
  const pieces: string[] = [];
  if (body.day.sessions) pieces.push(`${body.day.sessions} разговора`);
  if (body.day.new_tasks) pieces.push(`${body.day.new_tasks} задачи`);
  const italic = body.week.review
    ? `${body.week.review} спорных мест`
    : "ничего спорного";
  return { plain: pieces.length ? `Сегодня: ${pieces.join(", ")}, и` : "Сегодня тихо, а в работе", italic };
}

export default function DigestPage() {
  const { data, error, loading, reload } = useApi<DigestBody>("/digest");

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const today = new Date().toISOString().slice(0, 10);
  const title = headline(data);

  return (
    <>
      <PageTitle eyebrow={`ДИКТОФОН · ${formatDay(today).toUpperCase()}`}
                 plain={title.plain} italic={title.italic} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Tile label="Записано минут" value={formatMinutes(data.day.minutes)}
              hint={`за неделю ${formatMinutes(data.week.minutes)}`} />
        <Tile label="Разговоров" value={data.day.sessions} hint={`за неделю ${data.week.sessions}`} />
        <Tile label="Новых задач" value={data.day.new_tasks} hint={`за неделю ${data.week.new_tasks}`} />
        <Tile label="Вопросов без ответа" value={data.week.open_questions}
              hint={`просрочено задач: ${data.week.overdue_tasks}`} />
      </div>

      <section className="mt-12">
        <h2 className="text-title-3-semibold text-text-primary">Мои открытые задачи</h2>
        {!data.has_me ? (
          <p className="mt-3 rounded-2xl border border-separator-border p-6 text-body-regular text-text-secondary">
            Ни один говорящий не отмечен как «это я». Отметьте себя на странице сессии —
            тогда здесь появятся ваши задачи.
          </p>
        ) : data.my_tasks.length === 0 ? (
          <div className="mt-3"><Empty text="Открытых задач нет." /></div>
        ) : (
          <ul className="mt-3 flex flex-col gap-3">
            {data.my_tasks.map((task) => (
              <TaskRow key={task.task_id} task={task} onChanged={reload} />
            ))}
          </ul>
        )}
      </section>

      <section className="mt-12">
        <h2 className="text-title-3-semibold text-text-primary">Последние записи</h2>
        <ul className="mt-3 flex flex-col gap-3">
          {data.recent.map((row) => (
            <li key={`${row.device_id}-${row.session}`}>
              <Link href={`/session/?d=${encodeURIComponent(row.device_id)}&n=${row.session}`}
                    className="block rounded-2xl border border-separator-border bg-background-primary-default p-6 hover:bg-background-primary-hover">
                <p className="text-headline-medium text-text-primary">
                  Сессия {String(row.session).padStart(4, "0")}
                </p>
                <p className="mt-1 text-body-2-regular text-text-tertiary nums">
                  {formatDay(row.day)} · {formatMinutes(row.seconds / 60)} мин · частей {row.parts},
                  расшифровано {row.done_parts} ·{" "}
                  {row.summary_status === "done" ? "саммари готово" : "обрабатывается"}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
```

- [ ] **Step 4: Проверить сборку и типы**

Run: `cd web && npm run typecheck && npm run build`
Expected: без ошибок.

- [ ] **Step 5: Приёмка на живых данных**

Открыть `http://127.0.0.1:3000/` при запущенных `next dev` и сервере с демо-данными.
Ожидается: плитки показывают 4.0 минуты, 3 разговора, 2 новых задачи; блок «Мои открытые задачи» объясняет, что «это я» не отмечен; три последние записи кликабельны.

```bash
git add -A && git commit -m "<тип>: <что сделано в этой задаче>"
```

---

### Задача 8: Записи `/records`

**Files:**
- Create: `web/app/records/page.tsx`
- Create: `web/components/recorder/session-card.tsx`

**Interfaces:**
- Consumes: `/api/ui/sessions`
- Produces: `<SessionCard row={SessionRow} />` — используется на `/records`

- [ ] **Step 1: Карточка сессии**

`web/components/recorder/session-card.tsx`:

```tsx
import Link from "next/link";
import type { SessionRow } from "@/lib/api";
import { formatDay, formatMinutes } from "@/lib/format";

export function SessionCard({ row }: { row: SessionRow }) {
  const ready = row.summary_status === "done";
  return (
    <Link href={`/session/?d=${encodeURIComponent(row.device_id)}&n=${row.session}`}
          className="block rounded-2xl border border-separator-border bg-background-primary-default p-6 hover:bg-background-primary-hover">
      <div className="flex items-center justify-between gap-4">
        <p className="text-headline-medium text-text-primary">
          Сессия {String(row.session).padStart(4, "0")}
        </p>
        <span className="rounded-full border border-separator-border px-3 py-1 text-caption-1-medium text-text-secondary">
          {ready ? "саммари готово" : "обрабатывается"}
        </span>
      </div>
      <p className="nums mt-2 text-body-2-regular text-text-tertiary">
        {formatDay(row.day)} · {formatMinutes(row.seconds / 60)} мин · частей {row.parts},
        расшифровано {row.done_parts}
      </p>
    </Link>
  );
}
```

- [ ] **Step 2: Страница**

`web/app/records/page.tsx`:

```tsx
"use client";

import { PageTitle } from "@/components/recorder/eyebrow";
import { SessionCard } from "@/components/recorder/session-card";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { useApi } from "@/lib/use-api";
import type { SessionRow } from "@/lib/api";

export default function RecordsPage() {
  const { data, error, loading, reload } = useApi<SessionRow[]>("/sessions");

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Все" italic="записи" />
      {data.length === 0 ? (
        <Empty text="Пока пусто: записей с диктофона ещё не приходило." />
      ) : (
        <ul className="flex flex-col gap-3">
          {data.map((row) => (
            <li key={`${row.device_id}-${row.session}`}>
              <SessionCard row={row} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
```

- [ ] **Step 3: Сборка и приёмка**

Run: `cd web && npm run typecheck && npm run build`
Expected: без ошибок. На `http://127.0.0.1:3000/records/` видны три сессии демо-данных: 0009, 0008, 0007.

```bash
git add -A && git commit -m "<тип>: <что сделано в этой задаче>"
```

---

### Задача 9: Сессия `/session` с закреплённым плеером

**Files:**
- Create: `web/app/session/page.tsx`
- Create: `web/components/recorder/player.tsx`
- Create: `web/components/recorder/summary-list.tsx`
- Create: `web/components/recorder/speakers-form.tsx`

**Interfaces:**
- Consumes: `/api/ui/sessions/{d}/{n}`, `/api/ui/sessions/{d}/{n}/speakers`, `/audio/{d}/{n}/{part}`
- Produces:
  - `<PlayerProvider deviceId session parts>{children}</PlayerProvider>` и хук `useSeek(): (part: number, seconds: number) => void`
  - `<SummaryList title items deviceId session />`
  - `<SpeakersForm deviceId session speakers onSaved />`

- [ ] **Step 1: Плеер и контекст перемотки**

`web/components/recorder/player.tsx`:

```tsx
"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { formatClock } from "@/lib/format";

type SeekFn = (part: number, seconds: number) => void;

const SeekContext = createContext<SeekFn>(() => {});

export function useSeek() {
  return useContext(SeekContext);
}

/**
 * Плеер закреплён снизу по центру — место, где в референсе живёт чат-виджет.
 * Один элемент audio на страницу: параллельно играющие части путают.
 */
export function PlayerProvider({ deviceId, session, children }:
  { deviceId: string; session: number; children: React.ReactNode }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [part, setPart] = useState<number | null>(null);
  const [position, setPosition] = useState(0);

  const seek = useCallback<SeekFn>((nextPart, seconds) => {
    const element = audio.current;
    if (!element) return;
    const source = `/audio/${encodeURIComponent(deviceId)}/${session}/${nextPart}`;
    if (part !== nextPart) {
      element.src = source;
      setPart(nextPart);
    }
    const start = () => {
      element.currentTime = seconds;
      void element.play();
    };
    if (element.readyState >= 1) start();
    else element.addEventListener("loadedmetadata", start, { once: true });
  }, [deviceId, session, part]);

  const value = useMemo(() => seek, [seek]);

  return (
    <SeekContext.Provider value={value}>
      {children}
      <div className="fixed inset-x-0 bottom-0 z-20 mx-auto w-full max-w-[360px] px-4 pb-4">
        <div className="rounded-2xl border border-separator-border bg-background-primary-default p-4 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
          <p className="text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">
            {part === null ? "часть не выбрана" : `часть ${String(part).padStart(2, "0")}`}
            <span className="nums"> · {formatClock(position)}</span>
          </p>
          <audio ref={audio} controls preload="none" className="mt-2 w-full"
                 onTimeUpdate={(event) => setPosition(event.currentTarget.currentTime)} />
        </div>
      </div>
    </SeekContext.Provider>
  );
}
```

- [ ] **Step 2: Списки саммари**

`web/components/recorder/summary-list.tsx`:

```tsx
"use client";

import { useSeek } from "@/components/recorder/player";
import { SourceDot } from "@/components/recorder/source-link";
import type { SummaryItem } from "@/lib/api";

/** Пункт саммари: время, текст, состояние источника и перемотка на реплику. */
export function SummaryList({ title, items }: { title: string; items: SummaryItem[] }) {
  const seek = useSeek();
  if (items.length === 0) return null;

  return (
    <section className="mt-8">
      <h2 className="text-title-3-semibold text-text-primary">{title}</h2>
      <ul className="mt-3 flex flex-col gap-3">
        {items.map((item, index) => (
          <li key={`${item.time}-${index}`}
              className="rounded-2xl border border-separator-border bg-background-primary-default p-6">
            <p className="text-body-regular text-text-primary">
              <span className="nums mr-2 text-text-tertiary">{item.time}</span>
              {item.text}
            </p>
            {(item.owner !== undefined || item.due !== undefined) && (
              <p className="mt-1 text-body-2-regular text-text-tertiary">
                {item.owner || "исполнитель не назван"} · {item.due || "срок не назван"}
              </p>
            )}
            <div className="mt-3 flex items-center gap-2">
              <SourceDot source={item.source ?? null} />
              {item.source ? (
                <button type="button"
                        className="text-body-2-medium text-text-secondary underline-offset-2 hover:underline"
                        onClick={() => seek(item.source!.part, item.source!.start)}>
                  Послушать основание
                </button>
              ) : (
                <span className="text-body-2-regular text-text-tertiary">источник не найден</span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
```

- [ ] **Step 3: Форма говорящих**

`web/components/recorder/speakers-form.tsx`:

```tsx
"use client";

import { useState } from "react";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { Radio, RadioGroup } from "@/components/base/radio/radio";
import { apiPost } from "@/lib/api";

type Speaker = { speaker: number; name: string; is_me: boolean };

export function SpeakersForm({ deviceId, session, speakers, onSaved }:
  { deviceId: string; session: number; speakers: Speaker[]; onSaved: () => void }) {
  const [names, setNames] = useState(() =>
    Object.fromEntries(speakers.map((s) => [String(s.speaker), s.name])));
  const [me, setMe] = useState(() => {
    const found = speakers.find((s) => s.is_me);
    return found ? String(found.speaker) : "";
  });
  const [busy, setBusy] = useState(false);

  if (speakers.length === 0) return null;

  async function save() {
    setBusy(true);
    try {
      await apiPost(`/sessions/${encodeURIComponent(deviceId)}/${session}/speakers`,
                    { names, me: me === "" ? null : Number(me) });
      onSaved();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-8">
      <h2 className="text-title-3-semibold text-text-primary">Говорящие</h2>
      <p className="mt-1 text-body-2-regular text-text-tertiary">
        Номера — метки диаризации, не личности. Исходный транскрипт не меняется.
      </p>
      <RadioGroup className="mt-3 flex flex-col gap-3" value={me} onChange={setMe}
                  aria-label="Кто из говорящих — это я">
        {speakers.map((speaker) => (
          <div key={speaker.speaker} className="flex flex-wrap items-center gap-3">
            <span className="w-32 text-body-2-regular text-text-tertiary">
              Говорящий {speaker.speaker + 1}
            </span>
            <Input className="min-w-40 flex-1" placeholder="Имя"
                   value={names[String(speaker.speaker)] ?? ""}
                   onChange={(value: string) =>
                     setNames((old) => ({ ...old, [String(speaker.speaker)]: value }))} />
            <Radio value={String(speaker.speaker)}>это я</Radio>
          </div>
        ))}
        <Radio value="">меня нет</Radio>
      </RadioGroup>
      <Button className="mt-4" variant="primary" size="small" isDisabled={busy} onClick={save}>
        Сохранить
      </Button>
    </section>
  );
}
```

Свойства `Input` в установленной версии BoardUI могут отличаться (например, `onChange` принимает событие). Прочитать `web/components/base/input/input.tsx` и подогнать вызов, файл не править.

- [ ] **Step 4: Страница сессии**

`web/app/session/page.tsx`:

```tsx
"use client";

import { Suspense, useMemo } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { PageTitle } from "@/components/recorder/eyebrow";
import { PlayerProvider, useSeek } from "@/components/recorder/player";
import { SpeakersForm } from "@/components/recorder/speakers-form";
import { SummaryList } from "@/components/recorder/summary-list";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { useApi } from "@/lib/use-api";
import type { Summary, Utterance } from "@/lib/api";
import { formatClock, formatDay } from "@/lib/format";

type Detail = {
  device_id: string;
  session: number;
  day: string;
  parts: { part: number; recovered: number; transcript_status: string | null; last_error: string | null }[];
  summary: Summary | null;
  summary_error: string | null;
  timeline: Utterance[];
  speakers: { speaker: number; name: string; is_me: boolean }[];
};

function Transcript({ timeline }: { timeline: Utterance[] }) {
  const seek = useSeek();
  // Группировка заранее: заголовок части ставится по данным, а не по счётчику,
  // который менялся бы прямо во время отрисовки.
  const byPart = useMemo(() => {
    const groups: { part: number; items: Utterance[] }[] = [];
    for (const item of timeline) {
      const last = groups[groups.length - 1];
      if (last && last.part === item.part) last.items.push(item);
      else groups.push({ part: item.part, items: [item] });
    }
    return groups;
  }, [timeline]);

  return (
    <section className="mt-8 pb-40">
      <h2 className="text-title-3-semibold text-text-primary">Транскрипт</h2>
      <p className="mt-1 text-body-2-regular text-text-tertiary">
        Нажмите на реплику, чтобы послушать.
      </p>
      {byPart.map((group) => (
        <div key={group.part} className="mt-4 flex flex-col gap-1">
          <p className="text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">
            часть {String(group.part).padStart(2, "0")}
          </p>
          {group.items.map((u) => (
            <button key={`${u.part}-${u.idx}`} type="button" id={`u-${u.part}-${u.idx}`}
                    onClick={() => seek(u.part, u.start)}
                    className="w-full rounded-xl px-3 py-2 text-left hover:bg-background-secondary-default target:bg-background-secondary-default">
              <span className="nums mr-2 text-text-tertiary">{formatClock(u.start_abs)}</span>
              <span className="text-body-medium text-text-primary">{u.label}:</span>{" "}
              <span className="text-body-regular text-text-secondary">{u.text}</span>
            </button>
          ))}
        </div>
      ))}
    </section>
  );
}

function Body() {
  const params = useSearchParams();
  const deviceId = params.get("d") ?? "";
  const session = Number(params.get("n") ?? 0);
  const ready = Boolean(deviceId) && Number.isFinite(session) && session > 0;
  const { data, error, loading, reload } =
    useApi<Detail>(ready ? `/sessions/${encodeURIComponent(deviceId)}/${session}` : null);

  if (!ready) return <Empty text="Не указана сессия." />;
  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const minutes = data.parts.length;

  return (
    <PlayerProvider deviceId={data.device_id} session={data.session}>
      <PageTitle
        eyebrow={`СЕССИЯ ${String(data.session).padStart(4, "0")} · ${formatDay(data.day).toUpperCase()} · ЧАСТЕЙ ${minutes}`}
        plain="Разговор и его" italic="основания" />

      {data.summary ? (
        <>
          <p className="text-body-regular leading-6 text-text-secondary">{data.summary.summary}</p>
          {data.summary.topics.length > 0 && (
            <p className="mt-3 flex flex-wrap gap-2">
              {data.summary.topics.map((topic) => (
                <Link key={topic} href={`/project/?name=${encodeURIComponent(topic)}`}
                      className="rounded-full border border-separator-border px-3 py-1 text-caption-1-medium text-text-secondary hover:border-border-button-hover">
                  {topic}
                </Link>
              ))}
            </p>
          )}
          <SummaryList title="Решения" items={data.summary.decisions} />
          <SummaryList title="Факты" items={data.summary.facts} />
          <SummaryList title="Задачи" items={data.summary.tasks} />
          <SummaryList title="Вопросы без ответа" items={data.summary.open_questions} />
        </>
      ) : (
        <Empty text="Саммари ещё не готово." />
      )}

      {data.summary_error && (
        <p className="mt-4 rounded-2xl border border-separator-border p-4 text-body-2-regular text-text-tertiary">
          Последняя ошибка саммари: {data.summary_error}
        </p>
      )}

      {data.parts.filter((part) => part.transcript_status === "failed").map((part) => (
        <p key={part.part} className="mt-4 rounded-2xl border border-separator-border p-4 text-body-2-regular text-text-tertiary">
          Расшифровка части {String(part.part).padStart(2, "0")} не удалась: {part.last_error}
        </p>
      ))}

      <SpeakersForm deviceId={data.device_id} session={data.session}
                    speakers={data.speakers} onSaved={reload} />

      {data.timeline.length === 0
        ? <div className="mt-8"><Empty text="Расшифровки пока нет." /></div>
        : <Transcript timeline={data.timeline} />}
    </PlayerProvider>
  );
}

export default function SessionPage() {
  // Статический экспорт требует Suspense вокруг useSearchParams.
  return (
    <Suspense fallback={<Loading />}>
      <Body />
    </Suspense>
  );
}
```

- [ ] **Step 5: Сборка**

Run: `cd web && npm run typecheck && npm run build`
Expected: без ошибок. Если сборка ругается на `useSearchParams` — проверить, что `Suspense` обёрнут вокруг компонента, который его вызывает.

- [ ] **Step 6: Приёмка на живых данных**

Открыть `http://127.0.0.1:3000/session/?d=xiao-01&n=7`.
Ожидается: темы ведут на `/project/`, у пункта «Проверить пункт о пени» жёлтая точка «источник не подтверждён», клик по «Послушать основание» запускает нужную часть с нужной секунды, клик по реплике перематывает, форма говорящих сохраняет имена и после перезагрузки подписи в транскрипте меняются на «Игорь» и «Мария».

```bash
git add -A && git commit -m "<тип>: <что сделано в этой задаче>"
```

---

### Задача 10: Задачи `/tasks`

**Files:**
- Create: `web/app/tasks/page.tsx`
- Create: `web/components/recorder/filters.tsx`

**Interfaces:**
- Consumes: `/api/ui/tasks`, `/api/ui/tasks/{d}/{n}/{id}`, `<TaskRow>` из задачи 7
- Produces: `<Filters value options onChange />` — ряд фильтров, используется на `/tasks` и `/questions`

- [ ] **Step 1: Фильтры**

`web/components/recorder/filters.tsx`:

```tsx
"use client";

import { ButtonGroup, ButtonGroupItem } from "@/components/base/buttons/button-group";

export function Filters({ value, options, onChange }: {
  value: string;
  options: { key: string; title: string }[];
  onChange: (key: string) => void;
}) {
  return (
    <ButtonGroup selectedKeys={[value]} selectionMode="single"
                 onSelectionChange={(keys) => {
                   const [first] = Array.from(keys as Set<string>);
                   if (first) onChange(first);
                 }}>
      {options.map((option) => (
        <ButtonGroupItem key={option.key} id={option.key}>{option.title}</ButtonGroupItem>
      ))}
    </ButtonGroup>
  );
}
```

Свойства `ButtonGroup` сверить с установленным `web/components/base/buttons/button-group.tsx`; при расхождении подогнать обёртку.

- [ ] **Step 2: Страница задач**

`web/app/tasks/page.tsx`:

```tsx
"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Filters } from "@/components/recorder/filters";
import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { TaskRow } from "@/components/recorder/task-row";
import { useApi } from "@/lib/use-api";
import type { Task } from "@/lib/api";

const FILTERS = [
  { key: "open", title: "Открытые" },
  { key: "mine", title: "Мои" },
  { key: "overdue", title: "Просроченные" },
  { key: "all", title: "Все" },
];

function Body() {
  const params = useSearchParams();
  const session = params.get("session");
  const [show, setShow] = useState(params.get("show") ?? "open");
  const query = `/tasks?show=${show}${session ? `&session=${session}` : ""}`;
  const { data, error, loading, reload } = useApi<{ tasks: Task[]; has_me: boolean }>(query);

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Что надо" italic="сделать" />
      <Filters value={show} options={FILTERS} onChange={setShow} />

      {session && (
        <p className="mt-3 text-body-2-regular text-text-tertiary">
          Только сессия {String(Number(session)).padStart(4, "0")}.
        </p>
      )}

      <div className="mt-6">
        {error ? <Failed error={error} onRetry={reload} />
          : loading || !data ? <Loading />
          : show === "mine" && !data.has_me ? (
            <p className="rounded-2xl border border-separator-border p-6 text-body-regular text-text-secondary">
              Ни один говорящий не отмечен как «это я»: отметьте себя на странице сессии,
              иначе «Мои» ничего не найдут.
            </p>
          ) : data.tasks.length === 0 ? <Empty text="Ничего нет." />
          : (
            <ul className="flex flex-col gap-3">
              {data.tasks.map((task) => (
                <TaskRow key={`${task.session}-${task.task_id}`} task={task} onChanged={reload} />
              ))}
            </ul>
          )}
      </div>
    </>
  );
}

export default function TasksPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Body />
    </Suspense>
  );
}
```

- [ ] **Step 3: Сборка и приёмка**

Run: `cd web && npm run typecheck && npm run build`
Expected: без ошибок.

На `http://127.0.0.1:3000/tasks/`: две задачи, у первой срок «пятница · 18 сентября 2026», «Закрыть» убирает её из фильтра «Открытые» и оставляет в «Все» с чипом «закрыта». После проверки вернуть демо-данные: `python tools/seed_demo.py`.

```bash
git add -A && git commit -m "<тип>: <что сделано в этой задаче>"
```

---

### Задача 11: Вопросы `/questions` и спорные места `/review`

**Files:**
- Create: `web/app/questions/page.tsx`
- Create: `web/app/review/page.tsx`

**Interfaces:**
- Consumes: `/api/ui/questions`, `/api/ui/questions/{d}/{n}/{id}`, `/api/ui/review`
- Produces: ничего для других задач

- [ ] **Step 1: Страница вопросов**

`web/app/questions/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import { Button } from "@/components/base/buttons/button";
import { Filters } from "@/components/recorder/filters";
import { PageTitle } from "@/components/recorder/eyebrow";
import { ListenLink, SourceDot } from "@/components/recorder/source-link";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { apiPost, type Question } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { formatDay } from "@/lib/format";

const FILTERS = [{ key: "open", title: "Без ответа" }, { key: "all", title: "Все" }];
const ACTIONS: { state: Question["state"]; title: string }[] = [
  { state: "answered", title: "Ответ получен" },
  { state: "dropped", title: "Снять" },
  { state: "open", title: "Вернуть" },
];
const MARKS: Record<Question["state"], string> = {
  open: "", answered: "ответ получен", dropped: "снят",
};

export default function QuestionsPage() {
  const [show, setShow] = useState("open");
  const { data, error, loading, reload } = useApi<Question[]>(`/questions?show=${show}`);

  async function change(question: Question, state: Question["state"]) {
    await apiPost(
      `/questions/${encodeURIComponent(question.device_id)}/${question.session}/${question.question_id}`,
      { state });
    reload();
  }

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Вопросы" italic="без ответа" />
      <Filters value={show} options={FILTERS} onChange={setShow} />

      <div className="mt-6">
        {error ? <Failed error={error} onRetry={reload} />
          : loading || !data ? <Loading />
          : data.length === 0 ? <Empty text="Ничего нет." />
          : (
            <ul className="flex flex-col gap-3">
              {data.map((question) => (
                <li key={`${question.session}-${question.question_id}`}
                    className="rounded-2xl border border-separator-border bg-background-primary-default p-6">
                  <p className="text-headline-medium text-text-primary">{question.text}</p>
                  <p className="mt-1 text-body-2-regular text-text-tertiary">
                    {formatDay(question.day)} · сессия {String(question.session).padStart(4, "0")}
                    {MARKS[question.state] ? ` · ${MARKS[question.state]}` : ""}
                  </p>
                  <div className="mt-3 flex items-center gap-2">
                    <SourceDot source={question.source} />
                    <ListenLink source={question.source} deviceId={question.device_id}
                                session={question.session} />
                  </div>
                  <div className="mt-4 flex flex-wrap gap-3">
                    {ACTIONS.filter((action) => action.state !== question.state).map((action) => (
                      <Button key={action.state} variant="ghost" size="small"
                              onClick={() => change(question, action.state)}>
                        {action.title}
                      </Button>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
      </div>
    </>
  );
}
```

- [ ] **Step 2: Страница спорных мест**

`web/app/review/page.tsx`:

```tsx
"use client";

import { PageTitle } from "@/components/recorder/eyebrow";
import { ListenLink, SourceDot } from "@/components/recorder/source-link";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { useApi } from "@/lib/use-api";
import type { ReviewItem } from "@/lib/api";
import { formatDay } from "@/lib/format";

export default function ReviewPage() {
  const { data, error, loading, reload } = useApi<ReviewItem[]>("/review");

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  const groups = new Map<string, ReviewItem[]>();
  for (const item of data) {
    const bucket = groups.get(item.kind) ?? [];
    bucket.push(item);
    groups.set(item.kind, bucket);
  }

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Требует" italic="владельца" />
      <p className="text-body-regular text-text-secondary">
        Обработка ничего из этого не ждёт: это подсказка, а не блокировка.
      </p>

      <div className="mt-6">
        {data.length === 0 ? <Empty text="Нечего проверять." /> : (
          [...groups.entries()].map(([kind, items]) => (
            <section key={kind} className="mt-8">
              <h2 className="text-title-3-semibold text-text-primary">
                {kind} <span className="nums text-text-tertiary">· {items.length}</span>
              </h2>
              <ul className="mt-3 flex flex-col gap-3">
                {items.map((item, index) => (
                  <li key={`${item.session}-${index}`}
                      className="rounded-2xl border border-separator-border bg-background-primary-default p-6">
                    <p className="text-body-regular text-text-primary">{item.text}</p>
                    <p className="mt-1 text-body-2-regular text-text-tertiary">
                      {item.detail} · {formatDay(item.day)} ·
                      сессия {String(item.session).padStart(4, "0")}
                    </p>
                    <div className="mt-3 flex items-center gap-2">
                      <SourceDot source={item.source} />
                      <ListenLink source={item.source} deviceId={item.device_id}
                                  session={item.session} />
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ))
        )}
      </div>
    </>
  );
}
```

- [ ] **Step 3: Сборка и приёмка**

Run: `cd web && npm run typecheck && npm run build`
Expected: без ошибок.

На демо-данных `/questions/` показывает «Кто согласует фасад с городом», «Ответ получен» убирает его из фильтра «Без ответа». `/review/` показывает три группы: «задача без исполнителя», «источник не подтверждён», «число распознано неуверенно».

```bash
git add -A && git commit -m "<тип>: <что сделано в этой задаче>"
```

---

### Задача 12: Проекты `/projects` и лента `/project`

**Files:**
- Create: `web/app/projects/page.tsx`
- Create: `web/app/project/page.tsx`

**Interfaces:**
- Consumes: `/api/ui/projects`, `/api/ui/projects/{name}`, `/api/ui/projects/aliases`
- Produces: ничего для других задач

- [ ] **Step 1: Список проектов и форма псевдонима**

`web/app/projects/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { apiPost } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { formatDay } from "@/lib/format";

type Listing = {
  projects: { name: string; sessions: number; last_day: string | null }[];
  aliases: { alias: string; canonical: string }[];
};

export default function ProjectsPage() {
  const { data, error, loading, reload } = useApi<Listing>("/projects");
  const [alias, setAlias] = useState("");
  const [canonical, setCanonical] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    if (!alias.trim() || !canonical.trim()) return;
    setBusy(true);
    try {
      await apiPost("/projects/aliases", { alias, canonical });
      setAlias("");
      setCanonical("");
      reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Разговоры по" italic="проектам" />

      {error ? <Failed error={error} onRetry={reload} />
        : loading || !data ? <Loading />
        : (
          <>
            {data.projects.length === 0 ? (
              <Empty text="Пока пусто: тема разговора появляется в саммари после обработки следующих записей." />
            ) : (
              <ul className="grid gap-3 sm:grid-cols-2">
                {data.projects.map((project) => (
                  <li key={project.name}>
                    <Link href={`/project/?name=${encodeURIComponent(project.name)}`}
                          className="block rounded-2xl border border-separator-border bg-background-primary-default p-6 hover:bg-background-primary-hover">
                      <p className="text-headline-medium text-text-primary">{project.name}</p>
                      <p className="nums mt-1 text-body-2-regular text-text-tertiary">
                        разговоров: {project.sessions} · последний {formatDay(project.last_day)}
                      </p>
                    </Link>
                  </li>
                ))}
              </ul>
            )}

            <section className="mt-12">
              <h2 className="text-title-3-semibold text-text-primary">Псевдонимы</h2>
              <p className="mt-1 text-body-2-regular text-text-tertiary">
                «Пози флора» и «Posiflora» — один проект: сведите их здесь.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <Input className="min-w-48 flex-1" placeholder="Как прозвучало"
                       value={alias} onChange={setAlias} />
                <Input className="min-w-48 flex-1" placeholder="Название проекта"
                       value={canonical} onChange={setCanonical} />
                <Button variant="primary" size="small" isDisabled={busy} onClick={save}>
                  Сохранить
                </Button>
              </div>
              {data.aliases.length > 0 && (
                <ul className="mt-4 flex flex-col gap-2">
                  {data.aliases.map((item) => (
                    <li key={item.alias} className="text-body-2-regular text-text-tertiary">
                      {item.alias} → {item.canonical}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
    </>
  );
}
```

- [ ] **Step 2: Лента проекта**

`web/app/project/page.tsx`:

```tsx
"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { useApi } from "@/lib/use-api";
import { formatDay } from "@/lib/format";

type Feed = {
  name: string;
  sessions: {
    device_id: string;
    session: number;
    day: string | null;
    summary: string | null;
    decisions: { text: string }[];
    tasks: { text: string }[];
    open_questions: { text: string }[];
  }[];
};

function Group({ title, items }: { title: string; items: { text: string }[] }) {
  if (items.length === 0) return null;
  return (
    <>
      <p className="mt-4 text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">
        {title}
      </p>
      <ul className="mt-1 list-disc pl-5">
        {items.map((item, index) => (
          <li key={index} className="text-body-regular text-text-secondary">{item.text}</li>
        ))}
      </ul>
    </>
  );
}

function Body() {
  const name = useSearchParams().get("name") ?? "";
  const { data, error, loading, reload } =
    useApi<Feed>(name ? `/projects/${encodeURIComponent(name)}` : null);

  if (!name) return <Empty text="Проект не указан." />;
  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  return (
    <>
      <PageTitle eyebrow="ПРОЕКТ" plain={data.name} />
      {data.sessions.length === 0 ? <Empty text="Разговоров по этой теме нет." /> : (
        <ol className="flex flex-col gap-6">
          {data.sessions.map((item) => (
            <li key={`${item.device_id}-${item.session}`}
                className="rounded-2xl border border-separator-border bg-background-primary-default p-6">
              <p className="font-[family-name:var(--font-literata)] text-title-3-semibold text-text-primary">
                {formatDay(item.day)}{" "}
                <Link href={`/session/?d=${encodeURIComponent(item.device_id)}&n=${item.session}`}
                      className="text-body-2-medium text-text-tertiary underline-offset-2 hover:underline">
                  сессия {String(item.session).padStart(4, "0")}
                </Link>
              </p>
              <p className="mt-2 text-body-regular leading-6 text-text-secondary">{item.summary}</p>
              <Group title="Решения" items={item.decisions} />
              <Group title="Задачи" items={item.tasks} />
              <Group title="Вопросы" items={item.open_questions} />
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

export default function ProjectPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Body />
    </Suspense>
  );
}
```

- [ ] **Step 3: Сборка и приёмка**

Run: `cd web && npm run typecheck && npm run build`
Expected: без ошибок.

На демо-данных `/projects/` показывает два проекта; после добавления псевдонима «смета заказчику» → «Смета для заказчика» список сводится. Переход с темы на странице сессии ведёт в ленту, и она открывается по псевдониму тоже.

```bash
git add -A && git commit -m "<тип>: <что сделано в этой задаче>"
```

---

### Задача 13: Поиск `/search` и состояние `/status`

**Files:**
- Create: `web/app/search/page.tsx`
- Create: `web/app/status/page.tsx`

**Interfaces:**
- Consumes: `/api/ui/search`, `/api/ui/status`
- Produces: ничего для других задач

- [ ] **Step 1: Поиск**

`web/app/search/page.tsx`:

```tsx
"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { useApi } from "@/lib/use-api";
import type { SearchHit } from "@/lib/api";
import { formatClock } from "@/lib/format";

function Body() {
  const initial = useSearchParams().get("q") ?? "";
  const [draft, setDraft] = useState(initial);
  const [query, setQuery] = useState(initial);
  const { data, error, loading, reload } =
    useApi<SearchHit[]>(query.trim() ? `/search?q=${encodeURIComponent(query)}` : null);

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Найти" italic="сказанное" />
      <form className="flex flex-wrap items-center gap-3"
            onSubmit={(event) => { event.preventDefault(); setQuery(draft); }}>
        <Input className="min-w-48 flex-1" placeholder="Слово или фраза"
               value={draft} onChange={setDraft} />
        <Button type="submit" variant="primary" size="small">Найти</Button>
      </form>

      <div className="mt-6">
        {!query.trim() ? <Empty text="Введите слово: поиск идёт по леммам, «сметы» найдёт «смету»." />
          : error ? <Failed error={error} onRetry={reload} />
          : loading || !data ? <Loading />
          : data.length === 0 ? <Empty text="Ничего не найдено." />
          : (
            <ul className="flex flex-col gap-3">
              {data.map((hit) => (
                <li key={`${hit.session}-${hit.part}-${hit.idx}`}>
                  <Link href={`/session/?d=${encodeURIComponent(hit.device_id)}&n=${hit.session}#u-${hit.part}-${hit.idx}`}
                        className="block rounded-2xl border border-separator-border bg-background-primary-default p-6 hover:bg-background-primary-hover">
                    <p className="nums text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">
                      сессия {String(hit.session).padStart(4, "0")} · {formatClock(hit.start_abs)}
                    </p>
                    <p className="mt-2 text-body-regular text-text-secondary">
                      <span className="text-body-medium text-text-primary">{hit.label}: </span>
                      {hit.segments.map((part, index) =>
                        part.hit ? <mark key={index}>{part.text}</mark> : <span key={index}>{part.text}</span>)}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
      </div>
    </>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Body />
    </Suspense>
  );
}
```

- [ ] **Step 2: Состояние обработки**

`web/app/status/page.tsx`:

```tsx
"use client";

import {
  Table, TableBody, TableCell, TableColumn, TableHeader, TableRow,
} from "@/components/base/table/table";
import { PageTitle } from "@/components/recorder/eyebrow";
import { Empty, Failed, Loading } from "@/components/recorder/states";
import { Tile } from "@/components/recorder/tile";
import { useApi } from "@/lib/use-api";
import { formatMinutes } from "@/lib/format";

type Status = {
  transcripts_pending: number;
  summaries_pending: number;
  deepgram_minutes: number;
  failed_parts: { session: number; part: number; attempts: number; last_error: string }[];
  stuck_summaries: { session: number; attempts: number; last_error: string }[];
  recovered_parts: { session: number; part: number }[];
  last_upload: { device_id: string; at: string; parts: number }[];
  gaps: { session: number; missing: number[] }[];
  duration_mismatch: { session: number; part: number; by_size: number; by_deepgram: number }[];
};

function Rows({ title, hint, head, rows }: {
  title: string; hint?: string; head: string[]; rows: (string | number)[][];
}) {
  return (
    <section className="mt-10">
      <h2 className="text-title-3-semibold text-text-primary">{title}</h2>
      {hint ? <p className="mt-1 text-body-2-regular text-text-tertiary">{hint}</p> : null}
      {rows.length === 0 ? <div className="mt-3"><Empty text="Нет." /></div> : (
        <Table aria-label={title} size="sm" containerClassName="mt-3 rounded-2xl border border-separator-border">
          <TableHeader>
            {head.map((cell) => <TableColumn key={cell} isRowHeader={cell === head[0]}>{cell}</TableColumn>)}
          </TableHeader>
          <TableBody>
            {rows.map((row, index) => (
              <TableRow key={index}>
                {row.map((cell, cellIndex) => <TableCell key={cellIndex} className="nums">{cell}</TableCell>)}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}

export default function StatusPage() {
  const { data, error, loading, reload } = useApi<Status>("/status");

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading />;

  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН" plain="Что происходит с" italic="обработкой" />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <Tile label="В очереди на расшифровку" value={data.transcripts_pending} />
        <Tile label="Ждут саммари" value={data.summaries_pending} />
        <Tile label="Расшифровано Deepgram" value={`${formatMinutes(data.deepgram_minutes)} мин`}
              hint="оценка расхода кредита" />
      </div>

      <Rows title="Последняя выгрузка с диктофона" head={["Устройство", "Когда", "Частей всего"]}
            rows={data.last_upload.map((row) => [row.device_id, row.at, row.parts])} />
      <Rows title="Части с ошибкой расшифровки" head={["Сессия", "Часть", "Попыток", "Ошибка"]}
            rows={data.failed_parts.map((row) => [row.session, row.part, row.attempts, row.last_error])} />
      <Rows title="Саммари с ошибкой" head={["Сессия", "Попыток", "Ошибка"]}
            rows={data.stuck_summaries.map((row) => [row.session, row.attempts, row.last_error])} />
      <Rows title="Пропуски частей"
            hint="Номера отсутствуют: запись потеряна или ещё не выгружена."
            head={["Сессия", "Нет частей"]}
            rows={data.gaps.map((row) => [row.session, row.missing.join(", ")])} />
      <Rows title="Расхождение длительности"
            hint="Размер файла и ответ Deepgram расходятся больше чем на 2%: часть аудио могла не обработаться."
            head={["Сессия", "Часть", "По файлу", "По Deepgram"]}
            rows={data.duration_mismatch.map((row) =>
              [row.session, row.part, `${row.by_size.toFixed(1)} с`, `${row.by_deepgram.toFixed(1)} с`])} />
      <Rows title="Восстановленные части"
            hint="Питание пропало во время записи, заголовок починен при старте диктофона."
            head={["Сессия", "Часть"]}
            rows={data.recovered_parts.map((row) => [row.session, row.part])} />
    </>
  );
}
```

- [ ] **Step 3: Сборка и приёмка**

Run: `cd web && npm run typecheck && npm run build`
Expected: без ошибок.

На демо-данных `/search/?q=сметы` подсвечивает «смету» и ведёт на реплику. `/status/` показывает пропуск части 3 в сессии 0009 и расхождение длительности.

```bash
git add -A && git commit -m "<тип>: <что сделано в этой задаче>"
```

---

### Задача 14: Сборка, развёртка, удаление старого интерфейса

**Files:**
- Create: `tools/deploy.sh`
- Modify: `server/README_RU.md`
- Modify: `server/app.py` (убрать `/legacy`)
- Delete: `server/web.py` кроме функции `owner`, тесты страниц в `server/test_pipeline.py`

**Interfaces:**
- Consumes: всё предыдущее
- Produces: `server/auth.py` с функцией `owner`; `tools/deploy.sh`

- [ ] **Step 1: Перенести отдачу аудио**

Роут `/audio/{device_id}/{session}/{part}` из `web.py` перенести в `api_ui.py` без изменений тела (`FileResponse` с Range обязателен для Safari). Тест `test_audio_supports_range_requests` в `test_pipeline.py` менять не требуется — адрес тот же.

- [ ] **Step 2: Удалить старый интерфейс**

Удалить `server/web.py` целиком и строку `app.include_router(web.router, prefix="/legacy")` из `app.py`. В фикстуре `env` файла `server/test_pipeline.py` убрать `web` из списка импорта и перезагрузки модулей, а `bundle.web` заменить на `bundle.textnorm = textnorm` (единственное обращение — проверка `env.db.textnorm.available()` в тесте поиска, она становится `env.textnorm.available()`). Из `server/test_pipeline.py` удалить тесты, которые бьют по HTML-страницам: `test_search_page_highlights_and_links_to_utterance`, `test_session_page_has_seek_targets_and_sources`, `test_web_marks_missing_owner_and_due_instead_of_inventing`, `test_rename_speaker_changes_labels_not_raw_data` (его проверка данных переезжает в `test_api_ui.py::test_speakers_are_saved_as_json`), `test_tasks_page_closes_task_by_form`, `test_task_form_does_not_redirect_outside`, `test_web_escapes_transcript_text`, `test_status_sees_gap_failure_and_duration_mismatch` (часть про страницу; проверку `db.processing_status` оставить).

- [ ] **Step 3: Запустить тесты**

Run: `cd server && python -m pytest -q`
Expected: PASS. Ни один тест не должен обращаться к `web.py`.

- [ ] **Step 4: Скрипт развёртки**

`tools/deploy.sh`:

```bash
#!/usr/bin/env bash
# Сборка фронтенда и выкладка на VPS. Node нужен только здесь.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${RECORDER_HOST:-config1}"
TARGET="${RECORDER_TARGET:-/opt/recorder}"

cd "$ROOT/web"
npm run typecheck
npm run build

cd "$ROOT"
rsync -av --delete \
  --exclude='__pycache__' \
  server/ "$HOST:$TARGET/server/"
rsync -av --delete shared/ "$HOST:$TARGET/shared/"
rsync -av --delete web/out/ "$HOST:$TARGET/web/out/"

echo "Выложено. Перезапустить службы:"
echo "  ssh $HOST 'sudo systemctl restart recorder-api recorder-worker'"
```

Сделать исполняемым: `chmod +x tools/deploy.sh`.

- [ ] **Step 5: Переписать README**

В `server/README_RU.md`:
- в таблицу файлов добавить `api_ui.py`, `static_site.py`, `auth.py`, убрать `web.py`;
- добавить раздел «Интерфейс» с описанием сборки (`cd web && npm run build`), переменной `RECORDER_WEB_DIR` и адресами страниц;
- в «Настройки» добавить `RECORDER_WEB_DIR=/opt/recorder/web/out`;
- в «Запуск» заменить число проверок на фактическое из `pytest -q`;
- в «Незакрытые вопросы» добавить измеренный размер бандла: взять из вывода `npm run build` (строка First Load JS) и записать числом, а не оценкой.

- [ ] **Step 6: Финальная проверка целиком**

```bash
cd /d/PROJECTS/Recorder_v0_1 && python tools/seed_demo.py
cd web && npm run build
cd ../server && python -m pytest -q
RECORDER_DATA=../demo-data RECORDER_OWNER_PASSWORD=demo RECORDER_WEB_DIR=../web/out \
  python -m uvicorn app:app --port 8099 --host 127.0.0.1 &
sleep 4
for p in "" records/ tasks/ questions/ projects/ review/ search/ status/ "session/?d=xiao-01&n=7"; do
  printf "%s -> " "/$p"
  curl -s -o /dev/null -w "%{http_code}\n" -u owner:demo "http://127.0.0.1:8099/$p"
done
```

Expected: все строки `200`. Открыть в браузере `http://127.0.0.1:8099/`, пройти по всем восьми пунктам меню, проверить на ширине телефона (в инструментах разработчика 390×844): боковая панель становится выдвижной, плитки в две колонки, горизонтальной прокрутки нет, плеер на странице сессии не перекрывает последнюю реплику.

```bash
git add -A && git commit -m "<тип>: <что сделано в этой задаче>"
```

---

## Самопроверка плана

**Покрытие спека.** Раздел 1 спека (структура и сборка) — задачи 1, 2, 3, 14. Раздел 2 (JSON-API) — задачи 4, 5; все четырнадцать роутов таблицы спека присутствуют. Раздел 3 (девять экранов) — задачи 7–13; каждый экран из таблицы спека имеет свою задачу и приёмку. Раздел 4 (визуальная система) — задача 2 (токены и шрифты) плюс обязательные классы в каждом компоненте. Раздел 5 (проверки) — шаги `pytest`, `typecheck`, `build` в каждой задаче. Раздел 6 (миграция) — порядок задач повторяет четыре шага спека: проба, каркас, экраны по одному, удаление старого. Раздел 7 (развёртка) — задача 14, шаг 5.

**Согласованность имён.** `useApi` возвращает `{ data, error, loading, reload }` и используется с этими именами на всех девяти страницах. `apiPost(path, body)` — три вызова: задачи, вопросы, говорящие, псевдонимы. `SourceDot` и `ListenLink` определены в задаче 7 и используются в задачах 9, 11. `Filters` определён в задаче 10 и используется в задаче 11. `formatDay`, `formatClock`, `formatMinutes` определены в задаче 2. `db.session_rows` определён в задаче 4 и используется в задачах 4 и 7 (через `/digest` и `/sessions`). `textnorm.segments` и `db.search_segments` определены в задаче 5, используются в задаче 13.

**Известные места, требующие сверки с установленным BoardUI.** Свойства `DashboardSidebar` (задача 6), `Input` (задачи 9, 12, 13), `ButtonGroup` (задача 10) взяты по описанию компонентов; в установленной версии они могут отличаться. В каждой такой задаче есть шаг «прочитать установленный файл и подогнать вызов, сам файл не править». Это не заглушка: варианты известны, требуется одна сверка при исполнении.
