"""Interfaz común de las fuentes de audio.

El resto del pipeline no sabe de dónde viene el audio: micrófono, una aplicación,
todo el sistema, una pestaña del navegador o un archivo (pruebas).
"""

from __future__ import annotations

import logging
import queue
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from transcriptor.audio.types import Channel, Samples, to_mono_16k

log = logging.getLogger(__name__)


class SourceUnavailableError(RuntimeError):
    """La fuente no se puede abrir (sin permisos, app no encontrada, sistema no compatible)."""


@dataclass(frozen=True)
class SourceInfo:
    kind: str  # "app", "tab", "system", "mic", "file"
    label: str  # texto para la interfaz, p. ej. "Google Chrome" o "Pestaña: meet.google.com"
    channel: Channel


class AudioSource(ABC):
    """Fuente que entrega bloques de audio 16 kHz mono por una cola."""

    def __init__(self, info: SourceInfo) -> None:
        self.info = info
        self._queue: queue.Queue[Samples] = queue.Queue(maxsize=2000)
        self._lost_callbacks: list[Callable[[AudioSource, str], None]] = []
        self._running = threading.Event()
        self.lost = False
        self.level_db = -120.0

    # -- ciclo de vida -------------------------------------------------
    def start(self) -> None:
        self.lost = False
        self._running.set()
        self._open()

    def stop(self) -> None:
        self._running.clear()
        try:
            self._close()
        except Exception:
            log.exception("Error al cerrar la fuente %s", self.info.label)

    @property
    def running(self) -> bool:
        return self._running.is_set()

    @abstractmethod
    def _open(self) -> None: ...

    @abstractmethod
    def _close(self) -> None: ...

    # -- datos ----------------------------------------------------------
    def read(self, timeout: float = 0.2) -> Samples | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _emit(self, data: np.ndarray, rate: int) -> None:
        if not self.running:
            return
        samples = to_mono_16k(data, rate)
        if samples.size == 0:
            return
        from transcriptor.audio.types import rms_db

        self.level_db = rms_db(samples)
        try:
            self._queue.put_nowait(samples)
        except queue.Full:
            log.warning("Cola de audio llena en %s; se descarta un bloque", self.info.label)

    # -- fuente perdida -------------------------------------------------
    def on_lost(self, callback: Callable[[AudioSource, str], None]) -> None:
        self._lost_callbacks.append(callback)

    def _mark_lost(self, reason: str) -> None:
        if self.lost:
            return
        self.lost = True
        log.warning("Fuente perdida (%s): %s", self.info.label, reason)
        for cb in self._lost_callbacks:
            try:
                cb(self, reason)
            except Exception:
                log.exception("Error en callback de fuente perdida")
