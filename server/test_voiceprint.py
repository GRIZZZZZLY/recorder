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


class FakeModel:
    """Модель-заглушка: запоминает, сколько звука ей отдали."""
    def __init__(self):
        self.lengths = []

    def create_stream(self):
        model = self

        class Stream:
            def accept_waveform(self, rate, samples):
                model.lengths.append(len(samples))

            def input_finished(self):
                pass
        return Stream()

    def compute(self, stream):
        return [1.0, 0.0, 0.0]


def fake_model(monkeypatch, audio_seconds):
    import numpy as np
    model = FakeModel()
    monkeypatch.setattr(vp, "extractor", lambda: model)
    monkeypatch.setattr(vp, "samples",
                        lambda path: np.zeros(int(audio_seconds * vp.RATE), np.float32))
    return model


def test_long_monologue_is_cut_to_bound_memory(monkeypatch):
    model = fake_model(monkeypatch, 600)
    prints = vp.embed_part("part.flac", [{"s": 0, "e": 590, "spk": 0, "text": "лекция"}])
    # Память модели растёт с длиной звука: 300 с — 0,8 ГБ. Минуты хватает на отпечаток.
    assert model.lengths == [int(vp.MAX_SEC * vp.RATE)]
    assert prints[0][1] == vp.MAX_SEC


def test_speech_past_the_end_of_the_audio_gives_no_print(monkeypatch):
    # Пустой отпечаток у названного номера обнулил бы профиль человека.
    model = fake_model(monkeypatch, 5)
    assert vp.embed_part("part.flac", [{"s": 10, "e": 30, "spk": 0, "text": "после конца"}]) == {}
    assert model.lengths == []


def test_failed_prints_after_retranscription_wait_for_the_next_try(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack([1.0, 0, 0]), 9.0),
                                         1: (vp.pack([0, 1.0, 0]), 9.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        db.set_auto_names(conn, "xiao-01", 7, {(1, 1): "Мария"})
        # Часть расшифрована заново: у текста новая версия.
        conn.execute("UPDATE transcripts SET updated_at = datetime('now', '+1 minute')")
        conn.commit()

    def broken(path, utterances):
        raise RuntimeError("модель упала")
    monkeypatch.setattr(vp, "embed_part", broken)
    with closing(db.connect()) as conn:
        vp.remember_part(conn, KEY, "part.flac", [])
        # Номера уже другие: догадки ушли, имя владельца ждёт, старые отпечатки
        # не участвуют ни в профилях, ни в подписях.
        assert db.speaker_names(conn, "xiao-01", 7) == {
            (1, 0): {"name": "Игорь", "is_me": False, "auto": False}}
        assert db.voice_prints(conn) == []
    # Следующая попытка с рабочей моделью переносит имя по голосу.
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        2: (vp.normalized([0.97, 0.05, 0]), 9.0)})
    with closing(db.connect()) as conn:
        assert vp.backfill(conn) == 1
        assert db.speaker_names(conn, "xiao-01", 7) == {
            (1, 2): {"name": "Игорь", "is_me": False, "auto": False}}


def test_old_voices_find_their_new_numbers_one_to_one():
    a, b, c = vp.normalized([1, 0, 0]), vp.normalized([0, 1, 0]), vp.normalized([0, 0, 1])
    near_a = vp.normalized([0.95, 0.1, 0])
    # Номера поменялись местами; третий старый голос из новой расшифровки пропал.
    assert vp.carry_over({0: a, 1: b, 2: c}, {0: b, 1: near_a}) == {0: 1, 1: 0, 2: None}
    # Два старых номера одного голоса — новый номер достаётся более похожему.
    assert vp.carry_over({0: a, 5: near_a}, {3: a}) == {0: 3, 5: None}


