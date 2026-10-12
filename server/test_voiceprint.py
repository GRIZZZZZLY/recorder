"""Узнавание по голосу: правила сравнения, хранение отпечатков, подписи номеров."""
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

import voiceprint as vp
from test_pipeline import AUTH, env, fake_transcribe, run_all, summary_stub, upload  # noqa: F401


def test_two_close_profiles_give_no_name():
    profiles = {"Игорь": vp.normalized([1, 0, 0]), "Алексей": vp.normalized([0.95, 0.31, 0])}
    assert vp.best_name(vp.normalized([1, 0, 0]), profiles) is None


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


def test_names_survive_retranscription_when_one_person_has_two_chunk_numbers():
    # Gemini делит человека на номера по 5-минутным порциям: старый номер похож
    # на свою порцию (0,96) и почти так же на соседнюю (0,87) — имя не должно теряться.
    old_first, old_second = vp.normalized([1, 0, 0]), vp.normalized([0.80, -0.60, 0])
    new_first, new_second = vp.normalized([0.96, 0.28, 0]), vp.normalized([0.87, -0.493, 0])
    assert vp.carry_over({0: old_first, 10: old_second}, {0: new_first, 10: new_second}) \
        == {0: 0, 10: 10}


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


def test_names_do_not_jump_between_two_different_people():
    igor = vp.normalized([1, 0, 0])
    # Ни Мария, ни Пётр не Игорь: похожи на него почти одинаково и не похожи
    # друг на друга — имени здесь не место.
    maria, petr = vp.normalized([0.65, 0.76, 0]), vp.normalized([0.63, -0.1, 0.77])
    assert vp.carry_over({0: igor}, {1: maria, 2: petr}) == {0: None}


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


V2, V3 = "2030-01-01 00:00:02", "2030-01-01 00:00:03"


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


def test_voice_groups_join_numbers_of_one_voice_and_keep_names_apart(env):
    upload(env)
    upload(env, part=2, marker=2)
    db = env.db
    with closing(db.connect()) as conn:
        # Части сохраняются по одной: вторая save_voice_prints не трогает первую.
        db.save_voice_prints(conn, ("xiao-01", 7, 1), {
            0: (vp.pack(vp.normalized([1, 0, 0])), 9.0),
            10: (vp.pack(vp.normalized([0.95, 0.2, 0])), 9.0),      # тот же человек, другая порция
            1: (vp.pack(vp.normalized([0, 1, 0])), 9.0)})
        db.save_voice_prints(conn, ("xiao-01", 7, 2), {
            0: (vp.pack(vp.normalized([0.97, 0.1, 0])), 9.0),
            3: (vp.pack(vp.normalized([0.1, 0.97, 0])), 9.0)})
        # Похожие голоса под разными именами владельца не сливаются.
        db.set_speaker(conn, "xiao-01", 7, 1, 1, "Мария", False)
        db.set_speaker(conn, "xiao-01", 7, 2, 3, "Анна", False)
        keys = [(1, 0), (1, 10), (1, 1), (2, 0), (2, 3)]
        groups, short = vp.session_groups(conn, "xiao-01", 7, keys)
    assert sorted(sorted(g) for g in groups) == [[(1, 0), (1, 10), (2, 0)], [(1, 1)], [(2, 3)]]
    assert short == []


def test_session_page_gives_voice_groups_largest_first(env, monkeypatch):
    texts = {1: [(0.5, 0, "раз"), (4.0, 1, "два"), (7.5, 0, "три"), (11.0, 2, "ага")]}
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, texts))
    summary_stub(env, monkeypatch)
    voices(monkeypatch, {(7, 1): {0: [1, 0, 0], 1: [0, 1, 0]}})
    upload(env)
    run_all(env)
    with TestClient(env.app) as client:
        body = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()
    # Говорящий 0 — две реплики по 3 с, говорящий 1 — одна; у 2 отпечатка нет.
    # Названных в записи нет — подсказывать не с кем сравнить.
    assert body["voice_groups"] == [{"keys": ["1:0"], "seconds": 6.0, "hint": None},
                                    {"keys": ["1:1"], "seconds": 3.0, "hint": None}]
    assert body["short_keys"] == ["1:2"]


# --- фразы: голос внутри номера ---------------------------------------------

THREE_LINES = {1: [(0.5, 0, "раз"), (4.0, 0, "два"), (7.5, 1, "три")]}


def lines(monkeypatch, table):
    """Подмена отпечатков фраз: {(сессия, часть): {индекс: (номер, вектор)}}."""
    def embed(path, utterances):
        name = Path(path).name
        key = (int(name.split("_S")[1][:4]), int(name.split("_P")[1][:2]))
        return {idx: (speaker, vp.normalized(vector))
                for idx, (speaker, vector) in table.get(key, {}).items()}
    monkeypatch.setattr(vp, "embed_lines", embed)


