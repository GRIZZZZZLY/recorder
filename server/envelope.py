"""Огибающая громкости части записи.

Волна в интерфейсе была декоративной: форма считалась из номера сессии. Это
выглядело как данные и данными не было. Здесь она считается из самого файла
один раз — при приёме части — и дальше только читается.

Пик, а не RMS: речь на пике видна лучше, а `max`/`min` по срезу `array`
выполняются в C, поэтому десятиминутная часть считается за десятки
миллисекунд. Sum of squares пришлось бы гонять питоновским циклом по девяти
миллионам сэмплов.
"""
import array
import json
import sys

# db импортируется внутри функции: db зовёт resample отсюда, и модульный
# импорт в обе стороны замкнулся бы кольцом.

# Одно значение на полсекунды звука: 30-минутная часть — 3600 чисел, около
# 12 КБ в базе. Было две секунды: запись в полминуты давала 17 чисел, и в
# плеере они растягивались в широкие прямоугольники. Лента дня и список
# всё равно сжимают до своей ширины (db.session_envelopes, resample).
BUCKET_SECONDS = 0.5
SAMPLES_PER_BUCKET = int(16000 * BUCKET_SECONDS)
# Тишина всё равно рисуется волоском: пустая полоса выглядела бы как дыра.
FLOOR = 2


def from_wav(path):
    """[0..100] по полсекунды на значение. None, если файл не читается.

    Нормировка внутри своей части: важно, где в разговоре говорили громче, а
    не абсолютный уровень микрофона, который всё равно неизвестен.
    """
    import db

    try:
        with open(path, "rb") as handle:
            handle.seek(db.WAV_HEADER_BYTES)
            peaks = []
            while True:
                chunk = handle.read(SAMPLES_PER_BUCKET * 2)
                if not chunk:
                    break
                # Нечётный хвост байта — обрезанная запись; последний сэмпл
                # отбрасываем, а не читаем мусор.
                samples = array.array("h")
                samples.frombytes(chunk[: len(chunk) - len(chunk) % 2])
                if sys.byteorder == "big":       # WAV всегда little-endian
                    samples.byteswap()
                if not samples:
                    break
                peaks.append(max(max(samples), -min(samples)))
    except OSError:
        return None

    if not peaks:
        return []
    top = max(peaks)
    if top <= 0:
        return [FLOOR] * len(peaks)
    return [max(FLOOR, round(peak * 100 / top)) for peak in peaks]


def store(conn, device_id, session, part, path):
    """Посчитать и записать. Ошибка чтения не рвёт приёмку: аудио уже цело."""
    values = from_wav(path)
    if values is None:
        return None
    conn.execute(
        "UPDATE parts SET envelope=? WHERE device_id=? AND session=? AND part=?",
        (json.dumps(values), device_id, session, part),
    )
    return values


def resample(values, buckets):
    """Сжать огибающую до нужного числа столбиков, беря пик в каждом окне."""
    if not values or buckets <= 0:
        return []
    if len(values) <= buckets:
        return list(values)
    step = len(values) / buckets
    return [max(values[int(i * step):max(int((i + 1) * step), int(i * step) + 1)])
            for i in range(buckets)]
