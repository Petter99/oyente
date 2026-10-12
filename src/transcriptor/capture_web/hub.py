"""Puente local entre el navegador (página de captura o extensión) y la app.

Seguridad (T11.1): solo conexiones desde este equipo (127.0.0.1 / ::1), desde la propia
página local o desde la extensión con su ID fijo, y con un token por sesión de captura.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from transcriptor.audio.sources.browser_tab import BrowserTabSource

log = logging.getLogger(__name__)

# ID fijo de la extensión (derivado de la clave pública en browser-extension/manifest.json).
EXTENSION_IDS: set[str] = {"jkipccmbgjbgagedjgngohiahaoeicmm"}
EXTENSION_VERSION = "1.0.0"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


@dataclass
class ExtensionInfo:
    connected: bool = False
    version: str = ""
    browser: str = ""


@dataclass
class TabCaptureHub:
    port: int = 0
    token: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    source: BrowserTabSource | None = None
    extension: ExtensionInfo = field(default_factory=ExtensionInfo)
    expected_extension_version: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _listeners: list[Callable[[str, dict[str, Any]], None]] = field(default_factory=list)

    def new_token(self) -> str:
        self.token = secrets.token_urlsafe(16)
        return self.token

    def capture_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/captura?token={self.token}"

    def attach(self, source: BrowserTabSource) -> None:
        with self._lock:
            self.source = source

    def detach(self) -> None:
        with self._lock:
            self.source = None

    def on_event(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    def emit(self, kind: str, **data: Any) -> None:
        for listener in self._listeners:
            try:
                listener(kind, data)
            except Exception:
                log.exception("Error en oyente del puente de captura")

    # ------------------------------------------------------------------ validación
    def allowed(
        self,
        client_host: str | None,
        origin: str | None,
        token: str | None,
        require_token: bool = True,
    ) -> bool:
        if (client_host or "") not in LOCAL_HOSTS:
            return False
        origin = origin or ""
        from_page = origin in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}", "")
        from_ext = origin.startswith("chrome-extension://") and (
            not EXTENSION_IDS or origin.split("//", 1)[1].strip("/") in EXTENSION_IDS
        )
        if not (from_page or from_ext):
            return False
        if require_token and not from_ext:
            return bool(token) and secrets.compare_digest(token or "", self.token)
        return True

    # ------------------------------------------------------------------ mensajes
    def handle_text(self, text: str, via: str) -> None:
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            return
        kind = msg.get("type")
        if kind == "hello" and via == "extension":
            self.extension = ExtensionInfo(
                True, str(msg.get("version", "")), str(msg.get("browser", ""))
            )
            self.emit(
                "extension",
                connected=True,
                version=self.extension.version,
                outdated=bool(
                    self.expected_extension_version
                    and self.extension.version != self.expected_extension_version
                ),
            )
        elif kind == "start":
            with self._lock:
                src = self.source
            if src is not None:
                src.set_label(f"Pestaña: {msg.get('label') or 'navegador'}")
                src.connected = True
            self.emit("tab_started", label=msg.get("label", ""))
        elif kind == "stop":
            with self._lock:
                src = self.source
            if src is not None:
                src.disconnected("Se dejó de compartir la pestaña")
            self.emit("tab_stopped")

    def handle_audio(self, payload: bytes, rate: int = 16_000) -> bool:
        with self._lock:
            src = self.source
        if src is None or not src.running:
            return False
        src.push_pcm(payload, rate)
        return True

    def handle_closed(self, via: str) -> None:
        if via == "extension-control":
            self.extension = ExtensionInfo()
            self.emit("extension", connected=False)
            return
        with self._lock:
            src = self.source
        if src is not None and src.connected:
            src.disconnected()
            self.emit("tab_stopped")
