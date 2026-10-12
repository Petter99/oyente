"""Grabación continua a FLAC por canal, alineada con el reloj de la sesión.

El FLAC se escribe bloque a bloque: si la app se cierra de golpe, el audio hasta ese
momento queda legible (T2.3).
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import soundfile as sf

from transcriptor.audio.types import SAMPLE_RATE, Channel, Samples


class ChannelRecorder:
    def __init__(self, path: Path, channel: Channel) -> None:
        self.path = path
        self.channel = channel
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = sf.SoundFile(
            str(path), mode="w", samplerate=SAMPLE_RATE, channels=1, format="FLAC", subtype="PCM_16"
        )
        self._lock = threading.Lock()
        self.samples_written = 0

    @property
    def seconds(self) -> float:
        return self.samples_written / SAMPLE_RATE

    def write(self, samples: Samples) -> float:
        """Escribe y devuelve el tiempo (s) en que empieza este bloque."""
        with self._lock:
            t0 = self.samples_written / SAMPLE_RATE
            self._file.write(np.clip(samples, -1.0, 1.0))
            self.samples_written += samples.size
            self._file.flush()
            return t0

    def pad_to(self, seconds: float) -> None:
        """Rellena con silencio si el canal se quedó atrás (fuente pausada o perdida)."""
        missing = int(seconds * SAMPLE_RATE) - self.samples_written
        if missing > SAMPLE_RATE // 2:
            self.write(np.zeros(missing, dtype=np.float32))

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.close()
