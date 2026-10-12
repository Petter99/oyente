"""Calibra el umbral de «misma persona» del extractor de voz (benchmark T4.5).

Usa voces en español del sistema (macOS «say») como hablantes distintos y muestra la similitud
mínima entre frases de la misma voz y la máxima entre voces distintas.

Uso:  uv run python scripts/calibrate_speakers.py [wespeaker|spectral]
"""

from __future__ import annotations

import itertools
import subprocess
import sys
import tempfile
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from transcriptor.speakers.embedder import SpectralEmbedder, WespeakerEmbedder, cosine  # noqa: E402

TEXTS = [
    "Hoy vamos a ver integrales por partes y la regla de los logaritmos.",
    "La tarea es la hoja cuatro para el jueves, no lo olviden.",
    "Si tienen dudas pregunten al final de la clase, por favor.",
    "Ahora derivamos el producto y simplificamos la expresión.",
]
VOICES = ["Paulina", "Mónica", "Eddy (Spanish (Mexico))", "Flo (Spanish (Spain))"]


def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 else "wespeaker"
    emb = WespeakerEmbedder() if kind == "wespeaker" else SpectralEmbedder()
    tmp = Path(tempfile.mkdtemp())
    vecs: dict[str, list] = {}
    for v in VOICES:
        vecs[v] = []
        for i, text in enumerate(TEXTS):
            out = tmp / f"{abs(hash(v))}-{i}.wav"
            subprocess.run(["say", "-v", v, "-o", str(out), "--data-format=LEI16@16000", text], check=True)
            audio, _ = sf.read(str(out), dtype="float32")
            vecs[v].append(emb.embed(audio))
    same, diff = [], []
    for a, b in itertools.combinations_with_replacement(VOICES, 2):
        sims = [cosine(x, y) for x in vecs[a] for y in vecs[b] if x is not y]
        (same if a == b else diff).extend(sims)
        print(f"{a[:8]:8} {b[:8]:8} min={min(sims):.2f} max={max(sims):.2f}")
    print(f"\n{emb.name}: misma voz ≥ {min(same):.2f} · voces distintas ≤ {max(diff):.2f} · "
          f"umbral actual {emb.default_threshold}")


if __name__ == "__main__":
    main()
