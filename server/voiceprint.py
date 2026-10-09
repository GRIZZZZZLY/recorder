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
в среднем на 0,90, разные номера одной порции — на 0,32. Владелец прослушал
нарезку четырёх найденных голосов: совпадают. Второй замер, когда владелец
назвал 4 человек (35 номеров — проверочные ответы): при пороге 0,6 нового,
ещё не названного человека подписывали чужим именем в 46% случаев, при 0,7 —
ни разу, а своих узнаётся 91% вместо 94%. Кусок в 3 секунды при 0,7 узнаётся
в 52% случаев и ошибается в 3%; секунда не узнаётся надёжно ни одной из пяти
проверенных моделей.

Без sherpa-onnx или файла модели сервер работает как раньше: номера без имён.
"""
import array
import json
import logging
import math
import os
import subprocess
from collections import Counter
from pathlib import Path

import db
import textnorm
from transcribe import FFMPEG, LOUDNORM_FILTER

log = logging.getLogger("voiceprint")

ENABLED = os.environ.get("RECORDER_VOICE", "1") != "0"
MODEL = Path(os.environ.get("RECORDER_VOICE_MODEL",
                            str(db.DATA_DIR / "models" / "campplus.onnx")))
# Порог и разрыв подобраны на 0016 (см. замеры выше); ручки — чтобы
# подстроить на новых записях без выкладки кода.
THRESHOLD = float(os.environ.get("RECORDER_VOICE_THRESHOLD", "0.7"))
MARGIN = float(os.environ.get("RECORDER_VOICE_MARGIN", "0.1"))
# Группы в списке «Имена и дата» — мягче, чем подпись: строка списка ничего
# не утверждает, а одного человека в двух строках владелец замечает сразу.
GROUP_THRESHOLD = float(os.environ.get("RECORDER_VOICE_GROUP_THRESHOLD", "0.6"))
# Меньше 3 секунд речи — отпечаток случаен; такой номер остаётся без имени.
MIN_SEC = float(os.environ.get("RECORDER_VOICE_MIN_SEC", "3"))
# Память модели растёт с длиной звука и после вызова не отдаётся: замер
# 8 октября 2026 — 60 с речи 234 МБ, 300 с — 803 МБ, 600 с — 1,5 ГБ. Минуты
# на отпечаток хватает с запасом.
MAX_SEC = float(os.environ.get("RECORDER_VOICE_MAX_SEC", "60"))
MIN_UTTERANCE_SEC = 0.5
# Фразы. Gemini иногда кладёт двух людей под один номер, и имя номера стоит на
# чужих фразах. Замер 9 октября 2026 на 0016: 53 из 492 фраз длиннее 2 с
# (11%) голосом явно другого названного человека; владелец прослушал 8 самых
# явных — проверка права во всех. Правило того замера: лучший человек не тот,
# что у номера, сходство с ним от 0,6 и на 0,15 больше, чем с именем номера.
LINE_MIN_SEC = float(os.environ.get("RECORDER_VOICE_LINE_MIN_SEC", "2"))
LINE_THRESHOLD = float(os.environ.get("RECORDER_VOICE_LINE_THRESHOLD", "0.6"))
LINE_MARGIN = float(os.environ.get("RECORDER_VOICE_LINE_MARGIN", "0.15"))
# Подсказка в безымянной строке списка: «похоже на: Гоша». Имя не ставится —
# решает владелец, поэтому порог ниже, чем у подписи. «Голос 11» в 0016 (одна
# фраза 3 с, Гоша по словам владельца): 0,54 к Гоше, 0,33 к следующему.
HINT_THRESHOLD = float(os.environ.get("RECORDER_VOICE_HINT_THRESHOLD", "0.45"))
HINT_MARGIN = float(os.environ.get("RECORDER_VOICE_HINT_MARGIN", "0.15"))
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


def carry_over(old, new):
    """После повторной расшифровки: {старый номер: новый номер того же голоса
    или None}. Порог и разрыв те же, что у узнавания, с одним исключением:
    Gemini делит человека на номера по 5-минутным порциям, и старый номер
    похож на свою порцию (0,96) почти так же, как на соседнюю (0,87). Если два
    ближайших новых номера сами похожи друг на друга (как строки списка, от
    GROUP_THRESHOLD) — это один человек, и разрыв не нужен; если нет — это два разных человека, и имя не переезжает
    ни к одному. Спорный новый номер достаётся самому похожему старому."""
    found = []
    for before, voice in old.items():
        scores = sorted(((similarity(voice, other), after) for after, other in new.items()),
                        reverse=True)
        if not scores or scores[0][0] < THRESHOLD:
            continue
        if (len(scores) > 1 and scores[0][0] - scores[1][0] < MARGIN
                and similarity(new[scores[0][1]], new[scores[1][1]]) < GROUP_THRESHOLD):
            continue
        found.append((scores[0][0], before, scores[0][1]))
    moves, taken = dict.fromkeys(old), set()
    for _, before, after in sorted(found, reverse=True):
        if after not in taken:
            moves[before] = after
            taken.add(after)
    return moves


def line_voice(vector, own, profiles):
    """Чей голос у фразы, если не того, кем подписан её номер; иначе None.
    own — имя номера (None — номер без имени: такие фразы не трогаем)."""
    if own is None or own not in profiles:
        return None
    scores = sorted(((similarity(vector, centre), name) for name, centre in profiles.items()),
                    reverse=True)
    best_score, best = scores[0]
    if best == own or best_score < LINE_THRESHOLD:
        return None
    return best if best_score - similarity(vector, profiles[own]) >= LINE_MARGIN else None


def voice_hint(vector, profiles):
    """(имя, сходство) того, на кого голос заметно похож сильнее остальных,
    или None. Только подсказка: имя ставит владелец."""
    scores = sorted(((similarity(vector, centre), name) for name, centre in profiles.items()),
                    reverse=True)
    if not scores or scores[0][0] < HINT_THRESHOLD:
        return None
    if len(scores) > 1 and scores[0][0] - scores[1][0] < HINT_MARGIN:
        return None
    return scores[0][1], round(scores[0][0], 2)


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
    """Модель грузится один раз на процесс. Замер на живом архиве 8 октября:
    пик процесса 490 МБ вместе с раскрытым звуком части. None — узнавание
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


