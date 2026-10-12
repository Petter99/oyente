"""Transcripción real con faster-whisper (T3.2, T3.4, T3.5). Lentas: descargan modelos.

Ejecutar con:  uv run pytest -m slow tests/integration/test_asr_real.py
El audio se genera con la voz en español del sistema (macOS «say»); en otros sistemas se omite.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from transcriptor.asr.base import TranscribeOptions
from transcriptor.asr.faster_whisper_engine import FasterWhisperTranscriber
from transcriptor.asr.wer import wer
from transcriptor.audio.types import SAMPLE_RATE

pytestmark = pytest.mark.slow

TEXT = (
    "Buenas tardes. Hoy vamos a ver integración por partes. "
    "La regla LIATE nos dice qué función elegir como u: primero logaritmos, "
    "luego inversas trigonométricas, algebraicas, trigonométricas y exponenciales. "
    "La tarea es la hoja de ejercicios cuatro, para el próximo jueves."
)


def _say(text: str, path: Path, voice: str = "Paulina") -> Path:
    if not shutil.which("say"):
        pytest.skip("Requiere la voz del sistema de macOS")
    subprocess.run(["say", "-v", voice, "-o", str(path), "--data-format=LEI16@16000", text],
                   check=True)
    return path


@pytest.fixture(scope="module")
def model() -> FasterWhisperTranscriber:
    return FasterWhisperTranscriber("small", "cpu", "int8")


def test_t3_2_wer_small_model(tmp_path: Path, model: FasterWhisperTranscriber) -> None:
    wav = _say(TEXT, tmp_path / "clase.wav")
    segs = model.transcribe_file(wav, TranscribeOptions(language="es", glossary=("LIATE",)))
    hypothesis = " ".join(s.text for s in segs)
    score = wer(TEXT, hypothesis)
    print(f"\nWER={score:.3f}\n{hypothesis}")
    assert score <= 0.15
    assert all(s.final for s in segs)
    assert segs[0].words and 0 <= segs[0].words[0].probability <= 1


def test_t3_4_silence_produces_no_text(tmp_path: Path, model: FasterWhisperTranscriber) -> None:
    wav = tmp_path / "silencio.wav"
    rng = np.random.default_rng(0)
    sf.write(str(wav), (0.001 * rng.standard_normal(SAMPLE_RATE * 60)).astype(np.float32), SAMPLE_RATE)
    segs = model.transcribe_file(wav, TranscribeOptions(language="es"))
    assert segs == []


def test_live_mode_offsets(tmp_path: Path) -> None:
    live = FasterWhisperTranscriber("small", "cpu", "int8", live=True)
    wav = _say("La tarea es para el jueves.", tmp_path / "f.wav")
    data, _ = sf.read(str(wav), dtype="float32")
    segs = live.transcribe(data, offset=120.0, opts=TranscribeOptions(language="es"))
    assert segs and segs[0].start >= 120.0
    assert wer("La tarea es para el jueves", " ".join(s.text for s in segs)) <= 0.2
