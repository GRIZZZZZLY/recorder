# Узнавание людей по голосу — план встраивания

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** владелец один раз называет человека на странице записи — дальше сервер сам подписывает этот голос во всех новых частях и во всей текущей сессии, а неуверенные случаи оставляет «Говорящим N».

**Architecture:** после расшифровки части воркер снимает с каждого номера говорящего, у которого хватает речи, отпечаток голоса (192 числа, модель CAM++ через `sherpa-onnx`) и хранит его в таблице `voice_prints`. Профиль человека — это отпечатки всех номеров, которые владелец назвал этим именем руками; отдельной таблицы профилей нет. Имя, найденное по голосу, пишется в существующую таблицу `speaker_names` с пометкой `auto=1`, поэтому шкала, транскрипт для саммари, поиск и списки получают его без изменений.

**Tech Stack:** Python 3.10 (VPS), FastAPI, SQLite, `sherpa-onnx` 1.13 (без PyTorch), numpy (уже стоит на VPS), Next.js/TypeScript (фронтенд), pytest.

**Spec:** раздел «Контекст» ниже — запрос владельца от 8 октября 2026 и замер на записи 0016.

## Контекст

- Расшифровку с 7 октября делает Gemini порциями по 5 минут; номера говорящих в каждой порции свои (`gemini.py`: `speaker_base` +10 на порцию). На 0016 (7 частей, ~70 мин) вышло 53 номера.
- Замер 8 октября 2026 на 0016, модель `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx`: две половины речи одного номера похожи в среднем на 0,90 (10-й перцентиль 0,55), разные номера одной порции — на 0,32 (90-й перцентиль 0,60). Порог 0,6: свой номер узнаётся в 83% случаев, чужие склеиваются в 9%. 53 номера собрались в 4 основных голоса; владелец прослушал нарезку и подтвердил: голоса совпадают.
- Модель WeSpeaker ResNet34 на тех же данных бесполезна (всё похоже на всё, 0,91–0,98) — не использовать.
- Ресурсы: `sherpa-onnx` — 135 МБ вместе с зависимостями, процесс с моделью — 91 МБ памяти; на VPS свободно ~2 ГБ ОЗУ и 20 ГБ диска. Вся сессия 0016 (три отпечатка на номер) — 70 с процессора.

## Global Constraints

- Только `sherpa-onnx` + ONNX-модель CAM++; PyTorch, SpeechBrain, WeSpeaker не ставить.
- Модель: `https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx`, SHA-256 `aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2`, на VPS лежит в `/var/lib/recorder/models/campplus.onnx`. В git не класть.
- Ручки: `RECORDER_VOICE=0` выключает; `RECORDER_VOICE_MODEL` (по умолчанию `$RECORDER_DATA/models/campplus.onnx`); `RECORDER_VOICE_THRESHOLD=0.6`; `RECORDER_VOICE_MARGIN=0.1`; `RECORDER_VOICE_MIN_SEC=8`.
- Строку владельца (`speaker_names.auto=0`) автоматика не меняет и не удаляет никогда.
- Имена по голосу (`auto=1`) в профиль не идут — профиль строится только из имён владельца.
- Сбой узнавания (нет библиотеки, нет модели, ffmpeg, исключение) не роняет расшифровку и воркер: часть остаётся без имён, одна строка в лог.
- Отпечатки голоса — биометрия: удаляются вместе с сессией и не пишутся в удалённую сессию.
- Без модели сервер работает как раньше.
- Стиль: комментарии и docstring по-русски, в духе соседнего кода; идентификаторы по-английски; коммиты по-английски с `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Тесты: `cd server && python -m pytest -q -p no:cacheprovider`; на VPS: `bash tools/remote_tests.sh -q -p no:cacheprovider` (запускается с машины разработки).

## Review Focus

1. **Владелец жмёт «Сохранить», не трогая поля с именами по голосу.** Форма шлёт все поля как есть. Ожидание: имена по голосу остаются догадками (`auto=1`), пустые поля не создают строк и не запрещают узнавание. Тест — задача 4.
2. **Часть расшифрована повторно** (кнопка «повторить» или ручной перезапуск, как 14–16 седьмого октября): номера говорящих в ней другие. Ожидание: старые отпечатки и имена по голосу этой части заменяются; имена владельца остаются. Тест — задача 2.
3. **Нет `sherpa-onnx`, нет файла модели, ffmpeg не читает файл, модель падает.** Ожидание: расшифровка сохранена, имён по голосу нет, воркер жив. Тесты — задача 3.
4. **Сессию удалили, в том числе пока часть расшифровывалась.** Ожидание: ни одного отпечатка этой сессии в базе. Тест — задача 2.
5. **Два похожих голоса или одно имя двумя написаниями** («Игорь» и «игорь» — два профиля). Ожидание: имени нет, а не неверное имя (правило разрыва). Тест — задача 1.

Известное и в план не входит: имена владельца у части, расшифрованной повторно, могут указывать на чужие номера — это было и до плана. Отпечатки позволят перенести их по голосу; отдельная задача, если владелец попросит.

---

## Карта файлов

| Файл | Что делает |
|---|---|
| `server/voiceprint.py` (новый) | правила сравнения, снятие отпечатков, подпись сессии, `--backfill` |
| `server/db.py` | таблица `voice_prints`, столбец `speaker_names.auto`, функции хранения |
| `server/worker.py` | после `save_transcript` — `voiceprint.remember_part` |
| `server/api_ui.py` | правило «нетронутое поле — не подтверждение», поле `auto` в ответе, подпись сессии после сохранения |
| `server/test_voiceprint.py` (новый) | все проверки фичи |
| `server/test_pipeline.py` | `voiceprint` в списке перезагружаемых модулей фикстуры `env` |
| `server/test_api_ui.py` | ожидание словаря имени дополняется `auto` |
| `server/requirements.txt`, `server/README_RU.md` | зависимость и описание ручек |
| `web/components/recorder/speakers-form.tsx`, `web/app/session/page.tsx` | пометка «по голосу» |

---

### Task 1: Правила сравнения голосов

**Files:**
- Create: `server/voiceprint.py`
- Create: `server/test_voiceprint.py`

**Interfaces:**
- Produces: `normalized(vector) -> list[float]`, `similarity(a, b) -> float`, `centroid(vectors) -> list[float]`, `best_name(vector, profiles: dict[str, list[float]]) -> str | None`, `pack(vector) -> bytes`, `unpack(blob) -> list[float]`, `label_spans(utterances) -> dict[int, list[tuple[float, float]]]`; константы `THRESHOLD`, `MARGIN`, `MIN_SEC`, `MODEL`, `ENABLED`, `RATE`.

- [ ] **Step 1: Write the failing tests**

`server/test_voiceprint.py`:

```python
"""Узнавание по голосу: правила сравнения, хранение отпечатков, подписи номеров."""
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import voiceprint as vp
from test_pipeline import AUTH, env, fake_transcribe, run_all, summary_stub, upload  # noqa: F401