class VoiceUnavailable(Exception):
    """Отпечатки снять нельзя: узнавание выключено, нет модели или звука."""


def embed_part(audio_path, utterances):
    """{номер: (отпечаток, секунд речи)} для номеров части с достаточной речью;
    {} — ни у кого не набралось речи. VoiceUnavailable — снять нельзя: это не
    то же, что «отпечатков нет», после повторной расшифровки от этого зависит,
    что станет с именами владельца."""
    spans = label_spans(utterances)
    if not spans:
        return {}
    model = extractor()
    if model is None:
        raise VoiceUnavailable("узнавание по голосу выключено или нет модели")
    import numpy as np
    audio = samples(audio_path)
    if audio is None:
        raise VoiceUnavailable(f"ffmpeg не прочитал {audio_path}")
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


def embed_lines(audio_path, utterances):
    """{индекс реплики: (номер, отпечаток)} для реплик длиннее LINE_MIN_SEC.
    {} — снимать нечего, узнавание выключено или звук не прочитался: фразы
    тогда просто подписаны своим номером."""
    wanted = [(idx, u) for idx, u in enumerate(utterances)
              if u.get("spk") is not None
              and float(u.get("e") or 0) - float(u.get("s") or 0) >= LINE_MIN_SEC]
    if not wanted:
        return {}
    model = extractor()
    audio = samples(audio_path) if model is not None else None
    if audio is None:
        return {}
    prints = {}
    for idx, u in wanted:
        speech = audio[int(float(u["s"]) * RATE):int(float(u["e"]) * RATE)][:int(MAX_SEC * RATE)]
        if len(speech) < LINE_MIN_SEC * RATE:
            continue
        stream = model.create_stream()
        stream.accept_waveform(RATE, speech)
        stream.input_finished()
        prints[idx] = (u["spk"], normalized(model.compute(stream)))
    return prints


def _profiles(rows, device_id, session):
    """Профили людей по номерам, названным владельцем: {группа: (имя для
    подписи, {(устройство, сессия, часть, номер): вектор})}. «Игорь» и «игорь»
    — одна группа. Имя для подписи — как владелец пишет его в этой записи, а
    если здесь он его не писал — самое частое написание в архиве: иначе в
    одном транскрипте стояли бы «Мария» и «мария» как два человека."""
    groups = {}
    for row in rows:
        if row["owner_name"]:
            group = textnorm.normalize(row["owner_name"]) or row["owner_name"]
            here, everywhere, members = groups.setdefault(group, (Counter(), Counter(), {}))
            everywhere[row["owner_name"]] += 1
            if (row["device_id"], row["session"]) == (device_id, session):
                here[row["owner_name"]] += 1
            members[(row["device_id"], row["session"], row["part"], row["speaker"])] = \
                unpack(row["vec"])
    return {group: ((here or everywhere).most_common(1)[0][0], members)
            for group, (here, everywhere, members) in groups.items()}


