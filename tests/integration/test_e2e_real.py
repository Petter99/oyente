"""Extremo a extremo con modelos reales: grabar → Whisper → profesor → nota (lenta).

Clase sintetizada con las voces del sistema (macOS); se pasa como fuente de audio en tiempo
acelerado, igual que una captura real.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from transcriptor.asr.faster_whisper_engine import FasterWhisperTranscriber
from transcriptor.asr.wer import wer
from transcriptor.audio.sources import FileAudioSource
from transcriptor.audio.vad import SileroDetector
from transcriptor.config.store import ConfigStore
from transcriptor.notes.markdown import get_block, parse_frontmatter
from transcriptor.services import AppServices
from transcriptor.speakers.embedder import WespeakerEmbedder

pytestmark = pytest.mark.slow

TURNS = [
    ("Paulina", "Buenas tardes. Hoy vamos a ver integración por partes, que sirve para integrar productos de funciones."),
    ("Paulina", "La regla LIATE nos dice qué elegir como u: primero logaritmos y después las inversas trigonométricas."),
    ("Eddy (Spanish (Mexico))", "Profesora, ¿eso también aplica cuando hay dos funciones trigonométricas?"),
    ("Paulina", "Sí, en ese caso se aplica dos veces la integración por partes y se despeja la integral."),
    ("Paulina", "La tarea es la hoja de ejercicios cuatro para el próximo jueves. No lo olviden."),
]


def test_real_class_end_to_end(tmp_path: Path, vault: Path) -> None:
    if not shutil.which("say"):
        pytest.skip("Requiere las voces del sistema de macOS")
    parts = [np.zeros(8000, dtype=np.float32)]
    for i, (voice, text) in enumerate(TURNS):
        out = tmp_path / f"{i}.wav"
        subprocess.run(["say", "-v", voice, "-o", str(out), "--data-format=LEI16@16000", text], check=True)
        data, _ = sf.read(str(out), dtype="float32")
        parts += [data, np.zeros(12000, dtype=np.float32)]
    clase = np.concatenate(parts)

    svc = AppServices(ConfigStore(tmp_path / "config.yaml"))
    svc.cfg.general.vault_path = str(vault)
    svc._speaker_embedder = WespeakerEmbedder()
    course = svc.add_course("Cálculo II", "Ing. Pérez", ["LIATE"])
    live_model = FasterWhisperTranscriber("small", "cpu", "int8", live=True)
    svc.start_recording(course.id, "app", sources=[FileAudioSource(samples=clase, realtime=True, speed=4.0)],
                        transcriber=live_model, detector=SileroDetector)
    svc.live.recorder._channels["clase"].source.finished.wait(120)  # type: ignore[union-attr]
    sid = svc.stop_recording(background=False, final_transcriber=FasterWhisperTranscriber("small", "cpu", "int8"))

    session = svc.store.get(sid)  # type: ignore[arg-type]
    assert session and session.status == "done" and session.final_pass_ok
    text = (vault / session.note_path).read_text(encoding="utf-8")
    transcript = get_block(text, "transcripcion") or ""
    plain = " ".join(line.split("** ", 1)[-1].rsplit(" ^", 1)[0].replace("==", "")
                     for line in transcript.splitlines())
    reference = " ".join(t for _, t in TURNS)
    score = wer(reference, plain)
    print(f"\nWER de la nota: {score:.3f}\n{transcript}")
    assert score <= 0.15
    teacher_lines = [line for line in transcript.splitlines() if "Profesor:" in line]
    other_lines = [line for line in transcript.splitlines() if "Participante" in line]
    assert any("dos funciones trigonométricas" in line for line in other_lines)
    assert all("trigonométricas?" not in line for line in teacher_lines)
    fm = parse_frontmatter(text)
    assert "📅" in (get_block(text, "tareas") or "")
    assert fm["curso"] == "[[Clases/Cálculo II/_Curso|Cálculo II]]"
