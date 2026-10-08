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
