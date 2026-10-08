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
