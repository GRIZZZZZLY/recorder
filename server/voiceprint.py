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
import json
import logging
import math
import os
import subprocess
from pathlib import Path

import db
from transcribe import FFMPEG, LOUDNORM_FILTER

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
# Память модели растёт с длиной звука и после вызова не отдаётся: замер
# 8 октября 2026 — 60 с речи 234 МБ, 300 с — 803 МБ, 600 с — 1,5 ГБ. Минуты
# на отпечаток хватает с запасом.
MAX_SEC = float(os.environ.get("RECORDER_VOICE_MAX_SEC", "60"))
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


_extractor = None
_extractor_failed = False


def extractor():
    """Модель грузится один раз на процесс (~90 МБ памяти, с минутой звука до
    ~230 МБ — см. MAX_SEC). None — узнавание выключено; причина пишется в лог
    один раз. Новую модель подхватывает только перезапуск воркера."""
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
    spans = label_spans(utterances)
    model = extractor() if spans else None
    if model is None:
        return {}
    import numpy as np
    audio = samples(audio_path)
    if audio is None:
        log.warning("Отпечатки голоса не сняты: ffmpeg не прочитал %s", audio_path)
        return {}
    prints = {}
    for speaker, found in spans.items():
        speech = np.concatenate([audio[int(start * RATE):int(end * RATE)]
                                 for start, end in found])[:int(MAX_SEC * RATE)]
        if len(speech) < MIN_SEC * RATE:
            # Время реплик вышло за конец звука. Пустой отпечаток у
            # названного номера обнулил бы весь профиль человека.
            continue
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
    try:
        prints = embed_part(audio_path, utterances)
    except Exception:
        # Отпечатки части всё равно заменяются, пусть и пустыми: после
        # повторной расшифровки номера другие, и старые догадки стояли бы
        # под чужими словами.
        log.exception("Отпечатки голоса не сняты, %s", audio_path)
        prints = {}
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
