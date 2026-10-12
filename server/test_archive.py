"""Проверки архива WAV -> FLAC и точки расширения распознавания.

Что здесь проверяется без ffmpeg (UNIT): порядок шагов, состояние базы,
что удаляется и когда, миграция старой базы, выбор движка распознавания.

Что требует настоящего ffmpeg (INTEGRATION): само сжатие, доказательство
отсутствия потерь, отказ при испорченном FLAC, Range-запрос к архивной части.
Такие проверки помечены skipif и на машине без ffmpeg пропускаются — на VPS
ffmpeg есть, и там они идут.

Проверкой железа здесь не пахнет: диктофона в этих тестах нет вообще.
"""
import importlib
import shutil
import struct
import zlib
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"
PASSWORD = "owner-secret"
AUTH = ("owner", PASSWORD)

has_ffmpeg = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not has_ffmpeg, reason="нужен ffmpeg")


def wav(seconds=1, marker=0):
    """Тишина или ровный тон: лишь бы это был настоящий PCM 16 кГц/16 бит."""
    frames = 16000 * seconds
    payload = b"".join(
        struct.pack("<h", (index % 2000 - 1000) * marker) for index in range(frames)
    ) if marker else bytes(frames * 2)
    h = bytearray(44)
    h[0:4] = b"RIFF"
    struct.pack_into("<I", h, 4, 36 + len(payload))
    h[8:12] = b"WAVE"
    h[12:16] = b"fmt "
    struct.pack_into("<IHHIIHH", h, 16, 16, 1, 1, 16000, 32000, 2, 16)
    h[36:40] = b"data"
    struct.pack_into("<I", h, 40, len(payload))
    return bytes(h) + payload


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDER_DATA", str(tmp_path))
    monkeypatch.setenv("RECORDER_DEVICE_TOKEN", TOKEN)
    monkeypatch.setenv("RECORDER_OWNER_PASSWORD", PASSWORD)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "key-for-tests")
    monkeypatch.setenv("RECORDER_ARCHIVE_FLAC", "1")
    # Настоящий Claude проверки не зовут никогда: на VPS он установлен, и
    # проверка без подмены изложения висела там до таймаута в 900 с.
    monkeypatch.setenv("RECORDER_CLAUDE_BIN", "claude-is-not-called-in-tests")

    import archive, db, stt, transcribe, textnorm, duedate, summarize
    import app as app_module
    import worker

    for module in (textnorm, duedate, db, stt, transcribe, archive, summarize,
                   app_module, worker):
        importlib.reload(module)
    db.init_storage()
    # Плеер отдаёт громкую копию (audio.loud_copy); проверки отдачи сверяют
    # байты исходника, громкость проверяется отдельно.
    import audio
    monkeypatch.setattr(audio, "PLAY_LOUDNORM", False)

    class Env:
        pass

    bundle = Env()
    bundle.db = db
    bundle.archive = archive
    bundle.stt = stt
    bundle.transcribe = transcribe
    bundle.worker = worker
    bundle.app = app_module.app
    bundle.tmp = tmp_path
    return bundle


def upload(env, session=7, part=1, marker=0, seconds=1):
    body = wav(seconds=seconds, marker=marker)
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-Device-Id": "xiao-01",
        "X-Session": str(session),
        "X-Part": str(part),
        "X-Crc32": f"{zlib.crc32(body) & 0xFFFFFFFF:08x}",
    }
    with TestClient(env.app) as client:
        response = client.post("/api/v1/upload", content=body, headers=headers)
    assert response.status_code == 201, response.text
    return body


