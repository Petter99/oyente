"""Voces sintéticas para pruebas (sin grabaciones reales ni voces con licencia).

Cada «hablante» tiene tono (f0) y tamaño de tracto vocal (escala de formantes) propios,
lo que basta para probar VAD, grabación e identificación de hablantes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SR = 16_000
VOWELS = {  # formantes F1, F2, F3 (Hz) de vocales del español
    "a": (800, 1300, 2500),
    "e": (450, 1900, 2600),
    "i": (300, 2300, 3000),
    "o": (500, 900, 2400),
    "u": (320, 750, 2300),
}


@dataclass(frozen=True)
class Voice:
    f0: float
    formant_scale: float
    tilt: float = 1.0
    seed: int = 0


PROFESOR = Voice(f0=110, formant_scale=0.92, tilt=1.3, seed=1)
ESTUDIANTE = Voice(f0=215, formant_scale=1.15, tilt=0.8, seed=2)
YO = Voice(f0=160, formant_scale=1.03, tilt=1.0, seed=3)


def _resonator(x: np.ndarray, freq: float, bw: float) -> np.ndarray:
    r = np.exp(-np.pi * bw / SR)
    theta = 2 * np.pi * freq / SR
    a1, a2 = -2 * r * np.cos(theta), r * r
    y = np.zeros_like(x)
    for n in range(x.size):
        y[n] = x[n] - a1 * (y[n - 1] if n > 0 else 0) - a2 * (y[n - 2] if n > 1 else 0)
    return y


def syllable(voice: Voice, vowel: str, dur: float, rng: np.random.Generator) -> np.ndarray:
    n = int(dur * SR)
    f0 = voice.f0 * (1 + 0.04 * rng.standard_normal())
    t = np.arange(n) / SR
    phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.03 * np.sin(2 * np.pi * 4 * t))) / SR
    source = np.zeros(n)
    for h in range(1, int(4000 / f0)):
        source += np.sin(h * phase) / h**voice.tilt
    source += 0.02 * rng.standard_normal(n)
    out = np.zeros(n)
    for f, bw in zip(VOWELS[vowel], (80, 100, 120), strict=True):
        out += _resonator(source, f * voice.formant_scale, bw)
    env = np.minimum(1, np.minimum(t / 0.03, (dur - t) / 0.05))
    return out * env


def speech(voice: Voice, seconds: float, seed: int | None = None) -> np.ndarray:
    """Habla sintética continua (sílabas con pausas cortas), normalizada."""
    rng = np.random.default_rng(voice.seed if seed is None else seed)
    parts: list[np.ndarray] = []
    total = 0
    while total < seconds * SR:
        syl = syllable(voice, rng.choice(list(VOWELS)), rng.uniform(0.12, 0.25), rng)
        gap = np.zeros(int(rng.uniform(0.02, 0.08) * SR))
        parts += [syl, gap]
        total += syl.size + gap.size
    x = np.concatenate(parts)[: int(seconds * SR)]
    return (0.3 * x / (np.abs(x).max() + 1e-9)).astype(np.float32)


def silence(seconds: float, noise: float = 0.002, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (noise * rng.standard_normal(int(seconds * SR))).astype(np.float32)


def conversation(
    turns: list[tuple[Voice, float]], pause: float = 1.0
) -> tuple[np.ndarray, list[tuple[float, float, Voice]]]:
    """Concatena turnos de hablantes con pausas. Devuelve audio y la verdad (inicio, fin, voz)."""
    parts = [silence(0.5)]
    truth = []
    t = 0.5
    for i, (voice, dur) in enumerate(turns):
        parts.append(speech(voice, dur, seed=voice.seed * 100 + i))
        truth.append((t, t + dur, voice))
        t += dur
        parts.append(silence(pause, seed=i))
        t += pause
    return np.concatenate(parts), truth
