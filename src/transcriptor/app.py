"""Punto de entrada: ventana nativa (pywebview) con la interfaz NiceGUI.

Uso:  oyente            → ventana de escritorio
      oyente --browser  → en el navegador (diagnóstico)
"""

from __future__ import annotations

import argparse
import functools
import logging
import multiprocessing
import secrets
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from transcriptor.branding import branding
from transcriptor.logging_setup import setup_logging

log = logging.getLogger("transcriptor")

PORTS = (8765, 8766, 8767)  # la extensión del navegador busca la app en estos puertos


def assets_dir() -> Path:
    """Íconos: incluidos en el instalador (PyInstaller) o en assets/branding del repositorio."""
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "assets" / "branding"
    if bundled.exists():
        return bundled
    return Path(__file__).resolve().parents[2] / "assets" / "branding"


def free_port() -> int:
    for port in PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def build(svc: object, port: int, control_token: str) -> None:
    """Registra páginas, API local y estáticos en la app NiceGUI."""
    from fastapi import HTTPException
    from nicegui import app, ui

    from transcriptor.capture_web.routes import register
    from transcriptor.services import AppServices
    from transcriptor.ui import onboarding, pages_chat, pages_main, pages_settings, recording
    from transcriptor.ui.theme import head_html

    assert isinstance(svc, AppServices)
    svc.hub.port = port
    register(app, svc.hub)
    app.add_static_files("/branding", str(assets_dir()))
    ui.add_head_html(head_html(), shared=True)
    os_notices: deque[dict[str, str]] = deque(maxlen=20)

    def guard(page):  # type: ignore[no-untyped-def]
        """Si falta la configuración inicial, se muestra el asistente."""

        @functools.wraps(page)
        def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
            _theme(svc)
            if svc.onboarding_needed():
                ui.navigate.to("/bienvenida")
                return None
            return page(*args, **kwargs)

        return wrapper

    @ui.page("/")
    @guard
    def home() -> None:
        pages_main.home_page(svc)

    @ui.page("/clases")
    @guard
    def classes() -> None:
        pages_main.classes_page(svc)

    @ui.page("/clase/{session_id}")
    @guard
    def class_detail(session_id: str) -> None:
        pages_main.class_page(svc, session_id)

    @ui.page("/chat")
    @guard
    def chat(q: str = "") -> None:
        pages_chat.chat_page(svc, q)

    @ui.page("/cursos")
    @guard
    def courses() -> None:
        pages_chat.courses_page(svc)

    @ui.page("/configuracion")
    @guard
    def settings(tab: str = "general") -> None:
        pages_settings.settings_page(svc, tab)

    @ui.page("/grabando")
    @guard
    def recording_view() -> None:
        recording.recording_page(svc)

    @ui.page("/bienvenida")
    def welcome() -> None:
        _theme(svc)
        onboarding.onboarding_page(svc)

    # -------------------------------------------------- API local para la bandeja / barra de menús
    def check(token: str) -> None:
        if not secrets.compare_digest(token, control_token):
            raise HTTPException(status_code=403)

    @app.get("/api/vivo")
    def alive() -> dict:
        return {"ok": True}

    @app.get("/api/estado")
    def status(token: str = "") -> dict:
        check(token)
        rec = svc.live.recorder
        notes = list(os_notices)
        os_notices.clear()
        return {
            "recording": rec is not None,
            "paused": bool(rec and rec.paused),
            "elapsed": rec.elapsed if rec else 0,
            "course": svc.live.session.course_name if svc.live.session else "",
            "notifications": notes,
        }

    @app.post("/api/accion/{action}")
    def action(action: str, token: str = "") -> dict:
        check(token)
        if action == "pausar":
            svc.pause()
        elif action == "reanudar":
            svc.resume()
        elif action == "marcar":
            svc.mark()
        elif action == "detener":
            threading.Thread(target=svc.stop_recording, daemon=True).start()
        elif action == "abrir":
            _show_window()
        elif action == "salir":
            app.shutdown()
        return {"ok": True}

    # -------------------------------------------------- avisos de clase y notificaciones
    def scheduler() -> None:
        notified: set[str] = set()
        while True:
            try:
                g = svc.cfg.google
                if g.connected and g.read_schedule:
                    for up in svc.calendar_service().upcoming_classes(
                        within_min=max(1, g.notify_before_min)
                    ):
                        key = f"{up.course.id}-{up.start:%Y%m%d%H%M}"
                        if key in notified:
                            continue
                        notified.add(key)
                        minutes = max(
                            0, int((up.start - datetime.now().astimezone()).total_seconds() // 60)
                        )
                        os_notices.append(
                            {
                                "title": branding().nombre,
                                "message": f"{up.course.name} empieza en {minutes} min. ¿Grabar?",
                            }
                        )
                        svc.notices.append(("clase", up.course.id))
                svc.sync_google_tasks()
                svc.check_batches()
            except Exception:
                log.debug("Error revisando el horario", exc_info=True)
            time.sleep(60)

    def on_finished(event: object) -> None:
        if getattr(event, "type", "") == "finalize_finished":
            data = getattr(event, "data", {})
            msg = (
                "La nota de la clase está lista."
                if not data.get("error")
                else "La clase se guardó con errores."
            )
            os_notices.append({"title": branding().nombre, "message": msg})

    svc.bus.subscribe(on_finished)
    app.on_startup(lambda: threading.Thread(target=scheduler, daemon=True, name="horario").start())


def _show_window() -> None:
    try:
        from nicegui import app

        if app.native.main_window is not None:
            app.native.main_window.show()
            app.native.main_window.restore()
    except Exception:
        pass


def _theme(svc: object) -> None:
    from nicegui import ui

    from transcriptor.services import AppServices
    from transcriptor.ui.theme import PLATFORM

    assert isinstance(svc, AppServices)
    theme = svc.cfg.ui.theme
    ui.dark_mode(None if theme == "system" else theme == "dark")
    ui.colors(primary="#0A6CDB" if PLATFORM == "mac" else "#005FB8", negative="#D92D3A")


def main(argv: list[str] | None = None) -> None:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(prog=branding().ejecutable)
    parser.add_argument(
        "--browser", action="store_true", help="abrir en el navegador en lugar de una ventana"
    )
    parser.add_argument(
        "--no-tray", action="store_true", help="sin ícono en la bandeja / barra de menús"
    )
    parser.add_argument("--headless", action="store_true", help="solo el servidor local (pruebas)")
    args = parser.parse_args(argv)

    setup_logging()
    from nicegui import app, ui

    from transcriptor.services import AppServices

    svc = AppServices()
    port = free_port()
    token = secrets.token_urlsafe(24)
    build(svc, port, token)

    def startup() -> None:
        svc.startup_maintenance()
        if not svc.onboarding_needed():
            svc.warm_up_models()
            threading.Thread(target=svc.recover_interrupted, daemon=True, name="recuperar").start()

    app.on_startup(startup)

    if args.headless:
        args.browser = True
        args.no_tray = True
    if not args.no_tray:
        icons = assets_dir()
        from transcriptor.ui.tray import run_tray

        proc = multiprocessing.Process(
            target=run_tray,
            daemon=True,
            args=(
                port,
                token,
                branding().nombre,
                str(icons / "tray.png"),
                str(icons / "tray-light.png"),
            ),
        )
        app.on_startup(proc.start)

    def shutdown() -> None:
        if svc.recording:
            # Cerrar la ventana a mitad de clase no pierde nada: se cierra la clase correctamente.
            svc.stop_recording(background=False)

    app.on_shutdown(shutdown)
    if not args.browser:
        app.native.window_args["min_size"] = (980, 640)
        app.native.window_args["text_select"] = True
    ui.run(
        title=branding().nombre,
        port=port,
        host="127.0.0.1",
        native=not args.browser,
        window_size=(1280, 820) if not args.browser else None,
        reload=False,
        dark=None,
        favicon=str(assets_dir() / "icon.png"),
        language="es",
        show_welcome_message=False,
        show=not args.headless,
        storage_secret=secrets.token_hex(16),
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