def _refresh_lines(conn, device_id, session):
    """Подписать фразы сессии, чей голос явно другого названного человека.

    Профиль имени номера строится без самого номера: в смешанном номере
    чужой голос перетянул бы профиль на себя, и его фразы не нашлись бы. Если
    других номеров у человека нет, профиль — этот номер."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        groups = _profiles(db.voice_prints(conn, device_id, session), device_id, session)
        full = {group: centroid(list(members.values()))
                for group, (_, members) in groups.items()}
        display = {group: name for group, (name, _) in groups.items()}
        names = db.speaker_names(conn, device_id, session)
        cache, decided = {}, {}
        for row in db.line_prints(conn, device_id, session):
            label = (row["part"], row["speaker"])
            own_name = (names.get(label) or {}).get("name", "").strip()
            own = (textnorm.normalize(own_name) or own_name) if own_name else None
            if label not in cache:
                profiles = {display[g]: c for g, c in full.items()}
                member = (device_id, session) + label
                rest = [v for k, v in groups[own][1].items() if k != member]                     if own in groups and member in groups[own][1] else []
                # Других номеров у человека нет — образец только этот номер:
                # лучше проверять по нему, чем не проверять вовсе.
                if rest:
                    profiles[display[own]] = centroid(rest)
                cache[label] = profiles
            decided[(row["part"], row["idx"])] = line_voice(
                unpack(row["vec"]), display.get(own), cache[label])
        changed = db.set_line_names(conn, device_id, session, decided)
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise


def remember_lines(conn, key, audio_path, utterances, version):
    """Отпечатки фраз части. Уже снятые с этой версии расшифровки не
    переснимаются: второй проход (воркер и --backfill разом) только стёр бы
    имена и пересобрал саммари. Сбой не мешает остальному: фразы останутся
    подписаны своим номером, а номера — по голосу."""
    try:
        if db.has_line_prints(conn, key, version):
            return
        lines = embed_lines(audio_path, utterances)
        db.save_line_prints(conn, key, {idx: (speaker, pack(vector))
                                        for idx, (speaker, vector) in lines.items()}, version)
    except Exception:
        log.exception("Отпечатки фраз не сняты, %s", audio_path)
        if conn.in_transaction:
            conn.rollback()


def refresh_session(conn, device_id, session):
    """Подписать номера сессии, которых владелец не касался. Профили — из всех
    номеров, названных владельцем, во всём архиве. Возвращает число изменений.

    ponytail: при сохранении имён пересчитывается только своя сессия; старые
    сессии подписывает `--backfill`. Пересчёт всего архива на каждое имя
    отправил бы на пересборку все саммари разом.

    Чтение и запись идут под одной блокировкой базы: сайт и воркер могут
    пересчитывать одну сессию одновременно, и без неё более старый результат
    записался бы поверх нового. Пересчёт — миллисекунды, второй просто ждёт."""
    if not ENABLED:
        return 0
    if conn.in_transaction:
        # Чужая открытая транзакция: блокировку не взять, а set_auto_names
        # зафиксировал бы чужие незаконченные записи.
        raise RuntimeError("refresh_session: у соединения открыта транзакция")
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = db.voice_prints(conn, device_id, session)
        # «Игорь» и «игорь» — один человек, а не два почти одинаковых профиля,
        # из-за которых правило разрыва не назвало бы никого (_profiles).
        profiles = {name: centroid(list(members.values()))
                    for name, members in _profiles(rows, device_id, session).values()}
        names = {(row["part"], row["speaker"]): best_name(unpack(row["vec"]), profiles)
                 for row in rows
                 if (row["device_id"], row["session"]) == (device_id, session)
                 and not row["has_owner_row"]}
        changed = db.set_auto_names(conn, device_id, session, names)   # он же и фиксирует
    except Exception:
        conn.rollback()
        raise
    # Фразы — после номеров: имя номера, с которым сравнивается фраза, уже новое.
    return changed + _refresh_lines(conn, device_id, session)


def group_hints(conn, device_id, session, groups):
    """[подсказка или None] для каждой группы номеров, в которой ни у кого нет
    имени: голос группы — среднее её отпечатков в этой записи."""
    if not ENABLED:
        return [None] * len(groups)
    names = db.speaker_names(conn, device_id, session)
    rows = db.voice_prints(conn, device_id, session)
    profiles = {name: centroid(list(members.values()))
                for name, members in _profiles(rows, device_id, session).values()}
    here = {(row["part"], row["speaker"]): unpack(row["vec"]) for row in rows
            if (row["device_id"], row["session"]) == (device_id, session)}
    hints = []
    for group in groups:
        vectors = [here[key] for key in group if key in here]
        named = any((names.get(key) or {}).get("name", "").strip() for key in group)
        hints.append(None if named or not vectors or not profiles
                     else voice_hint(centroid(vectors), profiles))
    return hints


def session_groups(conn, device_id, session, keys):
    """Номера сессии, собранные по голосу, — для списка «Имена и дата».

    Gemini нумерует говорящих заново в каждой 5-минутной порции и дробит
    людей: на 0016 вышло 77 номеров на 4 основных голоса. Показывать владельцу
    77 строк бессмысленно. Возвращает (группы, короткие): группы — списки
    (часть, номер) одного голоса, короткие — номера без отпечатка (меньше 8 с
    речи) и без имени.

    Правила: одно имя (в любом написании) — одна группа, даже если голоса
    непохожи: владелец знает лучше. Разные имена в одну группу не попадают.
    Остальные номера сливаются по среднему сходству голосов с порогом
    GROUP_THRESHOLD — мягче, чем у подписи. Номер без отпечатка, но с именем, идёт в группу этого
    имени. Без отпечатков в сессии (или с выключенным узнаванием) — каждый
    номер сам по себе, как раньше."""
    keys = list(keys)
    names = db.speaker_names(conn, device_id, session)

    def tag(key):
        name = (names.get(key) or {}).get("name", "").strip()
        return (textnorm.normalize(name) or name) if name else None

    rows = [row for row in db.voice_prints(conn, device_id, session)
            if (row["device_id"], row["session"]) == (device_id, session)
            and (row["part"], row["speaker"]) in set(keys)] if ENABLED else []
    if not rows:
        return [[key] for key in keys], []

    printed = [(row["part"], row["speaker"]) for row in rows]
    vectors = [unpack(row["vec"]) for row in rows]
    # Сначала одинаковые имена вместе, остальные — по одному.
    groups, tags, by_tag = [], [], {}
    for index, key in enumerate(printed):
        label = tag(key)
        if label is not None and label in by_tag:
            groups[by_tag[label]].append(index)
            continue
        if label is not None:
            by_tag[label] = len(groups)
        groups.append([index])
        tags.append(label)
    # Среднее сходство между группами; при слиянии суммы складываются, а не
    # пересчитываются: на 80 номерах это доли секунды.
    pair = [[similarity(a, b) for b in vectors] for a in vectors]
    total = {(a, b): sum(pair[i][j] for i in groups[a] for j in groups[b])
             for a in range(len(groups)) for b in range(len(groups)) if a < b}
    alive = set(range(len(groups)))
    while True:
        best, merge = GROUP_THRESHOLD, None
        for (a, b), value in total.items():
            if tags[a] is not None and tags[b] is not None and tags[a] != tags[b]:
                continue
            mean = value / (len(groups[a]) * len(groups[b]))
            if mean >= best:
                best, merge = mean, (a, b)
        if merge is None:
            break
        a, b = merge
        groups[a] += groups[b]
        tags[a] = tags[a] if tags[a] is not None else tags[b]
        alive.discard(b)
        for c in alive - {a}:
            ab = total.pop((min(a, c), max(a, c)), 0.0)
            bc = total.pop((min(b, c), max(b, c)), 0.0)
            total[(min(a, c), max(a, c))] = ab + bc
        total.pop((a, b), None)
    result = {tags[g] if tags[g] is not None else ("voice", g): [printed[i] for i in groups[g]]
              for g in sorted(alive)}
    short = []
    for key in keys:
        if key in set(printed):
            continue
        label = tag(key)
        if label is None:
            short.append(key)
        else:
            result.setdefault(label, []).append(key)
    return list(result.values()), short


def remember_part(conn, key, audio_path, utterances, version=None):
    """После расшифровки части: снять отпечатки и подписать знакомые голоса.
    version — версия расшифровки, с реплик которой снимаем (по умолчанию
    текущая); если пока снимали, часть расшифровали снова, отпечатки сразу
    окажутся устаревшими и будут сняты заново.

    Если у части были отпечатки, она расшифрована заново и номера в ней другие:
    имена владельца, поставленные под старую версию, переезжают за своим
    голосом (carry_over), не найденные по голосу снимаются — под старым
    номером теперь другой человек, и его голос ушёл бы в чужой профиль."""
    if version is None:
        version = db.transcript_version(conn, key)
    try:
        prints = embed_part(audio_path, utterances)
    except Exception as error:
        # Снять нельзя. Догадки части уходят: номера могли смениться. Старые
        # отпечатки остаются, но их версия уже не текущая, и они не участвуют
        # ни в профилях, ни в подписях; имена владельца ждут следующей попытки
        # (--backfill), чтобы переехать по голосу.
        if isinstance(error, VoiceUnavailable):
            log.info("Отпечатки голоса не сняты: %s", error)
        else:
            log.exception("Отпечатки голоса не сняты, %s", audio_path)
        db.drop_auto_names(conn, key)
        return 0
    new = {speaker: vector for speaker, (vector, _) in prints.items()}
    db.replace_part_prints(
        conn, key, {speaker: (pack(vector), seconds)
                    for speaker, (vector, seconds) in prints.items()}, version,
        lambda old: carry_over({speaker: unpack(blob) for speaker, blob in old.items()}, new))
    remember_lines(conn, key, audio_path, utterances, version)
    return refresh_session(conn, key[0], key[1])


def backfill(conn):
    """Отпечатки для частей, где их нет или они сняты с прежней версии
    расшифровки (часть расшифрована заново, а снять не вышло), и подписи во
    всех сессиях. После установки модели и после сбоев:
    python voiceprint.py --backfill. Возвращает число частей, у которых
    снимались отпечатки."""
    rows = conn.execute(
        "SELECT t.device_id, t.session, t.part, t.utterances, t.updated_at, p.audio_path"
        " FROM transcripts t JOIN parts p"
        "   ON p.device_id = t.device_id AND p.session = t.session AND p.part = t.part"
        " WHERE t.status = 'done' AND NOT EXISTS (SELECT 1 FROM voice_prints v"
        "   WHERE v.device_id = t.device_id AND v.session = t.session AND v.part = t.part"
        "     AND v.transcript_at IS t.updated_at)"
        " ORDER BY t.device_id, t.session, t.part"
    ).fetchall()
    # ponytail: часть, где ни у кого не набралось 8 с речи, отпечатков не
    # получает и будет пересчитана при следующем --backfill — для разовой
    # команды это дешевле отдельной отметки «проверено».
    for row in rows:
        key = (row["device_id"], row["session"], row["part"])
        try:
            remember_part(conn, key, row["audio_path"], json.loads(row["utterances"] or "[]"),
                          row["updated_at"])
        except Exception:
            # Одна сломанная часть не должна останавливать остальные.
            log.exception("Отпечатки %s сессия %04d часть %02d не сняты", *key)
            if conn.in_transaction:
                conn.rollback()
    # Номера, которым отпечаток раньше не полагался (минимум речи снизили с 8
    # до 3 секунд), получают его без пересъёма остальных: та же версия
    # расшифровки, имена никуда не переезжают.
    for row in conn.execute(
            "SELECT t.device_id, t.session, t.part, t.utterances, t.updated_at, p.audio_path"
            " FROM transcripts t JOIN parts p"
            "   ON p.device_id = t.device_id AND p.session = t.session AND p.part = t.part"
            " WHERE t.status = 'done' ORDER BY t.device_id, t.session, t.part").fetchall():
        key = (row["device_id"], row["session"], row["part"])
        have = set(db.part_prints(conn, key))
        if not have:
            continue
        utterances = json.loads(row["utterances"] or "[]")
        missing = {speaker for speaker in label_spans(utterances) if speaker not in have}
        if not missing:
            continue
        try:
            prints = embed_part(row["audio_path"],
                                [u for u in utterances if u.get("spk") in missing])
        except Exception:
            log.exception("Отпечатки %s сессия %04d часть %02d не дополнены", *key)
            continue
        db.add_voice_prints(conn, key, {speaker: (pack(vector), seconds)
                                        for speaker, (vector, seconds) in prints.items()},
                            row["updated_at"])
        log.info("Отпечатки %s сессия %04d часть %02d дополнены: %d", *key, len(prints))
    # Отпечатки фраз для частей, где их нет (часть расшифрована до проверки
    # фраз или заново). ponytail: часть без фраз длиннее 2 с снова попадёт
    # сюда при следующем --backfill — для разовой команды это дешевле отметки.
    for row in db.parts_without_line_prints(conn):
        key = (row["device_id"], row["session"], row["part"])
        remember_lines(conn, key, row["audio_path"], json.loads(row["utterances"] or "[]"),
                       row["updated_at"])
    for device_id, session in conn.execute(
            "SELECT DISTINCT device_id, session FROM voice_prints").fetchall():
        try:
            refresh_session(conn, device_id, session)
        except Exception:
            log.exception("Имена по голосу %s сессия %04d не пересчитаны", device_id, session)
            if conn.in_transaction:
                conn.rollback()
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
