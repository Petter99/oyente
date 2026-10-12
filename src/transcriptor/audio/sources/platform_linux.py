"""Linux: audio del sistema desde el monitor de PulseAudio/PipeWire (sounddevice)."""

from __future__ import annotations

from typing import Any

import numpy as np

from transcriptor.audio.sources.base import AudioSource, SourceInfo, SourceUnavailableError


class PulseMonitorSource(AudioSource):
    RATE = 48_000

    def __init__(self) -> None:
        super().__init__(SourceInfo("system", "Todo el sistema", "clase"))
        self._stream: Any = None

    def _open(self) -> None:
        import sounddevice as sd

        monitors = [i for i, d in enumerate(sd.query_devices())
                    if "monitor" in d["name"].lower() and d["max_input_channels"] > 0]
        if not monitors:
            raise SourceUnavailableError("No se encontró un dispositivo «monitor» de PulseAudio/PipeWire.")
        self._stream = sd.InputStream(device=monitors[0], channels=1, samplerate=self.RATE,
                                      dtype="float32", callback=self._cb)
        self._stream.start()

    def _cb(self, indata: np.ndarray, frames: int, time: Any, status: Any) -> None:
        self._emit(indata.copy(), self.RATE)

    def _close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
