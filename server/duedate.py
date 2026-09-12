"""Срок, названный вслух, в дату. Считается от дня разговора.

«Пятница», сказанная в среду и в субботу, — разные даты, поэтому разбор идёт
относительно base, а не относительно сегодня. Что не разобрали — None: пункт
уходит в спорные места. Выдуманный срок хуже отсутствующего.

День разговора пока берётся из времени выгрузки части: часы диктофона не
синхронизированы. После синхронизации сменится источник даты, не этот разбор.
"""
import re
from datetime import date, timedelta

import textnorm

WEEKDAYS = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3,
            "пятниц": 4, "суббот": 5, "воскресен": 6}

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5,
          "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10,
          "ноябр": 11, "декабр": 12}
# Длинный префикс проверяется первым, иначе «марта» совпадёт с «ма…».
MONTHS_BY_LENGTH = sorted(MONTHS.items(), key=lambda kv: -len(kv[0]))

# Числительные словами: в речи «через две недели» звучит чаще, чем «через 2».
NUMBERS = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
           "пять": 5, "шесть": 6, "семь": 7, "десять": 10, "четырнадцать": 14}

DATE_RE = re.compile(r"(\d{1,2})[.](\d{1,2})(?:[.](\d{2,4}))?")


def _month_of(token):
    for prefix, number in MONTHS_BY_LENGTH:
        if token.startswith(prefix):
            return number
    return None


def _weekday_of(token):
    for prefix, number in WEEKDAYS.items():
        if token.startswith(prefix):
            return number
    return None


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:      # 31 февраля и прочая оговорка
        return None


def _end_of_month(day):
    first_next = date(day.year + day.month // 12, day.month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def _count(tokens):
    """Число из первых токенов: цифрами или словом. None, если числа нет."""
    for token in tokens[:2]:
        if token.isdigit():
            return int(token)
        if token in NUMBERS:
            return NUMBERS[token]
    return None


def parse_due(phrase, base):
    """Дата срока или None, если фраза неоднозначна.

    base — день разговора (datetime.date).
    """
    if not phrase or not phrase.strip():
        return None
    words = textnorm.tokens(phrase.lower().replace("ё", "е"))
    if not words:
        return None

    found = DATE_RE.search(phrase)
    if found:
        day, month, year = int(found.group(1)), int(found.group(2)), found.group(3)
        if year:
            year = int(year)
            return _safe_date(year + 2000 if year < 100 else year, month, day)
        exact = _safe_date(base.year, month, day)
        if exact and exact < base:
            exact = _safe_date(base.year + 1, month, day)
        return exact

    if "послезавтра" in words:
        return base + timedelta(days=2)
    if "завтра" in words:
        return base + timedelta(days=1)
    if "сегодня" in words:
        return base

    if "через" in words:
        tail = words[words.index("через") + 1:]
        number = _count(tail)
        if any(t.startswith("недел") for t in tail):
            return base + timedelta(days=7 * (number or 1))
        if any(t.startswith("дн") or t == "день" for t in tail):
            return base + timedelta(days=number or 1)
        return None

    if any(t.startswith("конц") or t == "конец" for t in words):
        if any(t.startswith("месяц") for t in words):
            return _end_of_month(base)
        # «до конца недели», «до конца квартала» однозначной даты не имеют.
        return None

    month = next((m for m in (_month_of(w) for w in words) if m), None)
    if month:
        days = [int(w) for w in words if w.isdigit() and 1 <= int(w) <= 31]
        if not days:
            return None        # «в сентябре» — не дата
        picked = _safe_date(base.year, month, days[0])
        if picked and picked < base:
            picked = _safe_date(base.year + 1, month, days[0])
        return picked

    for word in words:
        weekday = _weekday_of(word)
        if weekday is not None:
            # Тот же день недели считаем сегодняшним: «сделаю в пятницу»,
            # сказанное в пятницу, чаще про сегодня, чем про плюс неделю.
            return base + timedelta(days=(weekday - base.weekday()) % 7)

    return None
