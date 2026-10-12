"""Стерео с Recorder RevA не растягивает шкалу времени и волну.

Длительность части сервер считает из размера файла: размер есть у любой части,
включая нерасшифрованную. Пока диктофон был один и писал моно, множитель был
константой 32 000 байт в секунду. У RevA микрофона два, и та же секунда весит
64 000 байт — старая константа удвоила бы длину разговора на ленте дня и число
столбиков в волне, ничего при этом не уронив. Ошибка молчаливая, поэтому и
проверяется отдельным файлом.

Каждая проверка здесь падает на коде до появления parts.bytes_per_second.
"""
from contextlib import closing

import pytest

from test_pipeline import AUTH, env, upload, wav  # noqa: F401

DEVICE = "xiao-01"


def seconds_of(env, session):
    """Длина сессии так, как её видит список сессий и лента дня."""
    with closing(env.db.connect()) as conn:
        rows = {row["session"]: row for row in env.db.session_rows(conn)}
    return rows[session]["seconds"]


def part_row(env, session, part):
    with closing(env.db.connect()) as conn:
        return conn.execute(
            "SELECT * FROM parts WHERE device_id=? AND session=? AND part=?",
            (DEVICE, session, part),
        ).fetchone()


def test_ten_seconds_of_stereo_are_ten_seconds_on_the_timeline(env):
    """Главная проверка файла: со старой константой вышло бы 20 секунд."""
    upload(env, session=1, seconds=10, channels=1)
    upload(env, session=2, seconds=10, channels=2)

    assert seconds_of(env, 1) == pytest.approx(10.0)
    assert seconds_of(env, 2) == pytest.approx(10.0)


def test_part_offsets_use_each_parts_own_format(env):
    """Шкала реплик: части разных форматов в одной сессии не сбивают отсчёт.

    Так выглядит карта после смены платы: старые части моно, новые стерео, а
    реплики обязаны лечь на одну шкалу.
    """
    upload(env, session=5, part=1, seconds=10, channels=2)
    upload(env, session=5, part=2, seconds=10, channels=1)
    upload(env, session=5, part=3, seconds=10, channels=2)

    with closing(env.db.connect()) as conn:
        offsets = env.db.part_offsets(conn, DEVICE, 5)
    assert offsets == pytest.approx({1: 0.0, 2: 10.0, 3: 20.0})
    assert seconds_of(env, 5) == pytest.approx(30.0)


def test_unreadable_header_does_not_invent_a_format(env):
    """Заголовок не разобрался — остаёмся на моно, а не на выдуманном числе.

    Файл принимается: аудио дошло целым, контрольная сумма сошлась, и отказать
    из-за заголовка значило бы потерять запись. Длина считается по-старому.
    """
    body = bytearray(wav(seconds=10))
    body[8:12] = b"XXXX"            # не WAVE: stdlib-разбор откажется
    response = upload(env, session=4, body=bytes(body))

    assert response.status_code == 201
    assert part_row(env, 4, 1)["bytes_per_second"] is None
    assert seconds_of(env, 4) == pytest.approx(10.0)


def test_absurd_format_in_the_header_is_refused(env):
    """Битое число в заголовке исказило бы всю шкалу разговора молча.

    1 Гц моно дало бы 2 байта в секунду: десять секунд превратились бы в двое
    суток. Такое значение не сохраняется, часть считается моно.
    """
    import struct
    body = bytearray(wav(seconds=10))
    struct.pack_into("<I", body, 24, 1)      # частота 1 Гц
    struct.pack_into("<I", body, 28, 2)      # и байт в секунду тоже 2
    upload(env, session=8, body=bytes(body))

    assert part_row(env, 8, 1)["bytes_per_second"] is None
    assert seconds_of(env, 8) == pytest.approx(10.0)