def mark_transcribed(env, status="done", session=7, part=1):
    """Сжатие идёт после расшифровки, поэтому её состояние ставится вручную."""
    with closing(env.db.connect()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO transcripts (device_id, session, part, status,"
            " text, utterances, duration_sec) VALUES ('xiao-01',?,?,?,'текст','[]',60)",
            (session, part, status),
        )
        conn.commit()


def part_row(env, session=7, part=1):
    with closing(env.db.connect()) as conn:
        return conn.execute(
            "SELECT * FROM parts WHERE session=? AND part=?", (session, part)
        ).fetchone()


# --- очередь и порядок шагов (без ffmpeg) ------------------------------------


def test_wav_is_deleted_only_after_the_flac_path_is_in_the_database(env, monkeypatch):
    """Порядок: проверенный FLAC -> база -> и только теперь удаление WAV."""
    upload(env)
    mark_transcribed(env)
    seen = []

    def fake_convert(wav_path):
        flac = Path(wav_path).with_suffix(".flac")
        flac.write_bytes(b"fLaC stub")
        return flac

    def spy_finish(conn, key, flac_path):
        # В момент записи пути в базу оригинал обязан быть ещё на месте.
        seen.append(Path(str(flac_path)).with_suffix(".wav").exists())
        return original_finish(conn, key, flac_path)

    original_finish = env.db.finish_archive
    monkeypatch.setattr(env.archive, "convert", fake_convert)
    monkeypatch.setattr(env.worker.db, "finish_archive", spy_finish)

    with closing(env.db.connect()) as conn:
        assert env.worker.run_archive(conn) is True

    assert seen == [True], "WAV удалили раньше, чем записали путь к FLAC"
    row = part_row(env)
    assert row["archive_status"] == "done"
    assert row["audio_path"].endswith(".flac")
    assert Path(row["audio_path"]).exists()
    assert not Path(row["audio_path"]).with_suffix(".wav").exists()
    # Размер и сумма остаются от принятого WAV: это контракт приёмки.
    assert row["bytes"] == len(wav())


def test_wav_is_not_swept_when_its_flac_is_missing(env):
    """База говорит «FLAC готов», а на диске только WAV (например, после
    неполного восстановления копии). Этот WAV — последняя копия звука."""
    upload(env)
    mark_transcribed(env)
    wav_path = Path(part_row(env)["audio_path"])
    with closing(env.db.connect()) as conn:
        env.db.finish_archive(conn, ("xiao-01", 7, 1), wav_path.with_suffix(".flac"))
        assert env.db.stale_wavs(conn) == []
    assert wav_path.exists()


def test_unrecoverable_failure_keeps_the_wav_and_stops_trying(env, monkeypatch):
    def boom(_path):
        raise env.archive.ArchiveError("FLAC не совпал с оригиналом", retryable=False)

    upload(env)
    mark_transcribed(env)
    monkeypatch.setattr(env.archive, "convert", boom)
    with closing(env.db.connect()) as conn:
        assert env.worker.run_archive(conn) is True
        assert env.db.claim_part_for_archive(conn) is None

    row = part_row(env)
    assert row["archive_status"] == "failed"
    assert "не совпал" in row["archive_error"]
    assert Path(row["audio_path"]).exists(), "оригинал обязан остаться"


def test_failed_archive_is_visible_on_the_status_page(env, monkeypatch):
    """Вставший архив не ломает звук, но и молчать о нём нельзя."""
    upload(env)
    mark_transcribed(env)
    monkeypatch.setattr(env.archive, "convert", lambda _p: (_ for _ in ()).throw(
        env.archive.ArchiveError("ffmpeg вернул 1", retryable=False)))
    with closing(env.db.connect()) as conn:
        env.worker.run_archive(conn)

    with TestClient(env.app) as client:
        status = client.get("/api/ui/status", auth=AUTH).json()
    assert status["archive_failed"][0]["part"] == 1
    assert "ffmpeg" in status["archive_failed"][0]["archive_error"]


# --- миграция и совместимость со старой базой --------------------------------


def test_player_survives_the_moment_between_the_new_path_and_the_old_file(env):
    """Запрос плеера может прийти ровно между сменой пути и удалением файла."""
    upload(env)
    row = part_row(env)
    wav_path = Path(row["audio_path"])
    with closing(env.db.connect()) as conn:
        # В базе уже FLAC, а файла ещё нет: отдаём то, что есть на диске.
        env.db.finish_archive(conn, ("xiao-01", 7, 1), wav_path.with_suffix(".flac"))
        path, media = env.db.audio_file(conn, "xiao-01", 7, 1)
    assert path == wav_path and media == "audio/wav"

    with TestClient(env.app) as client:
        assert client.get("/audio/xiao-01/7/1", auth=AUTH).status_code == 200


# --- настоящий ffmpeg --------------------------------------------------------

@needs_ffmpeg
def test_conversion_is_lossless_and_smaller(env):
    upload(env, marker=1, seconds=3)
    row = part_row(env)
    wav_path = Path(row["audio_path"])
    before = wav_path.stat().st_size

    flac = env.archive.convert(wav_path)

    assert flac.exists() and flac.suffix == ".flac"
    assert wav_path.exists(), "исходник трогать нельзя: удаляет воркер, и позже"
    assert flac.stat().st_size < before
    # Главное доказательство: декодированный звук совпадает байт в байт.
    assert env.archive.pcm_md5(flac) == env.archive.pcm_md5(wav_path)
    assert list(wav_path.parent.glob("*.part")) == []


@needs_ffmpeg
def test_mismatched_audio_is_rejected_even_if_it_is_a_valid_flac(env, monkeypatch):
    """Настоящий FLAC, но не того звука: сверка PCM ловит и это."""
    upload(env, session=7, part=1, marker=1, seconds=2)
    upload(env, session=8, part=1, marker=2, seconds=1)
    ours = Path(part_row(env, 7, 1)["audio_path"])
    alien = Path(part_row(env, 8, 1)["audio_path"])

    monkeypatch.setattr(env.archive, "encode",
                        lambda src, dst: env.archive._run(
                            ["-y", "-i", str(alien), "-c:a", "flac", "-f", "flac", str(dst)]))

    with pytest.raises(env.archive.ArchiveError):
        env.archive.convert(ours)
    assert ours.exists()


@needs_ffmpeg
def test_worker_compresses_and_the_player_serves_flac_with_range(env):
    """Safari без Range не играет ничего: после сжатия Range обязан остаться."""
    upload(env, marker=1, seconds=2)
    mark_transcribed(env)

    with closing(env.db.connect()) as conn:
        assert env.worker.run_archive(conn) is True

    row = part_row(env)
    assert row["audio_path"].endswith(".flac")
    assert not Path(row["audio_path"]).with_suffix(".wav").exists()

    with TestClient(env.app) as client:
        whole = client.get("/audio/xiao-01/7/1", auth=AUTH)
        assert whole.status_code == 200
        assert whole.headers["content-type"] == "audio/flac"
        assert whole.content[:4] == b"fLaC"

        ranged = client.get("/audio/xiao-01/7/1", auth=AUTH,
                            headers={"Range": "bytes=0-3"})
        assert ranged.status_code == 206
        assert ranged.content == b"fLaC"


@needs_ffmpeg
def test_conversion_after_a_crash_overwrites_the_orphan_and_still_verifies(env):
    """Крах между заменой файла и записью в базу оставляет осиротевший FLAC."""
    upload(env, marker=1)
    wav_path = Path(part_row(env)["audio_path"])
    orphan = wav_path.with_suffix(".flac")
    orphan.write_bytes(b"leftover from a crashed attempt")

    flac = env.archive.convert(wav_path)

    assert flac == orphan
    assert env.archive.pcm_md5(flac) == env.archive.pcm_md5(wav_path)


# --- точка расширения распознавания ------------------------------------------


def _capture_post(env, monkeypatch):
    seen = {}

    class Reply:
        status_code = 200

        @staticmethod
        def json():
            return {"metadata": {"duration": 1.0},
                    "results": {"channels": [{"alternatives": [{"transcript": "да"}]}]}}

    def fake_post(url, params=None, headers=None, content=None, timeout=None):
        seen["headers"], seen["content"] = headers, content
        return Reply()

    monkeypatch.setattr(env.transcribe.httpx, "post", fake_post)
    return seen


def _quiet_wav(path, seconds=2, amplitude=300):
    """Тон 440 Гц около -40 дБ: громкость, как у записей XIAO Sense."""
    import math
    import struct
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(
            struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 440 * i / 16000)))
            for i in range(16000 * seconds)))


def _rms(wav_bytes):
    import array
    samples = array.array("h", wav_bytes[44:])
    return (sum(s * s for s in samples) / len(samples)) ** 0.5


@needs_ffmpeg
def test_quiet_part_reaches_deepgram_louder_and_same_length(env, monkeypatch):
    """Тихая запись уходит на расшифровку громче, но той же длины: иначе
    времена реплик разъедутся со звуком на сайте."""
    seen = _capture_post(env, monkeypatch)
    quiet = env.tmp / "quiet.wav"
    _quiet_wav(quiet)
    env.transcribe.transcribe_file(str(quiet))
    sent = seen["content"]
    assert seen["headers"]["Content-Type"] == "audio/wav"
    assert _rms(sent) > 5 * _rms(quiet.read_bytes())  # больше +14 дБ
    assert abs(len(sent) - quiet.stat().st_size) <= 64 * 2  # та же длина


