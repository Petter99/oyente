"""Identificación del profesor con el modelo real de huella de voz (T4.5). Lenta: descarga el modelo.

Las voces son las voces en español del sistema (macOS «say»), una por hablante.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from transcriptor.asr.base import Segment
from transcriptor.speakers import TEACHER, SpeakerIdentifier, SpeakerProfileStore
from transcriptor.speakers.embedder import WespeakerEmbedder

pytestmark = pytest.mark.slow

TURNS = [
    ("Paulina", "Buenas tardes, hoy vamos a estudiar la integración por partes con varios ejemplos."),
    ("Paulina", "Primero elegimos la función u siguiendo la regla de los logaritmos y las inversas."),
    ("Eddy (Spanish (Mexico))", "Profesora, ¿eso también aplica cuando hay dos funciones trigonométricas?"),
    ("Paulina", "Muy buena pregunta, en ese caso se aplica dos veces y se despeja la integral."),
    ("Flo (Spanish (Spain))", "¿Y para el examen hay que memorizar las fórmulas?"),
    ("Paulina", "No hace falta memorizarlas, pero sí entender de dónde salen, eso es lo importante."),
    ("Paulina", "La tarea es la hoja de ejercicios cuatro para el próximo jueves."),
]


@pytest.fixture(scope="module")
def embedder() -> WespeakerEmbedder:
    return WespeakerEmbedder()


def _audio(tmp: Path) -> tuple[list[Segment], list[np.ndarray], list[str]]:
    if not shutil.which("say"):
        pytest.skip("Requiere las voces del sistema de macOS")
    segs, audio, truth = [], [], []
    t = 0.0
    for i, (voice, text) in enumerate(TURNS):
        out = tmp / f"{i}.wav"
        subprocess.run(["say", "-v", voice, "-o", str(out), "--data-format=LEI16@16000", text], check=True)
        data, _ = sf.read(str(out), dtype="float32")
        dur = data.size / 16000
        segs.append(Segment(t, t + dur, text))
        audio.append(data)
        truth.append(TEACHER if voice == "Paulina" else "otro")
        t += dur + 0.5
    return segs, audio, truth


def test_t4_5_teacher_accuracy_with_real_model(tmp_path: Path, embedder: WespeakerEmbedder) -> None:
    segs, audio, truth = _audio(tmp_path)
    ident = SpeakerIdentifier(embedder, SpeakerProfileStore(tmp_path / "perfiles"), "calculo")
    result = ident.label_final(segs, [embedder.embed(a) for a in audio])
    got = [TEACHER if label == TEACHER else "otro" for label in result.labels]
    accuracy = np.mean([a == b for a, b in zip(got, truth, strict=True)])
    assert accuracy >= 0.95, (result.labels, truth)
    assert len({lbl for lbl in result.labels if lbl != TEACHER}) == 2  # dos participantes distintos

    ident.confirm_teacher(result.teacher_embedding)  # type: ignore[arg-type]
    nxt = SpeakerIdentifier(embedder, SpeakerProfileStore(tmp_path / "perfiles"), "calculo")
    live = [nxt.label_live("clase", a)[0] for a in audio]
    assert [TEACHER if x == TEACHER else "otro" for x in live] == truth
