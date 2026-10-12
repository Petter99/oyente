"""Pruebas de audio, VAD, grabación y retención (T2.*)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from tests.synth import ESTUDIANTE, PROFESOR, conversation, silence, speech

from transcriptor.audio.decode import decode_file, encode_opus, estimate_mb
from transcriptor.audio.recorder import ChannelRecorder
from transcriptor.audio.retention import delete_now, finalize_audio, keep_forever, sweep_expired
from transcriptor.audio.sources import FileAudioSource
from transcriptor.audio.types import SAMPLE_RATE, to_mono_16k
from transcriptor.audio.vad import EnergyDetector, SileroDetector, StreamingSegmenter
from transcriptor.config.models import RetentionConfig


def test_resample_to_16k_mono() -> None:
    stereo = np.zeros((48_000, 2), dtype=np.float32)
    stereo[:, 0] = np.sin(np.linspace(0, 2 * np.pi * 440, 48_000))
    out = to_mono_16k(stereo, 48_000)
    assert out.dtype == np.float32
    assert out.size == SAMPLE_RATE


def test_t2_1_file_source_feeds_blocks() -> None:
    audio = speech(PROFESOR, 2.0)
    src = FileAudioSource(samples=audio, chunk_ms=100)
    src.start()
    blocks = []
    while (b := src.read(timeout=0.5)) is not None:
        blocks.append(b)
    src.stop()
    assert sum(b.size for b in blocks) == audio.size
    assert src.level_db > -60


@pytest.mark.parametrize(
    "detector", [EnergyDetector(), SileroDetector()], ids=["energia", "silero"]
)
def test_t2_2_vad_segments_conversation(detector: object) -> None:
    turns = [(PROFESOR, 3.0), (ESTUDIANTE, 1.5), (PROFESOR, 2.5), (ESTUDIANTE, 2.0)] * 2 + [
        (PROFESOR, 2.0)
    ]
    audio, truth = conversation(turns, pause=1.0)
    seg = StreamingSegmenter("clase", detector)  # type: ignore[arg-type]
    utts = []
    for i in range(0, audio.size, 1600):
        utts.extend(seg.feed(audio[i : i + 1600]))
    utts.extend(seg.flush())
    assert abs(len(utts) - len(truth)) <= 1
    for utt, (start, end, _) in zip(utts, truth, strict=False):
        assert abs(utt.start - start) < 0.5
        assert abs(utt.end - end) < 0.5


def test_vad_long_monologue_is_split() -> None:
    audio = np.concatenate([speech(PROFESOR, 40.0), silence(1.0)])
    seg = StreamingSegmenter("clase", EnergyDetector())
    utts = list(seg.feed(audio)) + list(seg.flush())
    assert len(utts) >= 2
    assert all(u.duration <= 27 for u in utts)


def test_t2_3_flac_readable_after_abrupt_kill(tmp_path: Path) -> None:
    out = tmp_path / "clase.flac"
    code = textwrap.dedent(f"""
        import numpy as np, os, sys
        sys.path.insert(0, {str(Path.cwd())!r})
        from transcriptor.audio.recorder import ChannelRecorder
        rec = ChannelRecorder(__import__('pathlib').Path({str(out)!r}), "clase")
        for _ in range(30):
            rec.write(np.full(1600, 0.1, dtype=np.float32))
        print("listo", flush=True)
        os._exit(9)  # cierre abrupto sin cerrar el archivo
    """)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert "listo" in proc.stdout
    data = decode_file(out)
    assert data.size >= 28 * 1600  # se conserva prácticamente todo lo escrito


def test_recorder_pads_when_channel_falls_behind(tmp_path: Path) -> None:
    rec = ChannelRecorder(tmp_path / "mic.flac", "mic")
    rec.write(np.zeros(SAMPLE_RATE, dtype=np.float32))
    rec.pad_to(5.0)
    assert rec.seconds == pytest.approx(5.0, abs=0.01)
    rec.close()


def _make_flac(path: Path, seconds: float = 3.0) -> Path:
    sf.write(str(path), speech(PROFESOR, seconds), SAMPLE_RATE, format="FLAC")
    return path


def test_t2_4_retention_never_deletes_after_success(tmp_path: Path) -> None:
    files = [_make_flac(tmp_path / "clase.flac"), _make_flac(tmp_path / "mic.flac")]
    res = finalize_audio(files, "opus", RetentionConfig(mode="never"), final_pass_ok=True)
    assert res.kept == [] and not any(f.exists() for f in files)


def test_t2_4_retention_never_keeps_audio_if_final_pass_failed(tmp_path: Path) -> None:
    files = [_make_flac(tmp_path / "clase.flac")]
    res = finalize_audio(files, "opus", RetentionConfig(mode="never"), final_pass_ok=False)
    assert files[0].exists()
    assert res.kept == files


def test_t2_4_retention_days_expires(tmp_path: Path) -> None:
    files = [
        _make_flac(tmp_path / "s1" / "clase.flac") if (tmp_path / "s1").mkdir() is None else None
    ]
    now = datetime(2026, 10, 12, 18, 0)
    res = finalize_audio(
        [f for f in files if f],
        "flac",
        RetentionConfig(mode="days", days=30),
        final_pass_ok=True,
        now=now,
    )
    assert res.expires_at == now + timedelta(days=30)
    assert sweep_expired(tmp_path, now=now + timedelta(days=29)) == []
    removed = sweep_expired(tmp_path, now=now + timedelta(days=31))
    assert len(removed) == 1 and not removed[0].exists()


def test_t2_4_keep_forever_and_delete_now(tmp_path: Path) -> None:
    f = _make_flac(tmp_path / "clase.flac")
    finalize_audio(
        [f], "flac", RetentionConfig(mode="days", days=1), True, now=datetime(2026, 1, 1)
    )
    keep_forever(tmp_path)
    assert sweep_expired(tmp_path, now=datetime(2027, 1, 1)) == []
    assert delete_now(tmp_path) == [f]


def test_t2_5_opus_and_flac_sizes(tmp_path: Path) -> None:
    seconds = 60.0
    audio = speech(PROFESOR, seconds)
    flac = tmp_path / "a.flac"
    sf.write(str(flac), audio, SAMPLE_RATE, format="FLAC", subtype="PCM_16")
    opus = encode_opus(flac, tmp_path / "a.opus")
    opus_mb_h = opus.stat().st_size / 1e6 * 3600 / seconds
    flac_mb_h = flac.stat().st_size / 1e6 * 3600 / seconds
    assert 6 <= opus_mb_h <= 14, opus_mb_h  # ≈ 11 MB/h por canal
    assert flac_mb_h > 3 * opus_mb_h
    # La versión Opus se puede volver a leer (re-transcribir).
    back = decode_file(opus)
    assert abs(back.size / SAMPLE_RATE - seconds) < 0.2
    assert estimate_mb("opus", 1.5) == pytest.approx(33.0)
    assert estimate_mb("flac", 1.5) == pytest.approx(180.0)


def test_t2_6_lost_source_emits_event() -> None:
    src = FileAudioSource(samples=speech(PROFESOR, 3.0), lose_after_s=1.0, realtime=False)
    events = []
    src.on_lost(lambda s, reason: events.append(reason))
    src.start()
    src.finished.wait(5)
    src.stop()
    assert events == ["simulado"]
    assert src.lost


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="cronometrado")
def test_realtime_file_source_paces() -> None:
    src = FileAudioSource(samples=speech(PROFESOR, 1.0), realtime=True, speed=4.0)
    t0 = time.monotonic()
    src.start()
    src.finished.wait(5)
    src.stop()
    assert time.monotonic() - t0 >= 0.2
