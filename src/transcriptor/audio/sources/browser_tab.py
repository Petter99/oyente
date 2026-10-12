"""Audio de UNA pestaña del navegador (Chrome/Edge), recibido por WebSocket local.

Llega desde la página local de captura (getDisplayMedia) o desde la extensión opcional
(chrome.tabCapture). El audio nunca sale del equipo.
"""

from __future__ import annotations

import numpy as np

from transcriptor.audio.sources.base import AudioSource, SourceInfo
from transcriptor.audio.types import SAMPLE_RATE


class BrowserTabSource(AudioSource):
    def __init__(self, label: str = "Pestaña del navegador") -> None:
        super().__init__(SourceInfo("tab", label, "clase"))
        self.connected = False

    def _open(self) -> None:
        pass

    def _close(self) -> None:
        self.connected = False

    def push_pcm(self, payload: bytes, rate: int = SAMPLE_RATE) -> None:
        """PCM float32 little-endian mono."""
        if len(payload) % 4:
            payload = payload[: len(payload) - len(payload) % 4]
        self._emit(np.frombuffer(payload, dtype="<f4").copy(), rate)

    def set_label(self, label: str) -> None:
        self.info = SourceInfo("tab", label or self.info.label, "clase")

    def disconnected(self, reason: str = "La pestaña dejó de compartir audio") -> None:
        self.connected = False
        if self.running:
            self._mark_lost(reason)