def test_owner_names_follow_their_voice_after_retranscription(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    db = env.db
    igor, maria, stranger = [1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack(igor), 9.0), 1: (vp.pack(maria), 9.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", True)
        db.set_speaker(conn, "xiao-01", 7, 1, 1, "Мария", False)
        conn.execute("UPDATE transcripts SET updated_at = datetime('now', '+1 minute')")
        conn.commit()
    # Новая расшифровка: Игорь стал номером 2, Марии больше нет, под номером 1
    # теперь чужой голос — имя «Мария» ему достаться не должно.
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        1: (vp.normalized(stranger), 9.0), 2: (vp.normalized([0.97, 0.05, 0]), 9.0)})
    with closing(db.connect()) as conn:
        vp.remember_part(conn, KEY, "part.flac", [])
        assert db.speaker_names(conn, "xiao-01", 7) == {
            (1, 2): {"name": "Игорь", "is_me": True, "auto": False}}


def test_first_prints_of_a_part_leave_owner_names_alone(env, monkeypatch):
    # Часть расшифрована до появления отпечатков: сравнить не с чем, имена не трогаем.
    upload(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
    monkeypatch.setattr(vp, "embed_part",
                        lambda path, utterances: {1: (vp.normalized([0, 1.0, 0]), 9.0)})
    with closing(db.connect()) as conn:
        vp.remember_part(conn, KEY, "part.flac", [])
        assert db.speaker_names(conn, "xiao-01", 7)[(1, 0)]["name"] == "Игорь"


def test_session_deleted_during_save_keeps_no_prints(env):
    # Удаление сессии между проверкой и записью не должно оставить биометрию.
    upload(env)
    db = env.db
    with closing(db.connect()) as conn, closing(db.connect()) as other:
        done = []

        def delete_in_between(statement):
            if not done and statement.lstrip().upper().startswith("DELETE FROM VOICE_PRINTS"):
                done.append(db.delete_session(other, "xiao-01", 7))
        conn.set_trace_callback(delete_in_between)
        db.save_voice_prints(conn, KEY, {0: (vp.pack([1.0, 0, 0]), 9.0)})
        conn.set_trace_callback(None)
        assert done == [True]
        assert db.voice_prints(conn) == []


def test_refresh_reads_and_writes_names_under_one_lock(env):
    import sqlite3
    upload(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack([1.0, 0, 0]), 9.0)})
    blocked = []
    with closing(db.connect()) as conn, closing(sqlite3.connect(db.DB_PATH, timeout=0)) as other:
        def try_write(statement):
            if not blocked and "FROM voice_prints v" in statement:
                try:
                    other.execute("UPDATE parts SET bytes = bytes WHERE session = 7")
                    other.commit()
                    blocked.append(False)
                except sqlite3.OperationalError:
                    other.rollback()
                    blocked.append(True)
        conn.set_trace_callback(try_write)
        vp.refresh_session(conn, "xiao-01", 7)
        conn.set_trace_callback(None)
    # Пока сайт пересчитывает имена, воркер ждёт, а не пишет поверх более старым результатом.
    assert blocked == [True]


