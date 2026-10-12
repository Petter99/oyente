"""Ícono en la barra de menús (macOS) / bandeja del sistema (Windows).

Corre en un proceso aparte (AppKit exige el hilo principal) y habla con la app por la API
local protegida con un token. También muestra las notificaciones del sistema.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def notify_os(title: str, message: str, icon: Any = None) -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            safe_t = title.replace('"', "'")
            safe_m = message.replace('"', "'")
            subprocess.run(
                ["osascript", "-e", f'display notification "{safe_m}" with title "{safe_t}"'],
                check=False,
                timeout=5,
            )
        elif icon is not None:
            icon.notify(message, title)
    except Exception:
        log.debug("No se pudo mostrar la notificación")


def run_tray(port: int, token: str, brand: str, icon_path: str, icon_light: str) -> None:
    import httpx
    import pystray
    from PIL import Image

    base = f"http://127.0.0.1:{port}/api"
    client = httpx.Client(timeout=3.0)
    state: dict[str, Any] = {"recording": False, "paused": False, "elapsed": 0, "course": ""}

    def call(action: str) -> None:
        try:
            client.post(f"{base}/accion/{action}", params={"token": token})
        except httpx.HTTPError:
            pass

    def label(_: Any) -> str:
        if not state["recording"]:
            return f"{brand}: sin grabar"
        e = int(state["elapsed"])
        return f"● Grabando {state['course']} · {e // 60:02d}:{e % 60:02d}" + (
            " (en pausa)" if state["paused"] else ""
        )

    menu = pystray.Menu(
        pystray.MenuItem(label, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Abrir", lambda: call("abrir"), default=True),
        pystray.MenuItem(
            "Marcar momento", lambda: call("marcar"), visible=lambda _: state["recording"]
        ),
        pystray.MenuItem(
            lambda _: "Reanudar" if state["paused"] else "Pausar",
            lambda: call("reanudar" if state["paused"] else "pausar"),
            visible=lambda _: state["recording"],
        ),
        pystray.MenuItem(
            "Detener grabación", lambda: call("detener"), visible=lambda _: state["recording"]
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Salir", lambda: (call("salir"), icon.stop())),
    )
    path = icon_path if platform.system() == "Windows" else icon_light
    image = (
        Image.open(path) if Path(path).exists() else Image.new("RGBA", (64, 64), (217, 58, 43, 255))
    )
    icon = pystray.Icon("transcriptor", image, brand, menu)

    def poll() -> None:
        while True:
            try:
                data = client.get(f"{base}/estado", params={"token": token}).json()
                state.update(data)
                for n in data.get("notifications", []):
                    notify_os(n["title"], n["message"], icon)
                icon.update_menu()
            except Exception:
                if not _alive(client, base):
                    icon.stop()
                    return
            time.sleep(2)

    threading.Thread(target=poll, daemon=True).start()
    icon.run()


def _alive(client: Any, base: str) -> bool:
    for _ in range(5):
        try:
            client.get(f"{base}/vivo", timeout=2)
            return True
        except Exception:
            time.sleep(2)
    return False
