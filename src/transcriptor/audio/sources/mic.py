"""Micrófono con sounddevice (WASAPI en Windows, CoreAudio en macOS)."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from transcriptor.audio.sources.base import AudioSource, SourceInfo, SourceUnavailableError

log = logging.getLogger(__name__)


def list_input_devices() -> list[dict[str, Any]]:
    import sounddevice as sd

    devices = []
    try:
        default_in = sd.default.device[0]
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                devices.append({"id": idx, "name": dev["name"], "default": idx == default_in})
    except Exception:
        log.exception("No se pudieron listar los micrófonos")
    return devices


class MicSource(AudioSource):
    def __init__(self, device: str | int | None = None) -> None:
        super().__init__(SourceInfo("mic", "Micrófono", "mic"))
        self.device = device
        self._stream: Any = None

    def _open(self) -> None:
        import sounddevice as sd

        try:
            info = sd.query_devices(self.device, "input")
            rate = int(info["default_samplerate"])
            self._stream = sd.InputStream(
                device=self.device, channels=1, samplerate=rate, dtype="float32",
                blocksize=int(rate * 0.1), callback=self._callback,
                finished_callback=self._finished,
            )
            self._stream.start()
            self._rate = rate
        except Exception as exc:
            raise SourceUnavailableError(
                "No se pudo abrir el micrófono. Revisa que esté conectado y que la app tenga permiso."
            ) from exc

    def _callback(self, indata: np.ndarray, frames: int, time: Any, status: Any) -> None:
        if status and status.input_overflow:
            log.debug("Desbordamiento de entrada en el micrófono")
        self._emit(indata.copy(), self._rate)

    def _finished(self) -> None:
        if self.running:
            self._mark_lost("El micrófono se desconectó")

    def _close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
