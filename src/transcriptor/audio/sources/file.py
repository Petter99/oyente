"""Fuente desde archivo: usada en pruebas y para importar grabaciones."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from transcriptor.audio.decode import decode_file
from transcriptor.audio.sources.base import AudioSource, SourceInfo
from transcriptor.audio.types import SAMPLE_RATE, Channel


class FileAudioSource(AudioSource):
    def __init__(
        self,
        path: Path | None = None,
        channel: Channel = "clase",
        realtime: bool = False,
        chunk_ms: int = 100,
        samples: np.ndarray | None = None,
        speed: float = 1.0,
        lose_after_s: float | None = None,
    ) -> None:
        super().__init__(SourceInfo("file", path.name if path else "memoria", channel))
        self._data = samples if samples is not None else decode_file(path) if path else np.zeros(0)
        self._data = self._data.astype(np.float32)
        self.realtime = realtime
        self.speed = speed
        self.chunk = int(SAMPLE_RATE * chunk_ms / 1000)
        self.lose_after_s = lose_after_s
        self._thread: threading.Thread | None = None
        self.finished = threading.Event()

    @property
    def duration(self) -> float:
        return self._data.size / SAMPLE_RATE

    def _open(self) -> None:
        self.finished.clear()
        self._thread = threading.Thread(target=self._run, name="file-source", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        pos = 0
        started = time.monotonic()
        block_s = self.chunk / SAMPLE_RATE / self.speed
        sent = 0
        while self.running and pos < self._data.size:
            if self.lose_after_s is not None and pos >= self.lose_after_s * SAMPLE_RATE:
                self._mark_lost("simulado")
                break
            block = self._data[pos : pos + self.chunk]
            self._emit(block, SAMPLE_RATE)
            pos += self.chunk
            if self.realtime:
                # Calendario absoluto: si una pausa se pasa, la siguiente se acorta. Con
                # sleep fijo el exceso se acumulaba (hasta +45 % en un runner de macOS).
                sent += 1
                time.sleep(max(0.0, started + sent * block_s - time.monotonic()))
        self.finished.set()

    def _close(self) -> None:
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