def test_voice_is_named_only_when_clearly_closest():
    profiles = {"Игорь": vp.normalized([1, 0, 0]), "Алексей": vp.normalized([0, 1, 0])}
    assert vp.best_name(vp.normalized([0.95, 0.1, 0]), profiles) == "Игорь"
    # Ни на кого не похож — никто, а не ближайший: новый человек не должен
    # внезапно стать Алексеем.
    assert vp.best_name(vp.normalized([0, 0, 1]), profiles) is None


def test_two_close_profiles_give_no_name():
    profiles = {"Игорь": vp.normalized([1, 0, 0]), "Алексей": vp.normalized([0.95, 0.31, 0])}
    assert vp.best_name(vp.normalized([1, 0, 0]), profiles) is None


def test_single_profile_needs_only_the_threshold():
    assert vp.best_name(vp.normalized([0.9, 0.3, 0]), {"Игорь": vp.normalized([1, 0, 0])}) == "Игорь"
    assert vp.best_name(vp.normalized([1, 0, 0]), {}) is None


def test_print_survives_storage_round_trip():
    vector = vp.normalized([0.1, -0.2, 0.3])
    assert vp.unpack(vp.pack(vector)) == pytest.approx(vector, abs=1e-6)


def test_profile_is_the_mean_direction_of_its_prints():
    assert vp.centroid([[1, 0], [0, 1]]) == pytest.approx([0.7071, 0.7071], abs=1e-4)