def test_lines_are_corrected_in_the_transcript_and_the_summary_is_rebuilt(env, monkeypatch):
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, THREE_LINES))
    summary_stub(env, monkeypatch)
    voices(monkeypatch, {(7, 1): {0: [1, 0, 0], 1: [0, 1, 0]}})
    lines(monkeypatch, {(7, 1): {0: (0, [0.98, 0.1, 0]), 1: (0, [0.05, 0.99, 0]),
                                 2: (1, [0, 1, 0])}})
    upload(env)
    run_all(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        db.set_speaker(conn, "xiao-01", 7, 1, 1, "Мария", False)
        conn.execute("UPDATE summaries SET parts_covered = 1")
        conn.commit()
        assert vp.refresh_session(conn, "xiao-01", 7) >= 1
        timeline, _ = db.session_timeline(conn, "xiao-01", 7)
        covered = conn.execute("SELECT parts_covered FROM summaries WHERE session=7").fetchone()[0]
    assert [(u["idx"], u["label"], u["by_voice"]) for u in timeline] == [
        (0, "Игорь", False), (1, "Мария", True), (2, "Мария", False)]
    assert covered == 0                       # в тексте для модели теперь верное имя


def test_a_mixed_number_does_not_shield_its_own_lines(env, monkeypatch):
    # Номер (1, 1) подписан «Игорь», но в нём в основном говорит Мария. Его
    # отпечаток в профиле Игоря перетянул бы профиль на Марию, и чужие фразы
    # этого же номера не нашлись бы. Профиль для фраз номера — без него самого.
    texts = {1: [(0.5, 0, "раз"), (4.0, 1, "два"), (7.5, 2, "три")]}
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, texts))
    summary_stub(env, monkeypatch)
    voices(monkeypatch, {(7, 1): {0: [1, 0, 0], 1: [0.2, 0.98, 0], 2: [0, 1, 0]}})
    lines(monkeypatch, {(7, 1): {1: (1, [0.5, 0.866, 0])}})
    upload(env)
    run_all(env)
    db = env.db
    with closing(db.connect()) as conn:
        db.set_speaker(conn, "xiao-01", 7, 1, 0, "Игорь", False)
        db.set_speaker(conn, "xiao-01", 7, 1, 1, "Игорь", False)
        db.set_speaker(conn, "xiao-01", 7, 1, 2, "Мария", False)
        vp.refresh_session(conn, "xiao-01", 7)
        timeline, _ = db.session_timeline(conn, "xiao-01", 7)
    assert {u["idx"]: u["label"] for u in timeline}[1] == "Мария"


def corrected_session(env, monkeypatch, texts=THREE_LINES, owner=(("Игорь", 0), ("Мария", 1))):
    """Сессия 7: номер 0 — Игорь, но фраза 1 голосом Марии."""
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, texts))
    summary_stub(env, monkeypatch)
    present = {speaker for _, speaker, _ in texts[1]}
    voices(monkeypatch, {(7, 1): {s: v for s, v in {0: [1, 0, 0], 1: [0, 1, 0]}.items()
                                  if s in present}})
    lines(monkeypatch, {(7, 1): {0: (0, [0.98, 0.1, 0]), 1: (0, [0.05, 0.99, 0])}})
    upload(env)
    run_all(env)
    db = env.db
    with closing(db.connect()) as conn:
        for name, speaker in owner:
            db.set_speaker(conn, "xiao-01", 7, 1, speaker, name, False)
        vp.refresh_session(conn, "xiao-01", 7)
    return db


def test_a_voice_line_means_the_recording_is_not_one_voice(env, monkeypatch):
    # Gemini дал всей записи один номер, а голос нашёл в нём второго человека:
    # это разговор, а не заметка вслух — иначе договорённости с Марией пропадут.
    texts = {1: [(0.5, 0, "раз"), (4.0, 0, "два")]}
    db = corrected_session(env, monkeypatch, texts, owner=(("Игорь", 0),))
    with closing(db.connect()) as conn:
        # профиль Марии — из другой записи
        upload(env, session=8)
        db.save_voice_prints(conn, ("xiao-01", 8, 1), {0: (vp.pack([0, 1.0, 0]), 9.0)})
        db.set_speaker(conn, "xiao-01", 8, 1, 0, "Мария", False)
        vp.refresh_session(conn, "xiao-01", 7)
        text = db.session_transcript(conn, "xiao-01", 7)
    assert "Мария" in text
    assert "[во всей записи один голос]" not in text


def test_minutes_in_the_names_list_leave_out_lines_of_another_voice(env, monkeypatch):
    corrected_session(env, monkeypatch)
    with TestClient(env.app) as client:
        body = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()
    by_key = {tuple(g["keys"]): g["seconds"] for g in body["voice_groups"]}
    assert by_key[("1:0",)] == 3.0          # фраза 1 — Мария, не Игорь


# --- подсказка для безымянного голоса -------------------------------------------


def test_names_list_carries_hints_only_for_unnamed_voices(env, monkeypatch):
    texts = {1: [(0.5, 0, "раз"), (4.0, 1, "два"), (7.5, 2, "три")]}
    monkeypatch.setattr(env.transcribe, "transcribe_file", fake_transcribe(env, texts))
    summary_stub(env, monkeypatch)
    voices(monkeypatch, {(7, 1): {0: [1, 0, 0], 1: [0, 1, 0], 2: [0.54, 0.33, 0.77]}})
    upload(env)
    run_all(env)
    with closing(env.db.connect()) as conn:
        env.db.set_speaker(conn, "xiao-01", 7, 1, 0, "Гоша", False)
        env.db.set_speaker(conn, "xiao-01", 7, 1, 1, "Игорь", False)
    with TestClient(env.app) as client:
        body = client.get("/api/ui/sessions/xiao-01/7", auth=AUTH).json()
    hints = {tuple(g["keys"]): g.get("hint") for g in body["voice_groups"]}
    assert hints[("1:2",)]["name"] == "Гоша"
    assert hints[("1:0",)] is None and hints[("1:1",)] is None


def test_guest_never_sees_voice_hints():
    import share
    view = {"day": "2026-10-09", "first_at": "x", "parts": [], "summary": None, "timeline": [],
            "speakers": [], "short_keys": [],
            "voice_groups": [{"keys": ["1:2"], "seconds": 3.0, "hint": {"name": "Гоша", "score": 0.54}}]}
    assert share.guest_view(view)["voice_groups"] == [{"keys": ["1:2"], "seconds": 3.0}]
