"""Tipos básicos de audio. Todo el pipeline trabaja en 16 kHz mono float32."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16_000
Channel = Literal["clase", "mic"]
Samples = npt.NDArray[np.float32]


@dataclass
class Utterance:
    """Frase detectada por el VAD, con tiempos relativos al inicio de la sesión."""

    channel: Channel
    start: float
    end: float
    samples: Samples = field(repr=False)

    @property
    def duration(self) -> float:
        return self.end - self.start


def to_mono_16k(data: npt.NDArray[np.floating], rate: int) -> Samples:
    """Convierte audio (frames x canales o 1D) a 16 kHz mono float32."""
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr.mean(axis=1)
    if rate == SAMPLE_RATE or arr.size == 0:
        return arr.astype(np.float32, copy=False)
    n_out = round(arr.size * SAMPLE_RATE / rate)
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    # Filtro paso bajo simple antes de diezmar para evitar aliasing.
    if rate > SAMPLE_RATE:
        width = max(1, round(rate / SAMPLE_RATE))
        kernel = np.ones(width, dtype=np.float32) / width
        arr = np.convolve(arr, kernel, mode="same")
    x_old = np.linspace(0.0, 1.0, num=arr.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, arr).astype(np.float32)


def rms_db(samples: Samples) -> float:
    if samples.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
    return 20.0 * np.log10(max(rms, 1e-6))