def test_only_labels_with_enough_speech_get_a_print():
    utterances = [
        {"s": 0, "e": 5, "spk": 0, "text": "а"}, {"s": 6, "e": 10, "spk": 0, "text": "б"},
        {"s": 11, "e": 13, "spk": 1, "text": "в"},             # 2 с — мало
        {"s": 14, "e": 14.3, "spk": 2, "text": "г"},           # короче 0,5 с — шум
        {"s": 15, "e": 25, "spk": None, "text": "д"},          # без номера
    ]
    assert vp.label_spans(utterances) == {0: [(0.0, 5.0), (6.0, 10.0)]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && python -m pytest test_voiceprint.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'voiceprint'`.

- [ ] **Step 3: Write minimal implementation**

`server/voiceprint.py`:

```python
"""Узнавание людей по голосу между частями и сессиями.

Номер говорящего от движка расшифровки живёт внутри своей части, а у Gemini —
внутри 5-минутной порции: на записи 0016 за 70 минут вышло 53 номера на
4 основных голоса. Здесь с каждого номера, у которого хватает речи, снимается
отпечаток голоса — 192 числа от модели CAM++ (sherpa-onnx, без PyTorch), — и
номер получает имя, если голос уверенно похож на того, кого владелец уже
назвал сам.

Профиль человека — все номера, которые владелец назвал этим именем руками.
Отдельной таблицы профилей нет: переименование или удаление сессии сразу
меняет профиль. Имена, поставленные здесь, в профиль не идут — иначе одна
ошибка тянула бы за собой следующие.

Замер 8 октября 2026 на 0016 (CAM++): две половины речи одного номера похожи
в среднем на 0,90, разные номера одной порции — на 0,32. Порог 0,6: свой
номер узнаётся в 83% случаев, чужие склеиваются в 9%. Владелец прослушал
нарезку четырёх найденных голосов: совпадают.

Без sherpa-onnx или файла модели сервер работает как раньше: номера без имён.
"""
import array
import logging
import math
import os
from pathlib import Path

import db

log = logging.getLogger("voiceprint")

ENABLED = os.environ.get("RECORDER_VOICE", "1") != "0"
MODEL = Path(os.environ.get("RECORDER_VOICE_MODEL",
                            str(db.DATA_DIR / "models" / "campplus.onnx")))
# Порог и разрыв подобраны на 0016; ручки — чтобы подстроить на новых записях
# без выкладки кода.
THRESHOLD = float(os.environ.get("RECORDER_VOICE_THRESHOLD", "0.6"))
MARGIN = float(os.environ.get("RECORDER_VOICE_MARGIN", "0.1"))
# Меньше 8 секунд речи — отпечаток случаен; такой номер остаётся без имени.
MIN_SEC = float(os.environ.get("RECORDER_VOICE_MIN_SEC", "8"))
MIN_UTTERANCE_SEC = 0.5
RATE = 16000


def normalized(vector):
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def similarity(a, b):
    """Косинус для уже нормированных векторов."""
    return sum(x * y for x, y in zip(a, b))


def centroid(vectors):
    return normalized([sum(column) for column in zip(*vectors)])


def best_name(vector, profiles):
    """Имя из profiles ({имя: нормированный вектор}) или None.

    Не угадываем: мало похоже — никто; два имени почти одинаково близко —
    тоже никто. «Говорящий 3» честнее, чем чужое имя под чужими словами."""
    scores = sorted(((similarity(vector, centre), name) for name, centre in profiles.items()),
                    reverse=True)
    if not scores or scores[0][0] < THRESHOLD:
        return None
    if len(scores) > 1 and scores[0][0] - scores[1][0] < MARGIN:
        return None
    return scores[0][1]


def pack(vector):
    return array.array("f", vector).tobytes()


def unpack(blob):
    vector = array.array("f")
    vector.frombytes(blob)
    return list(vector)


def label_spans(utterances):
    """{номер: [(начало, конец), ...]} для номеров, у которых речи хватает
    на отпечаток. Реплики короче полсекунды — чаще междометие или шум."""
    spans = {}
    for u in utterances:
        start, end, speaker = float(u.get("s") or 0), float(u.get("e") or 0), u.get("spk")
        if speaker is None or end - start < MIN_UTTERANCE_SEC:
            continue
        spans.setdefault(speaker, []).append((start, end))
    return {speaker: found for speaker, found in spans.items()
            if sum(end - start for start, end in found) >= MIN_SEC}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && python -m pytest test_voiceprint.py -q -p no:cacheprovider`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add server/voiceprint.py server/test_voiceprint.py
git commit -m "feat(server): voice matching rules for speaker recognition"
```

---

### Task 2: Хранение отпечатков и имён по голосу

**Files:**
- Modify: `server/db.py` — `SCHEMA` (таблица `speaker_names` ~строка 130, новая таблица после неё), `init_storage` (после пересборки `speaker_names`, ~строка 292), `speaker_names` (~481), `SESSION_TABLES` (~524), `set_speaker` (~589)
- Modify: `server/test_pipeline.py:103` — список перезагружаемых модулей
- Modify: `server/test_api_ui.py:279` — ожидание словаря имени
- Test: `server/test_voiceprint.py`

**Interfaces:**
- Consumes: `voiceprint.pack` (только в тестах).
- Produces:
  - `db.speaker_names(conn, device_id, session) -> {(part, speaker): {"name": str, "is_me": bool, "auto": bool}}`
  - `db.save_voice_prints(conn, key: tuple[str, int, int], prints: dict[int, tuple[bytes, float]]) -> None`
  - `db.voice_prints(conn) -> list[sqlite3.Row]` с полями `device_id, session, part, speaker, vec, owner_name, has_owner_row`
  - `db.set_auto_names(conn, device_id, session, names: dict[tuple[int, int], str | None]) -> int` (сколько строк изменилось)

- [ ] **Step 1: Write the failing tests**

В `server/test_pipeline.py` в фикстуре `env` добавить `voiceprint` в импорт и в кортеж перезагрузки сразу после `transcribe` (модуль читает `RECORDER_DATA` при импорте и должен перечитать его после `db`):

```python
    import archive, db, duedate, textnorm, stt, transcribe, summarize, voiceprint
    import app as app_module
    import worker

    for module in (textnorm, duedate, db, stt, transcribe, voiceprint, archive, summarize,
                   app_module, worker):
        importlib.reload(module)
```

В конец `server/test_voiceprint.py`:

```python
KEY = ("xiao-01", 7, 1)


def test_auto_names_never_touch_the_owner_and_report_changes(env):
    upload(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        names = {(1, 0): "Алексей", (1, 1): "Мария"}
        assert db.set_auto_names(conn, "xiao-01", 7, names) == 1
        assert db.set_auto_names(conn, "xiao-01", 7, names) == 0   # повтор ничего не меняет
        got = db.speaker_names(conn, "xiao-01", 7)
        assert got[(1, 0)] == {"name": "Игорь", "is_me": False, "auto": False}
        assert got[(1, 1)] == {"name": "Мария", "is_me": False, "auto": True}
        assert db.set_auto_names(conn, "xiao-01", 7, {(1, 1): None, (1, 0): None}) == 1
        assert db.speaker_names(conn, "xiao-01", 7) == {
            (1, 0): {"name": "Игорь", "is_me": False, "auto": False}}


def test_changed_auto_names_send_the_summary_back_to_the_queue(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)
    with closing(env.db.connect()) as conn:
        env.db.set_auto_names(conn, "xiao-01", 7, {(1, 0): "Мария"})
        covered = conn.execute("SELECT parts_covered FROM summaries WHERE session=7").fetchone()[0]
    # Имя попадает в текст для модели — саммари с «Говорящим 1» устарело.
    assert covered == 0


def test_new_prints_of_a_part_replace_old_ones_and_their_auto_names(env):
    upload(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack([1.0, 0, 0]), 9.0),
                                         1: (vp.pack([0, 1.0, 0]), 12.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        db.set_auto_names(conn, "xiao-01", 7, {(1, 1): "Мария"})
        # Повторная расшифровка: номера другие, старые подписи по голосу неверны.
        db.save_voice_prints(conn, KEY, {0: (vp.pack([0, 0, 1.0]), 10.0)})
        rows = db.voice_prints(conn)
        assert [(r["speaker"], r["owner_name"], bool(r["has_owner_row"])) for r in rows] \
            == [(0, "Игорь", True)]
        assert db.speaker_names(conn, "xiao-01", 7) == {
            (1, 0): {"name": "Игорь", "is_me": False, "auto": False}}


def test_prints_of_a_deleted_session_are_gone_and_not_written_back(env):
    upload(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack([1.0, 0, 0]), 9.0)})
        assert db.delete_session(conn, "xiao-01", 7)
        assert db.voice_prints(conn) == []
        # Расшифровка, закончившаяся после удаления, отпечатки не возвращает.
        db.save_voice_prints(conn, KEY, {0: (vp.pack([1.0, 0, 0]), 9.0)})
        assert db.voice_prints(conn) == []


def test_old_speaker_names_become_owner_rows(env):
    db = env.db
    with closing(db.connect()) as conn:
        conn.execute("DROP TABLE speaker_names")
        conn.execute(
            "CREATE TABLE speaker_names (device_id TEXT NOT NULL, session INTEGER NOT NULL,"
            " part INTEGER NOT NULL, speaker INTEGER NOT NULL, name TEXT NOT NULL DEFAULT '',"
            " is_me INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (device_id, session, part, speaker))")
        conn.execute("INSERT INTO speaker_names VALUES ('xiao-01', 7, 1, 0, 'Игорь', 1)")
        conn.commit()
    db.init_storage()
    with closing(db.connect()) as conn:
        assert db.speaker_names(conn, "xiao-01", 7)[(1, 0)] == {
            "name": "Игорь", "is_me": True, "auto": False}
```

В `server/test_api_ui.py` (`test_speakers_are_saved_as_json`) дополнить ожидания полем `auto`:

```python
    assert body["speakers"][0] == {"part": 1, "speaker": 0, "name": "Игорь", "is_me": True,
                                   "auto": False}
    with closing(env.db.connect()) as conn:
        assert env.db.speaker_names(conn, "xiao-01", 7)[(1, 0)] == {"name": "Игорь",
                                                                    "is_me": True,
                                                                    "auto": False}
```

(Поле `auto` в ответе API появится в задаче 4; до неё первая из этих двух проверок падает — это ожидаемо, её чинит задача 4.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && python -m pytest test_voiceprint.py -q -p no:cacheprovider`
Expected: 5 новых FAIL — `AttributeError: module 'db' has no attribute 'set_auto_names'` / `save_voice_prints`, словари без `auto`.

- [ ] **Step 3: Write minimal implementation**

`server/db.py`, `SCHEMA`: в `CREATE TABLE IF NOT EXISTS speaker_names` добавить столбец и сразу после таблицы — новую:

```sql
-- auto=1 — имя поставил сервер по сходству голоса (voiceprint.py), а не
-- владелец. Такую строку сервер переписывает и удаляет сам; строку владельца
-- (auto=0) — никогда.
CREATE TABLE IF NOT EXISTS speaker_names (
    device_id TEXT    NOT NULL,
    session   INTEGER NOT NULL,
    part      INTEGER NOT NULL,
    speaker   INTEGER NOT NULL,
    name      TEXT    NOT NULL DEFAULT '',
    is_me     INTEGER NOT NULL DEFAULT 0,
    auto      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, session, part, speaker)
);

-- Отпечаток голоса номера говорящего: 192 числа float32 от модели CAM++.
-- Биометрия: удаляется вместе с сессией (SESSION_TABLES).
CREATE TABLE IF NOT EXISTS voice_prints (
    device_id TEXT    NOT NULL,
    session   INTEGER NOT NULL,
    part      INTEGER NOT NULL,
    speaker   INTEGER NOT NULL,
    seconds   REAL    NOT NULL,
    vec       BLOB    NOT NULL,
    PRIMARY KEY (device_id, session, part, speaker)
);
```

(Существующие комментарии над `speaker_names` оставить, новый абзац про `auto` — перед `CREATE TABLE`.)

`init_storage`, сразу после блока пересборки `speaker_names` и перед `conn.commit()`:

```python
        # Имена, которые были до узнавания по голосу, поставил владелец.
        speaker_columns = {row["name"]
                           for row in conn.execute("PRAGMA table_info(speaker_names)")}
        if "auto" not in speaker_columns:
            conn.execute("ALTER TABLE speaker_names ADD COLUMN auto INTEGER NOT NULL DEFAULT 0")
```

`speaker_names`:

```python
    rows = conn.execute(
        "SELECT part, speaker, name, is_me, auto FROM speaker_names"
        " WHERE device_id=? AND session=?",
        (device_id, session),
    ).fetchall()
    return {(r["part"], r["speaker"]): {"name": r["name"], "is_me": bool(r["is_me"]),
                                        "auto": bool(r["auto"])}
            for r in rows}
```

`SESSION_TABLES` — добавить `"voice_prints"`:

```python
SESSION_TABLES = ("transcripts", "summaries", "speaker_names", "voice_prints", "tasks",
                  "question_states", "marks", "recording_days", "transcript_fts", "parts")
```

`set_speaker` — строка владельца всегда `auto=0`, сброс саммари вынести в общую функцию:

```python
def _summary_stale(conn, device_id, session):
    """Имя попадает в транскрипт, который читает модель: исполнитель задачи
    стоит там именем, а не номером. Значит, смена имени делает саммари
    устаревшим ровно так же, как поздняя часть. parts_covered=0 возвращает
    сессию в очередь, next_try_at снимает паузу после прошлых неудач."""
    conn.execute(
        "UPDATE summaries SET parts_covered=0, next_try_at=datetime('now')"
        " WHERE device_id=? AND session=?",
        (device_id, session),
    )


def set_speaker(conn, device_id, session, part, speaker, name, is_me):
    conn.execute(
        "INSERT INTO speaker_names (device_id, session, part, speaker, name, is_me, auto)"
        " VALUES (?,?,?,?,?,?,0)"
        " ON CONFLICT(device_id, session, part, speaker) DO UPDATE SET"
        " name=excluded.name, is_me=excluded.is_me, auto=0",
        (device_id, session, part, speaker, name.strip(), 1 if is_me else 0),
    )
    _summary_stale(conn, device_id, session)
    conn.commit()


def save_voice_prints(conn, key, prints):
    """Отпечатки части заменяются целиком: после повторной расшифровки номера
    говорящих другие, и подписи по голосу этой части уходят вместе со старыми
    отпечатками. Имена владельца остаются. prints: {номер: (vec, секунд)}."""
    device_id, session, part = key
    if not session_exists(conn, device_id, session):
        # Сессию удалили, пока часть расшифровывалась: биометрию не сохраняем.
        return
    conn.execute("DELETE FROM voice_prints WHERE device_id=? AND session=? AND part=?", key)
    conn.execute("DELETE FROM speaker_names"
                 " WHERE device_id=? AND session=? AND part=? AND auto=1", key)
    conn.executemany(
        "INSERT INTO voice_prints (device_id, session, part, speaker, seconds, vec)"
        " VALUES (?,?,?,?,?,?)",
        [(device_id, session, part, speaker, round(float(seconds), 1), blob)
         for speaker, (blob, seconds) in prints.items()],
    )
    conn.commit()


def voice_prints(conn):
    """Все отпечатки и то, как владелец назвал их номера.

    owner_name — имя от владельца ('' — стёр, NULL — не называл или имя
    поставил сервер). has_owner_row — владелец трогал номер, в том числе стёр
    имя: такой номер сервер не подписывает."""
    return conn.execute(
        "SELECT v.device_id, v.session, v.part, v.speaker, v.vec,"
        " CASE WHEN s.auto = 0 THEN TRIM(s.name) END AS owner_name,"
        " COALESCE(s.auto = 0, 0) AS has_owner_row"
        " FROM voice_prints v LEFT JOIN speaker_names s"
        "   ON s.device_id = v.device_id AND s.session = v.session"
        "  AND s.part = v.part AND s.speaker = v.speaker"
        " ORDER BY v.device_id, v.session, v.part, v.speaker"
    ).fetchall()


def set_auto_names(conn, device_id, session, names):
    """Имена по голосу. names: {(часть, номер): имя или None — снять подпись}.
    Строки владельца не трогаются. Возвращает число изменённых строк; если
    что-то поменялось, саммари сессии уходит на пересборку."""
    changed = 0
    for (part, speaker), name in names.items():
        key = (device_id, session, part, speaker)
        if name:
            cursor = conn.execute(
                "INSERT INTO speaker_names (device_id, session, part, speaker, name, is_me, auto)"
                " VALUES (?,?,?,?,?,0,1)"
                " ON CONFLICT(device_id, session, part, speaker) DO UPDATE SET"
                " name=excluded.name"
                " WHERE speaker_names.auto = 1 AND speaker_names.name != excluded.name",
                (*key, name),
            )
        else:
            cursor = conn.execute(
                "DELETE FROM speaker_names"
                " WHERE device_id=? AND session=? AND part=? AND speaker=? AND auto=1", key)
        changed += cursor.rowcount
    if changed:
        _summary_stale(conn, device_id, session)
    conn.commit()
    return changed
```

Новые функции поставить в раздел `# --- говорящие ---` после `speaker_names`/`session_speakers`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && python -m pytest -q -p no:cacheprovider`
Expected: все проверки `test_voiceprint.py` проходят; падает только `test_speakers_are_saved_as_json` на поле `auto` в ответе API (его чинит задача 4). Если `set_auto_names` второй раз возвращает не 0 — значит `rowcount` у ничего не изменившего UPSERT в этой версии SQLite не ноль: заменить проверку на явное `SELECT name, auto` перед записью и считать изменения по сравнению.

- [ ] **Step 5: Commit**

```bash
git add server/db.py server/test_voiceprint.py server/test_pipeline.py server/test_api_ui.py
git commit -m "feat(server): store voice prints and voice-matched speaker names"
```

---

### Task 3: Отпечатки после расшифровки и подпись сессии

**Files:**
- Modify: `server/voiceprint.py` (дописать в конец)
- Modify: `server/worker.py:18-21` (импорт), `server/worker.py:62-69` (после `save_transcript`)
- Test: `server/test_voiceprint.py`

**Interfaces:**
- Consumes: всё из задач 1–2; `transcribe.FFMPEG`, `transcribe.LOUDNORM_FILTER`.
- Produces:
  - `voiceprint.extractor() -> sherpa_onnx.SpeakerEmbeddingExtractor | None`
  - `voiceprint.embed_part(audio_path, utterances) -> dict[int, tuple[list[float], float]]`
  - `voiceprint.refresh_session(conn, device_id, session) -> int`
  - `voiceprint.remember_part(conn, key, audio_path, utterances) -> int`
  - `voiceprint.backfill(conn) -> int` и запуск `python voiceprint.py --backfill`

- [ ] **Step 1: Write the failing tests**

В конец `server/test_voiceprint.py`:

```python
TWO_VOICES = {1: [(0.5, 0, "привет, это Игорь"), (4.0, 1, "добрый день")],
              2: [(0.5, 0, "продолжим")]}


def voices(monkeypatch, table):
    """Подмена модели: отпечаток берётся из таблицы {(сессия, часть): {номер: вектор}}."""
    def embed(path, utterances):
        name = Path(path).name                       # xiao-01_S0007_P01.wav
        key = (int(name.split("_S")[1][:4]), int(name.split("_P")[1][:2]))
        return {speaker: (vp.normalized(vector), 9.0)
                for speaker, vector in table.get(key, {}).items()}
    monkeypatch.setattr(vp, "embed_part", embed)


def test_voice_named_once_is_recognised_in_the_next_recording(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    voices(monkeypatch, {(7, 1): {0: [1, 0, 0], 1: [0, 1, 0]},
                         (8, 1): {0: [0, 0, 1], 1: [0.95, 0.1, 0]}})
    upload(env, session=7)
    run_all(env)
    with closing(env.db.connect()) as conn:
        env.db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
    upload(env, session=8)
    run_all(env)
    with closing(env.db.connect()) as conn:
        names = env.db.speaker_names(conn, "xiao-01", 8)
        timeline, _ = env.db.session_timeline(conn, "xiao-01", 8)
    assert names == {(1, 1): {"name": "Игорь", "is_me": False, "auto": True}}
    assert {u["speaker"]: u["label"] for u in timeline} == {0: "Говорящий 1", 1: "Игорь"}


def test_voice_failure_leaves_the_transcript_in_place(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)

    def broken(path, utterances):
        raise RuntimeError("модель упала")
    monkeypatch.setattr(vp, "embed_part", broken)
    upload(env)
    run_all(env)
    with closing(env.db.connect()) as conn:
        status = conn.execute("SELECT status FROM transcripts WHERE session=7").fetchone()[0]
    assert status == "done"


def test_without_model_parts_get_no_prints(env, monkeypatch):
    # 9 секунд речи одного номера: отпечаток сняли бы, будь модель на месте.
    long = {1: [(0.5, 0, "раз"), (4.0, 0, "два"), (7.5, 0, "три")]}
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, long))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    assert vp.extractor() is None
    with closing(env.db.connect()) as conn:
        assert env.db.voice_prints(conn) == []


def test_backfill_prints_old_parts_and_names_known_voices(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env, session=7)
    upload(env, session=8)
    run_all(env)                                     # модели ещё нет: отпечатков нет
    with closing(env.db.connect()) as conn:
        env.db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
    voices(monkeypatch, {(7, 1): {0: [1, 0, 0], 1: [0, 1, 0]},
                         (8, 1): {0: [0, 0, 1], 1: [0.95, 0.1, 0]}})
    with closing(env.db.connect()) as conn:
        assert vp.backfill(conn) == 2
        assert vp.backfill(conn) == 0                # повтор ничего не делает
        assert env.db.speaker_names(conn, "xiao-01", 8)[(1, 1)]["name"] == "Игорь"
```

(`deepgram_reply` в `test_pipeline.py` строит реплики длиной 3 с (`end = start + 3.0`), поэтому в `test_without_model_parts_get_no_prints` у номера 0 ровно 9 с ≥ `MIN_SEC`: `embed_part` доходит до `extractor()`, а не выходит раньше. В `TWO_VOICES` у каждого номера по 3 с — без подмены `embed_part` отпечатков нет, и модель не нужна.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && python -m pytest test_voiceprint.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module 'voiceprint' has no attribute 'embed_part'` (monkeypatch на несуществующий атрибут), `extractor`, `backfill`.

- [ ] **Step 3: Write minimal implementation**

`server/voiceprint.py` — дописать в импорты:

```python
import json
import subprocess

from transcribe import FFMPEG, LOUDNORM_FILTER
```

и в конец модуля:

```python
_extractor = None
_extractor_failed = False


def extractor():
    """Модель грузится один раз на процесс (~90 МБ памяти). None — узнавание
    выключено; причина пишется в лог один раз. Новую модель подхватывает
    только перезапуск воркера."""
    global _extractor, _extractor_failed
    if _extractor is None and not _extractor_failed:
        try:
            if not ENABLED:
                raise RuntimeError("RECORDER_VOICE=0")
            if not MODEL.is_file():
                raise RuntimeError(f"нет файла модели {MODEL}")
            import sherpa_onnx
            _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
                sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL), num_threads=2))
        except Exception as error:   # нет библиотеки, битый файл модели
            _extractor_failed = True
            log.warning("Узнавание по голосу выключено: %s", error)
    return _extractor