def test_prints_for_one_session_bring_only_named_voices_from_others(env):
    upload(env, session=7)
    upload(env, session=8)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, ("xiao-01", 7, 1), {0: (vp.pack([1.0, 0, 0]), 9.0),
                                                       1: (vp.pack([0, 1.0, 0]), 9.0)})
        db.save_voice_prints(conn, ("xiao-01", 8, 1), {0: (vp.pack([0, 0, 1.0]), 9.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        rows = db.voice_prints(conn, "xiao-01", 8)
    # Профилю нужны только названные голоса других сессий; сравнивать — свои номера.
    assert [(r["session"], r["speaker"]) for r in rows] == [(7, 0), (8, 0)]


@pytest.mark.parametrize("write", ["auto", "move"])
def test_names_are_not_written_into_a_session_deleted_mid_way(env, write):
    upload(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
    with closing(db.connect()) as conn, closing(db.connect()) as other:
        done = []

        def delete_in_between(statement):
            # Перенос имён берёт блокировку базы: удалить сессию можно только до
            # неё, после — удаление ждёт и потом убирает всё само.
            if not done and statement.lstrip().upper().startswith(("INSERT INTO SPEAKER_NAMES",
                                                                     "BEGIN IMMEDIATE")):
                done.append(db.delete_session(other, "xiao-01", 7))
        conn.set_trace_callback(delete_in_between)
        if write == "auto":
            db.set_auto_names(conn, "xiao-01", 7, {(1, 1): "Мария"})
        else:
            db.move_owner_names(conn, KEY, {0: 2}, db.transcript_version(conn, KEY))
        conn.set_trace_callback(None)
        assert done == [True]
        assert conn.execute("SELECT COUNT(*) FROM speaker_names").fetchone()[0] == 0


def test_names_survive_retranscription_when_one_person_has_two_chunk_numbers():
    # Gemini делит человека на номера по 5-минутным порциям: старый номер похож
    # на свою порцию (0,96) и почти так же на соседнюю (0,87) — имя не должно теряться.
    old_first, old_second = vp.normalized([1, 0, 0]), vp.normalized([0.80, -0.60, 0])
    new_first, new_second = vp.normalized([0.96, 0.28, 0]), vp.normalized([0.87, -0.493, 0])
    assert vp.carry_over({0: old_first, 10: old_second}, {0: new_first, 10: new_second}) \
        == {0: 0, 10: 10}


def test_voice_switch_off_stops_new_guesses(env, monkeypatch):
    upload(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack([1.0, 0, 0]), 9.0),
                                         1: (vp.pack([0.95, 0.1, 0]), 9.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
    monkeypatch.setattr(vp, "ENABLED", False)
    with closing(db.connect()) as conn:
        assert vp.refresh_session(conn, "xiao-01", 7) == 0
        assert (1, 1) not in db.speaker_names(conn, "xiao-01", 7)


def test_one_name_written_two_ways_is_one_person(env):
    for session in (7, 8, 9):
        upload(env, session=session)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, ("xiao-01", 7, 1), {0: (vp.pack([1.0, 0, 0]), 9.0)})
        db.save_voice_prints(conn, ("xiao-01", 8, 1), {0: (vp.pack([0.98, 0.1, 0]), 9.0)})
        db.save_voice_prints(conn, ("xiao-01", 9, 1), {0: (vp.pack([0.97, 0.05, 0]), 9.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        db.set_speaker(conn, "xiao-01", 8, 1, 0, "игорь", False)
        vp.refresh_session(conn, "xiao-01", 9)
        assert db.speaker_names(conn, "xiao-01", 9)[(1, 0)]["name"] == "Игорь"


def test_names_for_a_deleted_session_are_refused(env):
    upload(env)
    db = env.db
    with closing(db.connect()) as conn:
        assert db.delete_session(conn, "xiao-01", 7)
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        assert conn.execute("SELECT COUNT(*) FROM speaker_names").fetchone()[0] == 0
    with TestClient(env.app) as client:
        response = client.post("/api/ui/sessions/xiao-01/7/speakers", auth=AUTH,
                               json={"names": {"1:0": "Игорь"}, "me": None})
    assert response.status_code == 404


def test_names_fixed_after_a_failed_embed_stay_put(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    db = env.db
    igor, maria = [1.0, 0, 0], [0, 1.0, 0]
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack(igor), 9.0), 1: (vp.pack(maria), 9.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        db.set_speaker(conn, "xiao-01", 7, 1, 1, "Мария", False)
        # Новая расшифровка поменяла номера местами, снять отпечатки не вышло,
        # и владелец сам поправил имена под новую нумерацию.
        conn.execute("UPDATE transcripts SET updated_at = datetime('now', '+1 minute')")
        conn.commit()
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Мария", False)
        db.set_speaker(conn, "xiao-01", 7, 1, 1, "Игорь", False)
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        0: (vp.normalized(maria), 9.0), 1: (vp.normalized(igor), 9.0)})
    with closing(db.connect()) as conn:
        assert vp.backfill(conn) == 1
        # Исправления владельца уже про новую нумерацию — их не переносят обратно.
        assert db.speaker_names(conn, "xiao-01", 7) == {
            (1, 0): {"name": "Мария", "is_me": False, "auto": False},
            (1, 1): {"name": "Игорь", "is_me": False, "auto": False}}


def test_dropping_guesses_sends_the_summary_back(env, monkeypatch):
    upload(env)
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env))
    summary_stub(env, monkeypatch)
    run_all(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {1: (vp.pack([0, 1.0, 0]), 9.0)})
        db.set_auto_names(conn, "xiao-01", 7, {(1, 1): "Мария"})
        conn.execute("UPDATE summaries SET parts_covered = 1")
        conn.commit()
        db.save_voice_prints(conn, KEY, {})          # новая расшифровка без отпечатков
        covered = conn.execute("SELECT parts_covered FROM summaries WHERE session=7").fetchone()[0]
    assert covered == 0


def test_refresh_refuses_to_run_inside_someone_elses_transaction(env):
    upload(env)
    with closing(env.db.connect()) as conn:
        conn.execute("UPDATE parts SET bytes = bytes WHERE session = 7")
        with pytest.raises(RuntimeError):
            vp.refresh_session(conn, "xiao-01", 7)
        conn.rollback()


def test_backfill_goes_on_after_a_part_that_fails(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env, session=7)
    upload(env, session=8)
    run_all(env)

    def embed(path, utterances):
        if "_S0007_" in str(path):
            raise RuntimeError("битый файл")
        return {0: (vp.normalized([1, 0, 0]), 9.0)}
    monkeypatch.setattr(vp, "embed_part", embed)
    with closing(env.db.connect()) as conn:
        assert vp.backfill(conn) == 2
        assert sorted({r["session"] for r in env.db.voice_prints(conn)}) == [8]



def test_prints_on_a_migrated_database_stay_usable(env, monkeypatch):
    # Живая база: таблица отпечатков создана до появления версии расшифровки.
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    db = env.db
    with closing(db.connect()) as conn:
        conn.execute("DROP TABLE voice_prints")
        conn.execute("CREATE TABLE voice_prints (device_id TEXT NOT NULL, session INTEGER NOT NULL,"
                     " part INTEGER NOT NULL, speaker INTEGER NOT NULL, seconds REAL NOT NULL,"
                     " vec BLOB NOT NULL, PRIMARY KEY (device_id, session, part, speaker))")
        conn.execute("INSERT INTO voice_prints VALUES ('xiao-01', 7, 1, 0, 9.0, ?)",
                     (vp.pack([1.0, 0, 0]),))
        conn.commit()
    db.init_storage()
    with closing(db.connect()) as conn:
        assert [r["speaker"] for r in db.voice_prints(conn)] == [0]
        db.save_voice_prints(conn, KEY, {1: (vp.pack([0, 1.0, 0]), 9.0)})
        assert [r["speaker"] for r in db.voice_prints(conn)] == [1]


def test_failed_name_move_keeps_the_old_prints(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack([1.0, 0, 0]), 9.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        conn.execute("UPDATE transcripts SET updated_at = datetime('now', '+1 minute')")
        conn.commit()
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        2: (vp.normalized([0, 1.0, 0]), 9.0)})

    def broken(old, new):
        raise RuntimeError("база занята")
    monkeypatch.setattr(vp, "carry_over", broken)
    with closing(db.connect()) as conn:
        with pytest.raises(RuntimeError):
            vp.remember_part(conn, KEY, "part.flac", [])
        # Отпечатки и имена меняются вместе или не меняются вовсе.
        assert list(db.part_prints(conn, KEY)) == [0]
        assert db.speaker_names(conn, "xiao-01", 7)[(1, 0)]["name"] == "Игорь"


def test_names_do_not_jump_between_two_different_people():
    igor = vp.normalized([1, 0, 0])
    # Ни Мария, ни Пётр не Игорь: похожи на него почти одинаково и не похожи
    # друг на друга — имени здесь не место.
    maria, petr = vp.normalized([0.65, 0.76, 0]), vp.normalized([0.63, -0.1, 0.77])
    assert vp.carry_over({0: igor}, {1: maria, 2: petr}) == {0: None}


def test_backfill_rolls_back_a_part_that_breaks_mid_way(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env, session=7)
    upload(env, session=8)
    run_all(env)
    real = vp.remember_part

    def remember(conn, key, *args, **kwargs):
        if key[1] == 7:
            conn.execute("BEGIN IMMEDIATE")      # сломалась посреди своей транзакции
            raise RuntimeError("сбой")
        return real(conn, key, *args, **kwargs)
    monkeypatch.setattr(vp, "remember_part", remember)
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        0: (vp.normalized([1, 0, 0]), 9.0)})
    with closing(env.db.connect()) as conn:
        assert vp.backfill(conn) == 2
        assert sorted({r["session"] for r in env.db.voice_prints(conn)}) == [8]


def test_backfill_names_other_sessions_when_one_refresh_fails(env, monkeypatch):
    upload(env, session=7)
    upload(env, session=8)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, ("xiao-01", 7, 1), {0: (vp.pack([1.0, 0, 0]), 9.0),
                                                       1: (vp.pack([0.97, 0.05, 0]), 9.0)})
        db.save_voice_prints(conn, ("xiao-01", 8, 1), {0: (vp.pack([0.98, 0.1, 0]), 9.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
    real = vp.refresh_session

    def refresh(conn, device_id, session):
        if session == 7:
            raise RuntimeError("занято")
        return real(conn, device_id, session)
    monkeypatch.setattr(vp, "refresh_session", refresh)
    with closing(db.connect()) as conn:
        vp.backfill(conn)
        assert db.speaker_names(conn, "xiao-01", 8)[(1, 0)]["name"] == "Игорь"


def test_page_knows_the_owners_name_from_other_recordings(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env, session=7)
    upload(env, session=8)
    run_all(env)
    with closing(env.db.connect()) as conn:
        env.db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", True)
    with TestClient(env.app) as client:
        body = client.get("/api/ui/sessions/xiao-01/8", auth=AUTH).json()
    # «Это я» в новой записи ставит это имя, а не пустое поле.
    assert body["me_name"] == "Игорь"



def test_late_backfill_does_not_overwrite_newer_prints(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {5: (vp.pack([1.0, 0, 0]), 9.0)})
        # Медленный --backfill принёс отпечатки прежней версии уже после воркера.
        db.replace_part_prints(conn, KEY, {0: (vp.pack([0, 1.0, 0]), 9.0)},
                               "прежняя версия", lambda old: {})
        assert [r["speaker"] for r in db.voice_prints(conn)] == [5]


def test_names_typed_under_a_skipped_version_are_dropped(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    db = env.db
    igor, maria = [1.0, 0, 0], [0, 1.0, 0]
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack(igor), 9.0), 1: (vp.pack(maria), 9.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        # Вторая расшифровка: отпечатки снять не вышло, владелец вписал «Пётр».
        conn.execute("UPDATE transcripts SET updated_at = datetime('now', '+1 minute')")
        conn.commit()
        db.set_speaker(conn, "xiao-01", 7, 1, 2, "Пётр", False)
        # Третья расшифровка: под номером 2 теперь голос Марии.
        conn.execute("UPDATE transcripts SET updated_at = datetime('now', '+2 minutes')")
        conn.commit()
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        1: (vp.normalized([0.97, 0.05, 0]), 9.0), 2: (vp.normalized([0.05, 0.97, 0]), 9.0)})
    with closing(db.connect()) as conn:
        vp.remember_part(conn, KEY, "part.flac", [])
        # Игорь переехал за голосом; «Пётр» стоял на номере версии, от которой
        # нет отпечатков, — снят, иначе голос Марии ушёл бы в профиль Петра.
        assert db.speaker_names(conn, "xiao-01", 7) == {
            (1, 1): {"name": "Игорь", "is_me": False, "auto": False}}


def test_two_services_migrating_together_do_not_collide(env, monkeypatch):
    # Сайт и воркер стартуют вместе. Второй успевает перестроить базу между
    # проверкой столбцов первым и его блокировкой — первый не должен упасть
    # на повторном «ADD COLUMN».
    db = env.db
    with closing(db.connect()) as conn:
        conn.execute("ALTER TABLE voice_prints DROP COLUMN transcript_at")
        conn.commit()
    real_connect, real_init = db.connect, db.init_storage
    raced = []

    def other_service_first(statement):
        if not raced and statement.strip().upper() == "BEGIN IMMEDIATE":
            raced.append(True)
            monkeypatch.setattr(db, "connect", real_connect)
            real_init()

    def traced():
        conn = real_connect()
        conn.set_trace_callback(other_service_first)
        return conn
    monkeypatch.setattr(db, "connect", traced)
    db.init_storage()
    assert raced == [True]
    with closing(real_connect()) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(voice_prints)")}
    assert "transcript_at" in columns


def test_owner_names_on_a_migrated_database_follow_their_voice(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    db = env.db
    with closing(db.connect()) as conn:
        # Живая база: обе таблицы без версии расшифровки.
        conn.execute("DROP TABLE voice_prints")
        conn.execute("CREATE TABLE voice_prints (device_id TEXT NOT NULL, session INTEGER NOT NULL,"
                     " part INTEGER NOT NULL, speaker INTEGER NOT NULL, seconds REAL NOT NULL,"
                     " vec BLOB NOT NULL, PRIMARY KEY (device_id, session, part, speaker))")
        conn.execute("INSERT INTO voice_prints VALUES ('xiao-01', 7, 1, 0, 9.0, ?)",
                     (vp.pack([1.0, 0, 0]),))
        conn.execute("DROP TABLE speaker_names")
        conn.execute("CREATE TABLE speaker_names (device_id TEXT NOT NULL, session INTEGER NOT NULL,"
                     " part INTEGER NOT NULL, speaker INTEGER NOT NULL, name TEXT NOT NULL DEFAULT '',"
                     " is_me INTEGER NOT NULL DEFAULT 0, auto INTEGER NOT NULL DEFAULT 0,"
                     " PRIMARY KEY (device_id, session, part, speaker))")
        conn.execute("INSERT INTO speaker_names VALUES ('xiao-01', 7, 1, 0, 'Игорь', 0, 0)")
        conn.commit()
    db.init_storage()
    with closing(db.connect()) as conn:
        conn.execute("UPDATE transcripts SET updated_at = datetime('now', '+1 minute')")
        conn.commit()
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        3: (vp.normalized([0.97, 0.05, 0]), 9.0)})
    with closing(db.connect()) as conn:
        vp.remember_part(conn, KEY, "part.flac", [])
        assert db.speaker_names(conn, "xiao-01", 7) == {
            (1, 3): {"name": "Игорь", "is_me": False, "auto": False}}



def test_startup_does_not_wait_for_the_other_service_when_nothing_to_migrate(env, monkeypatch):
    import sqlite3
    db = env.db
    real = db.connect

    def impatient():
        conn = real()
        conn.execute("PRAGMA busy_timeout=0")
        return conn
    monkeypatch.setattr(db, "connect", impatient)
    with closing(sqlite3.connect(db.DB_PATH)) as other:
        other.execute("BEGIN IMMEDIATE")          # другая служба пишет
        db.init_storage()                         # перестраивать нечего — ждать незачем
        other.rollback()


def test_first_prints_after_retranscription_drop_names_of_the_old_numbering(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    db = env.db
    with closing(db.connect()) as conn:
        # Отпечатков у части ещё не было; владелец назвал номер 0.
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        conn.execute("UPDATE transcripts SET updated_at = datetime('now', '+1 minute')")
        conn.commit()
    # Новая расшифровка: под номером 0 теперь Мария. Перенести имя не по чему.
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        0: (vp.normalized([0, 1.0, 0]), 9.0)})
    with closing(db.connect()) as conn:
        vp.remember_part(conn, KEY, "part.flac", [])
        assert db.speaker_names(conn, "xiao-01", 7) == {}
        assert [r["owner_name"] for r in db.voice_prints(conn)] == [None]


