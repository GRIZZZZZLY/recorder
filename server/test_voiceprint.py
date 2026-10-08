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


def test_failed_model_on_retranscription_drops_old_guesses(env, monkeypatch):
    upload(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack([1.0, 0, 0]), 9.0),
                                         1: (vp.pack([0, 1.0, 0]), 9.0)})
        db.set_auto_names(conn, "xiao-01", 7, {(1, 1): "Мария"})

    def broken(path, utterances):
        raise RuntimeError("модель упала")
    monkeypatch.setattr(vp, "embed_part", broken)
    with closing(db.connect()) as conn:
        # Номера части уже другие: старая догадка стояла бы под чужими словами.
        vp.remember_part(conn, KEY, "part.flac", [])
        assert db.voice_prints(conn) == []
        assert db.speaker_names(conn, "xiao-01", 7) == {}


def test_old_voices_find_their_new_numbers_one_to_one():
    a, b, c = vp.normalized([1, 0, 0]), vp.normalized([0, 1, 0]), vp.normalized([0, 0, 1])
    near_a = vp.normalized([0.95, 0.1, 0])
    # Номера поменялись местами; третий старый голос из новой расшифровки пропал.
    assert vp.carry_over({0: a, 1: b, 2: c}, {0: b, 1: near_a}) == {0: 1, 1: 0, 2: None}
    # Два старых номера одного голоса — новый номер достаётся более похожему.
    assert vp.carry_over({0: a, 5: near_a}, {3: a}) == {0: 3, 5: None}


def test_owner_names_follow_their_voice_after_retranscription(env, monkeypatch):
    upload(env)
    db = env.db
    igor, maria, stranger = [1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]
    with closing(db.connect()) as conn:
        db.save_voice_prints(conn, KEY, {0: (vp.pack(igor), 9.0), 1: (vp.pack(maria), 9.0)})
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", True)
        db.set_speaker(conn, "xiao-01", 7, 1, 1, "Мария", False)
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
            if not done and statement.lstrip().upper().startswith(("INSERT INTO SPEAKER_NAMES",
                                                                     "DELETE FROM SPEAKER_NAMES")):
                done.append(db.delete_session(other, "xiao-01", 7))
        conn.set_trace_callback(delete_in_between)
        if write == "auto":
            db.set_auto_names(conn, "xiao-01", 7, {(1, 1): "Мария"})
        else:
            db.move_owner_names(conn, KEY, {0: 2})
        conn.set_trace_callback(None)
        assert done == [True]
        assert conn.execute("SELECT COUNT(*) FROM speaker_names").fetchone()[0] == 0