def samples(path):
    """Звук части: float32 от -1 до 1, 16 кГц моно, с той же громкостью, что
    слышит расшифровка, — на ней порог и замерен. None — ffmpeg не справился.
    numpy, а не список: десять минут списком чисел — около 300 МБ памяти."""
    import numpy as np
    try:
        done = subprocess.run(
            [FFMPEG, "-nostdin", "-loglevel", "error", "-i", str(path),
             "-af", LOUDNORM_FILTER, "-ar", str(RATE), "-ac", "1", "-f", "s16le", "pipe:1"],
            capture_output=True, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0 or not done.stdout:
        return None
    pcm = np.frombuffer(done.stdout[: len(done.stdout) // 2 * 2], dtype="<i2")
    return pcm.astype(np.float32) / 32768


def embed_part(audio_path, utterances):
    """{номер: (отпечаток, секунд речи)} для номеров части с достаточной речью.
    {} — узнавание выключено или звук не прочитался: номера останутся без имён."""
    import numpy as np
    spans = label_spans(utterances)
    model = extractor() if spans else None
    if model is None:
        return {}
    audio = samples(audio_path)
    if audio is None:
        log.warning("Отпечатки голоса не сняты: ffmpeg не прочитал %s", audio_path)
        return {}
    prints = {}
    for speaker, found in spans.items():
        speech = np.concatenate([audio[int(start * RATE):int(end * RATE)]
                                 for start, end in found])
        stream = model.create_stream()
        stream.accept_waveform(RATE, speech)
        stream.input_finished()
        prints[speaker] = (normalized(model.compute(stream)), len(speech) / RATE)
    return prints


def refresh_session(conn, device_id, session):
    """Подписать номера сессии, которых владелец не касался. Профили — из всех
    номеров, названных владельцем, во всём архиве. Возвращает число изменений.

    ponytail: при сохранении имён пересчитывается только своя сессия; старые
    сессии подписывает `--backfill`. Пересчёт всего архива на каждое имя
    отправил бы на пересборку все саммари разом."""
    rows = db.voice_prints(conn)
    named = {}
    for row in rows:
        if row["owner_name"]:
            named.setdefault(row["owner_name"], []).append(unpack(row["vec"]))
    profiles = {name: centroid(vectors) for name, vectors in named.items()}
    names = {(row["part"], row["speaker"]): best_name(unpack(row["vec"]), profiles)
             for row in rows
             if (row["device_id"], row["session"]) == (device_id, session)
             and not row["has_owner_row"]}
    return db.set_auto_names(conn, device_id, session, names)


def remember_part(conn, key, audio_path, utterances):
    """После расшифровки части: снять отпечатки и подписать знакомые голоса."""
    prints = embed_part(audio_path, utterances)
    db.save_voice_prints(conn, key, {speaker: (pack(vector), seconds)
                                     for speaker, (vector, seconds) in prints.items()})
    return refresh_session(conn, key[0], key[1])


def backfill(conn):
    """Отпечатки для частей, расшифрованных до появления модели, и подписи во
    всех сессиях. Один раз после установки: python voiceprint.py --backfill.
    Возвращает число частей, у которых снимались отпечатки."""
    rows = conn.execute(
        "SELECT t.device_id, t.session, t.part, t.utterances, p.audio_path"
        " FROM transcripts t JOIN parts p"
        "   ON p.device_id = t.device_id AND p.session = t.session AND p.part = t.part"
        " WHERE t.status = 'done' AND NOT EXISTS (SELECT 1 FROM voice_prints v"
        "   WHERE v.device_id = t.device_id AND v.session = t.session AND v.part = t.part)"
        " ORDER BY t.device_id, t.session, t.part"
    ).fetchall()
    # ponytail: часть, где ни у кого не набралось 8 с речи, отпечатков не
    # получает и будет пересчитана при следующем --backfill — для разовой
    # команды это дешевле отдельной отметки «проверено».
    for row in rows:
        key = (row["device_id"], row["session"], row["part"])
        prints = embed_part(row["audio_path"], json.loads(row["utterances"] or "[]"))
        db.save_voice_prints(conn, key, {speaker: (pack(vector), seconds)
                                         for speaker, (vector, seconds) in prints.items()})
        log.info("Отпечатки %s сессия %04d часть %02d: %d", *key, len(prints))
    for device_id, session in conn.execute(
            "SELECT DISTINCT device_id, session FROM voice_prints").fetchall():
        refresh_session(conn, device_id, session)
    return len(rows)


if __name__ == "__main__":
    import argparse
    from contextlib import closing

    parser = argparse.ArgumentParser(description="Отпечатки голоса")
    parser.add_argument("--backfill", action="store_true",
                        help="снять отпечатки у старых частей и подписать все сессии")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.backfill:
        db.init_storage()
        with closing(db.connect()) as conn:
            print(f"частей обработано: {backfill(conn)}")
```

Внимание к `test_backfill_...`: при `backfill` с подменой `embed_part` вторая сессия печатается, а номер 0 сессии 8 (`[0, 0, 1]`) ни на кого не похож — остаётся без имени; повторный `backfill` возвращает 0, потому что обе части уже с отпечатками.

`server/worker.py` — импорт рядом с остальными:

```python
import archive
import db
import stt
import summarize
import voiceprint
```

и в `run_transcription` после `log.info("Готово: ...")`, перед `return True`:

```python
    try:
        changed = voiceprint.remember_part(conn, key, row["audio_path"], result.utterances)
        if changed:
            log.info("Имена по голосу: изменено %d", changed)
    except Exception:
        # Расшифровка уже сохранена; без имён по голосу часть остаётся рабочей.
        log.exception("Отпечатки голоса не сняты, %s", key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && python -m pytest -q -p no:cacheprovider`
Expected: всё зелёное, кроме `test_speakers_are_saved_as_json` (поле `auto` в ответе — задача 4).

- [ ] **Step 5: Commit**

```bash
git add server/voiceprint.py server/worker.py server/test_voiceprint.py
git commit -m "feat(server): voice prints after transcription name known speakers"
```

---

### Task 4: Сохранение имён владельцем и пометка «по голосу»

**Files:**
- Modify: `server/api_ui.py:13-14` (импорт), `server/api_ui.py:135-138` (`speakers` в ответе), `server/api_ui.py:164-172` (`save_speakers`)
- Modify: `web/app/session/page.tsx:49` (тип)
- Modify: `web/components/recorder/speakers-form.tsx`
- Test: `server/test_voiceprint.py`

**Interfaces:**
- Consumes: `db.speaker_names` (с `auto`), `db.set_speaker`, `voiceprint.refresh_session`.
- Produces: в `GET /api/ui/sessions/{device}/{session}` у каждого элемента `speakers` поле `auto: bool`; `POST .../speakers` не превращает нетронутые поля в имена владельца и после сохранения подписывает сессию по голосу.

- [ ] **Step 1: Write the failing tests**

В конец `server/test_voiceprint.py`:

```python
def test_untouched_fields_stay_guesses_and_edits_become_the_owners(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    with closing(env.db.connect()) as conn:
        env.db.set_auto_names(conn, "xiao-01", 7, {(1, 0): "Игорь", (1, 1): "Мария"})
    url = "/api/ui/sessions/xiao-01/7/speakers"
    with TestClient(env.app) as client:
        body = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()
        assert body["speakers"][0] == {"part": 1, "speaker": 0, "name": "Игорь",
                                       "is_me": False, "auto": True}
        # Форма шлёт все поля как есть — это не подтверждение, и пустое поле
        # без имени не запрещает узнавание.
        client.post(url, auth=AUTH, json={"names": {"1:0": "Игорь", "1:1": "Мария",
                                                    "1:2": ""}, "me": None})
        with closing(env.db.connect()) as conn:
            assert env.db.speaker_names(conn, "xiao-01", 7) == {
                (1, 0): {"name": "Игорь", "is_me": False, "auto": True},
                (1, 1): {"name": "Мария", "is_me": False, "auto": True}}
        # Исправленное имя — слово владельца; стёртое — тоже: номер больше не подписываем.
        client.post(url, auth=AUTH, json={"names": {"1:0": "Пётр", "1:1": ""}, "me": None})
    with closing(env.db.connect()) as conn:
        assert env.db.speaker_names(conn, "xiao-01", 7) == {
            (1, 0): {"name": "Пётр", "is_me": False, "auto": False},
            (1, 1): {"name": "", "is_me": False, "auto": False}}


def test_naming_one_label_names_the_same_voice_across_the_session(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    voices(monkeypatch, {(7, 1): {0: [1, 0, 0], 1: [0, 1, 0]}, (7, 2): {0: [0.97, 0.05, 0]}})
    upload(env, part=1)
    upload(env, part=2, marker=2)
    run_all(env)
    with TestClient(env.app) as client:
        client.post("/api/ui/sessions/xiao-01/7/speakers", auth=AUTH,
                    json={"names": {"1:0": "Игорь"}, "me": "1:0"})
        body = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()
    by_key = {(s["part"], s["speaker"]): s for s in body["speakers"]}
    assert by_key[(2, 0)]["name"] == "Игорь" and by_key[(2, 0)]["auto"] is True
    assert by_key[(1, 1)]["name"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && python -m pytest test_voiceprint.py test_api_ui.py -q -p no:cacheprovider`
Expected: FAIL — `KeyError`/несовпадение на `auto` в ответе API; после POST поле `"1:2": ""` создало строку владельца.

- [ ] **Step 3: Write minimal implementation**

`server/api_ui.py` — импорт:

```python
import db
import voiceprint
from auth import owner
```

`speakers` в `session_detail`:

```python
        "speakers": [{"part": part, "speaker": speaker,
                      "name": names.get((part, speaker), {}).get("name", ""),
                      "is_me": bool(names.get((part, speaker), {}).get("is_me")),
                      "auto": bool(names.get((part, speaker), {}).get("auto"))}
                     for part, speaker in speakers],
```

`save_speakers`:

```python
@router.post("/sessions/{device_id}/{session}/speakers")
def save_speakers(device_id: str, session: int, body: Speakers, _=Depends(owner)):
    me = _speaker_key(body.me) if body.me else None
    with closing(db.connect()) as conn:
        shown = db.speaker_names(conn, device_id, session)
        for key, name in body.names.items():
            part, speaker = _speaker_key(key)
            current = shown.get((part, speaker))
            is_me = me == (part, speaker)
            # Форма шлёт все поля. Поле, которое владелец не трогал, — не
            # подтверждение: имя по голосу ушло бы в профиль человека, даже
            # если оно ошибочное, а пустое поле навсегда запретило бы узнавать
            # этот номер.
            if ((current is None or current["auto"]) and not is_me
                    and name[:60].strip() == (current["name"] if current else "")):
                continue
            db.set_speaker(conn, device_id, session, part, speaker, name[:60], is_me)
        # Новое имя владельца — новый образец голоса: подписываем остальные
        # номера сессии сразу, не дожидаясь следующей записи.
        voiceprint.refresh_session(conn, device_id, session)
    return {"ok": True}
```

`web/app/session/page.tsx:49`:

```ts
  speakers: { part: number; speaker: number; name: string; is_me: boolean; auto: boolean }[];
```

`web/components/recorder/speakers-form.tsx` — тип:

```ts
type Speaker = { part: number; speaker: number; name: string; is_me: boolean; auto: boolean };
```

в строке после `<input ... />` и перед `<MeSwitch ... />`:

```tsx
              {speaker.auto && name === speaker.name && (
                <span className="shrink-0 text-micro text-text-tertiary"
                      title="Имя поставлено по сходству голоса с теми, кого вы уже называли. Неверно — исправьте или сотрите.">
                  по голосу
                </span>
              )}
```

и подпись под кнопкой — вторая фраза, когда есть хоть одно имя по голосу:

```tsx
        <span className="text-note text-text-secondary">
          {manyParts
            ? "Номера — метки диаризации внутри своей части, между частями не совпадают."
            : "Номера — метки диаризации, транскрипт не меняется."}
          {speakers.some((s) => s.auto) && " «По голосу» — догадка: сохраните исправление, и она станет вашим именем."}
        </span>
```

- [ ] **Step 4: Run tests and checks to verify they pass**

Run: `cd server && python -m pytest -q -p no:cacheprovider`
Expected: всё зелёное, включая `test_speakers_are_saved_as_json`.

Run: `cd web && npm run typecheck && npm run build`
Expected: без ошибок.

Затем посмотреть живьём (правило владельца — интерфейс показывать запущенным). Поднять локальный сервер с демо-данными по разделу запуска в `README.md` (`python tools/seed_demo.py`, сборка `web`, `uvicorn server.app:app --port 8099`, вход `owner` / `demo`). Поставить одной демо-сессии имя по голосу:

```bash
RECORDER_DATA=demo-data python -c "import sys; sys.path.insert(0, 'server'); import db; c = db.connect(); s = c.execute('SELECT session FROM transcripts WHERE status=\'done\' LIMIT 1').fetchone()[0]; print(s, db.set_auto_names(c, 'xiao-01', s, {(1, 0): 'Игорь'}))"
```

Открыть эту сессию и снять скриншот блока «Говорящие» на ширине телефона (~400 px) и на десктопе: пометка «по голосу» стоит в строке «Игорь», не переносит строку и не наезжает на переключатель «это я»; подпись под кнопкой с фразой про догадку видна. Скриншоты показать владельцу.

- [ ] **Step 5: Commit**

```bash
git add server/api_ui.py server/test_voiceprint.py web/app/session/page.tsx web/components/recorder/speakers-form.tsx
git commit -m "feat: owner edits confirm voice-matched names, guesses are marked"
```

---

### Task 5: Установка на сервер и проверка на 0016

**Files:**
- Modify: `server/requirements.txt`
- Modify: `server/README_RU.md` (раздел про узнавание по голосу)

**Interfaces:**
- Consumes: всё выше; `tools/deploy.sh`, `tools/remote_tests.sh`.
- Produces: работающее узнавание на живом сервере, замер на 0016.

- [ ] **Step 1: Зависимость и описание**

`server/requirements.txt` — в конец:

```
# Узнавание людей по голосу (voiceprint.py): отпечатки модели CAM++ без
# PyTorch. Без них сервер работает, номера говорящих остаются без имён.
# Файл модели ставится отдельно: см. README_RU.md, «Узнавание по голосу».
sherpa-onnx>=1.13
numpy
```

`server/README_RU.md` — раздел «Узнавание по голосу»: что делает (абзац из docstring `voiceprint.py`), команда установки модели из шага 2, ручки `RECORDER_VOICE`, `RECORDER_VOICE_MODEL`, `RECORDER_VOICE_THRESHOLD`, `RECORDER_VOICE_MARGIN`, `RECORDER_VOICE_MIN_SEC` с значениями по умолчанию, команда `--backfill` из шага 4, откат (`RECORDER_VOICE=0` и перезапуск: имена по голосу остаются в базе, новые не ставятся; убрать их — `DELETE FROM speaker_names WHERE auto=1`).

Commit:

```bash
git add server/requirements.txt server/README_RU.md
git commit -m "docs(server): voice recognition setup and knobs"
```

- [ ] **Step 2: Библиотека и модель на VPS (пользователь службы — zubr)**

```bash
ssh config1 'pip install --user "sherpa-onnx>=1.13" && python3 -c "import sherpa_onnx; print(sherpa_onnx.__version__)"'
ssh config1 'mkdir -p /var/lib/recorder/models && curl -fsSL -o /var/lib/recorder/models/campplus.onnx https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx && echo "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2  /var/lib/recorder/models/campplus.onnx" | sha256sum -c'
```

Expected: версия `1.13.x`; `campplus.onnx: OK`.

- [ ] **Step 3: Проверки на VPS и выкладка**

```bash
bash tools/remote_tests.sh -q -p no:cacheprovider
bash tools/deploy.sh
ssh config1 'sudo systemctl restart recorder-api recorder-worker && systemctl is-active recorder-api recorder-worker'
```

Expected: все проверки зелёные; обе службы `active`. Перед выкладкой убедиться `git status`, что в `server/` нет чужих несохранённых изменений: `deploy.sh` копирует папку целиком.

- [ ] **Step 4: Отпечатки для архива**

```bash
ssh config1 'cd /opt/recorder/server && set -a && . /etc/recorder/recorder.env && set +a && python3 voiceprint.py --backfill'
ssh config1 'sqlite3 /var/lib/recorder/recorder.db "SELECT session, COUNT(*) FROM voice_prints GROUP BY session; SELECT COUNT(*) FROM speaker_names WHERE auto=1;"'
```

Expected: строка «частей обработано: N», где N — число расшифрованных частей; у 0016 около 53 отпечатков. Имён по голосу пока мало: на 7 октября владелец назвал только двоих в сессии 1 (запись с телефона, другой микрофон).

- [ ] **Step 5: Проверка с владельцем на 0016**

Попросить владельца (через `os-step-by-step`): открыть на сайте запись 0016, в блоке «Говорящие» назвать по одному номеру каждого из четырёх основных голосов (нарезка `golos_1..4.flac` подсказывает, какие номера чьи) и нажать «Сохранить».

Затем замерить:

```bash
ssh config1 'sqlite3 /var/lib/recorder/recorder.db "SELECT auto, COUNT(*) FROM speaker_names WHERE session=16 GROUP BY auto;"'
```

Expected: 4 строки владельца; имён по голосу — порядка 40 из оставшихся 49 (замер: 83% своих номеров при пороге 0,6). Владелец открывает транскрипт 0016 и проверяет несколько реплик с пометкой «по голосу» на слух. Найденные ошибки — повод подвинуть `RECORDER_VOICE_THRESHOLD`/`RECORDER_VOICE_MARGIN` в `/etc/recorder/recorder.env`, а не код.

- [ ] **Step 6: Уборка**

```bash
ssh config1 'rm -rf /tmp/spk'
```
