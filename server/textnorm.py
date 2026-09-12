"""Нормализация русского текста для поиска.

FTS5 умеет только английский стемминг, поэтому леммы считаются здесь и кладутся
в отдельную колонку. Без pymorphy3 модуль не падает, а работает на простых
токенах: поиск остаётся, просто хуже сводит формы слова.
"""
import re

TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁ]+")

try:
    import pymorphy3

    _morph = pymorphy3.MorphAnalyzer()
except Exception:  # библиотека или словарь не установлены
    _morph = None

_cache = {}


def lemma(word):
    lowered = word.lower().replace("ё", "е")
    if _morph is None:
        return lowered
    hit = _cache.get(lowered)
    if hit is None:
        hit = _morph.parse(lowered)[0].normal_form.replace("ё", "е")
        # словарь ограничен: разбор одного и того же слова повторяется тысячи раз
        if len(_cache) < 50000:
            _cache[lowered] = hit
    return hit


def tokens(text):
    return TOKEN_RE.findall(text or "")


def normalize(text):
    """Слова через один пробел, без регистра и знаков. Не зависит от pymorphy3:
    на ней строятся устойчивые идентификаторы, они не должны меняться от того,
    установлена морфология или нет."""
    return " ".join(t.lower().replace("ё", "е") for t in tokens(text))


def lemmas(text):
    """Строка лемм через пробел, для колонки FTS."""
    return " ".join(lemma(t) for t in tokens(text))


def fts_query(text):
    """Пользовательский запрос в синтаксис FTS5: леммы, последнее слово по префиксу.

    Кавычки убирают спецсимволы FTS, иначе «смета:» или «что-то» ломают запрос.
    """
    words = [lemma(t) for t in tokens(text)]
    if not words:
        return ""
    quoted = [f'"{w}"' for w in words[:-1]]
    quoted.append(f'"{words[-1]}"*')
    return " ".join(quoted)


def available():
    return _morph is not None


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