def test_backfill_racing_the_worker_keeps_names_of_both_versions(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    db = env.db
    igor, petr = [1.0, 0, 0], [0, 0, 1.0]
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack(igor), 9.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        # Вторая расшифровка: снять не вышло, владелец вписал «Пётр».
        conn.execute("UPDATE transcripts SET updated_at = '2030-01-01 00:00:02'")
        conn.commit()
        db.set_speaker(conn, "xiao-01", 7, 1, 2, "Пётр", False)
        # Третья: воркер уже сохранил текст, а отпечатки ещё снимает.
        conn.execute("UPDATE transcripts SET updated_at = '2030-01-01 00:00:03'")
        conn.commit()
    # --backfill второй версии успевает раньше воркера.
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        2: (vp.normalized(petr), 9.0), 5: (vp.normalized([0.97, 0.05, 0]), 9.0)})
    with closing(db.connect()) as conn:
        vp.remember_part(conn, KEY, "part.flac", [], "2030-01-01 00:00:02")
    # Теперь воркер со своей, третьей версией.
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        7: (vp.normalized([0.05, 0, 0.97]), 9.0), 8: (vp.normalized([0.96, 0.08, 0]), 9.0)})
    with closing(db.connect()) as conn:
        vp.remember_part(conn, KEY, "part.flac", [], "2030-01-01 00:00:03")
        assert db.speaker_names(conn, "xiao-01", 7) == {
            (1, 7): {"name": "Пётр", "is_me": False, "auto": False},
            (1, 8): {"name": "Игорь", "is_me": False, "auto": False}}


