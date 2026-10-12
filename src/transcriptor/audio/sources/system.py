"""Fábrica de fuentes del equipo según el sistema operativo.

- "system": todo lo que suena (WASAPI loopback / ScreenCaptureKit / monitor de PipeWire).
- "app": solo una aplicación (process loopback de WASAPI / ScreenCaptureKit filtrado).
"""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass

from transcriptor.audio.sources.base import AudioSource, SourceUnavailableError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunningApp:
    id: str  # pid (Windows/Linux) o bundle id (macOS)
    name: str
    playing: bool = False


BROWSERS = ("chrome", "msedge", "edge", "firefox", "safari", "brave", "opera", "arc")
CALL_APPS = ("zoom", "teams", "discord", "webex", "skype", "slack")


def list_audio_apps() -> list[RunningApp]:
    """Aplicaciones candidatas a capturar, con navegadores y apps de llamada primero."""
    system = platform.system()
    try:
        if system == "Windows":
            from transcriptor.audio.sources.platform_windows import list_audio_sessions

            apps = list_audio_sessions()
        elif system == "Darwin":
            from transcriptor.audio.sources.platform_macos import list_shareable_apps

            apps = list_shareable_apps()
        else:
            apps = _list_processes()
    except Exception as exc:
        log.warning("No se pudieron listar las aplicaciones: %s", exc)
        apps = _list_processes()

    def rank(app: RunningApp) -> tuple[int, int, str]:
        low = app.name.lower()
        kind = (
            0 if any(b in low for b in BROWSERS) else 1 if any(c in low for c in CALL_APPS) else 2
        )
        return (0 if app.playing else 1, kind, low)

    return sorted(apps, key=rank)


def _list_processes() -> list[RunningApp]:
    import psutil

    seen: dict[str, RunningApp] = {}
    for proc in psutil.process_iter(["pid", "name"]):
        name = (proc.info.get("name") or "").removesuffix(".exe")
        low = name.lower()
        if any(k in low for k in BROWSERS + CALL_APPS) and low not in seen:
            seen[low] = RunningApp(str(proc.info["pid"]), name)
    return list(seen.values())


def create_system_source() -> AudioSource:
    system = platform.system()
    if system == "Windows":
        from transcriptor.audio.sources.platform_windows import WindowsLoopbackSource

        return WindowsLoopbackSource()
    if system == "Darwin":
        from transcriptor.audio.sources.platform_macos import ScreenCaptureSource

        return ScreenCaptureSource(app=None)
    from transcriptor.audio.sources.platform_linux import PulseMonitorSource

    return PulseMonitorSource()


def create_app_source(app: RunningApp) -> AudioSource:
    system = platform.system()
    if system == "Windows":
        from transcriptor.audio.sources.platform_windows import WindowsProcessLoopbackSource

        return WindowsProcessLoopbackSource(app)
    if system == "Darwin":
        from transcriptor.audio.sources.platform_macos import ScreenCaptureSource

        return ScreenCaptureSource(app=app)
    raise SourceUnavailableError("La captura por aplicación no está disponible en este sistema.")