V2, V3 = "2030-01-01 00:00:02", "2030-01-01 00:00:03"


def renumbered_twice(env, monkeypatch):
    """Игорь назван под первой версией; вторая расшифровка прошла без
    отпечатков; третья сохранена, и воркер ещё снимает отпечатки."""
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack([1.0, 0, 0]), 9.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        conn.execute("UPDATE transcripts SET updated_at = ?", (V2,))
        conn.execute("UPDATE transcripts SET updated_at = ?", (V3,))
        conn.commit()
    return db


def test_late_backfill_does_not_push_a_name_onto_one_typed_under_the_current_version(
        env, monkeypatch):
    db = renumbered_twice(env, monkeypatch)
    with closing(db.connect()) as conn:
        db.set_speaker(conn, "xiao-01", 7, 1, 1, "Пётр", False)      # вписано под третью
    # Запоздалый --backfill второй версии: там Игорь — номер 1, на месте Петра.
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        1: (vp.normalized([0.97, 0.05, 0]), 9.0), 4: (vp.normalized([0, 0, 1.0]), 9.0)})
    with closing(db.connect()) as conn:
        vp.remember_part(conn, KEY, "part.flac", [], V2)
    # Воркер с третьей версией: Игорь — номер 2.
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        1: (vp.normalized([0.05, 0, 0.97]), 9.0), 2: (vp.normalized([0.96, 0.08, 0]), 9.0)})
    with closing(db.connect()) as conn:
        vp.remember_part(conn, KEY, "part.flac", [], V3)
        assert db.speaker_names(conn, "xiao-01", 7) == {
            (1, 1): {"name": "Пётр", "is_me": False, "auto": False},
            (1, 2): {"name": "Игорь", "is_me": False, "auto": False}}


def test_late_backfill_keeps_names_typed_under_the_current_version(env, monkeypatch):
    db = renumbered_twice(env, monkeypatch)
    with closing(db.connect()) as conn:
        db.set_speaker(conn, "xiao-01", 7, 1, 5, "Анна", False)      # вписано под третью
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        1: (vp.normalized([0.97, 0.05, 0]), 9.0)})
    with closing(db.connect()) as conn:
        vp.remember_part(conn, KEY, "part.flac", [], V2)
        assert db.speaker_names(conn, "xiao-01", 7)[(1, 5)]["name"] == "Анна"


def test_second_pass_of_the_same_version_keeps_names_without_a_print(env, monkeypatch):
    # Воркер и --backfill (или два --backfill) взяли одну и ту же версию части.
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, TWO_VOICES))
    summary_stub(env, monkeypatch)
    upload(env)
    run_all(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        db.set_speaker(conn, "xiao-01", 7, 1, 3, "Мария", False)     # говорила мало, без отпечатка
        version = db.transcript_version(conn, KEY)
    monkeypatch.setattr(vp, "embed_part", lambda path, utterances: {
        0: (vp.normalized([1.0, 0, 0]), 9.0)})
    with closing(db.connect()) as conn:
        vp.remember_part(conn, KEY, "part.flac", [], version)
        vp.remember_part(conn, KEY, "part.flac", [], version)
        assert db.speaker_names(conn, "xiao-01", 7)[(1, 3)]["name"] == "Мария"
