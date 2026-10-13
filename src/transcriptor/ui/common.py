"""Marco común (barra lateral + barra superior) y componentes reutilizables."""

from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from nicegui import ui

from transcriptor.asr.base import format_ts
from transcriptor.services import AppServices
from transcriptor.ui.theme import MOD, course_color

NAV = [
    ("inicio", "Inicio", "home", "/"),
    ("clases", "Clases", "library_books", "/clases"),
    ("chat", "Chat", "forum", "/chat"),
    ("cursos", "Cursos", "school", "/cursos"),
]
DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]


def nice_date(d: date | str | None, with_weekday: bool = True) -> str:
    if d is None or d == "":
        return ""
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    base = f"{d.day} {MONTHS[d.month - 1][:3]}"
    return f"{DAYS[d.weekday()][:3]} {base}" if with_weekday else base


def long_date(d: date) -> str:
    return f"{DAYS[d.weekday()].capitalize()} {d.day} de {MONTHS[d.month - 1]}"


def duration_text(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def open_external(url: str) -> None:
    """Abre un enlace (web u obsidian://) con la aplicación del sistema."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", url])
        elif system == "Windows":
            os.startfile(url)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception:
        import webbrowser

        webbrowser.open(url)


def obsidian_uri(vault_root: Path, note_rel: str) -> str:
    return "obsidian://open?path=" + quote((vault_root / note_rel).as_posix(), safe="")


def open_path(path: Path) -> None:
    """Abre un archivo con la aplicación predeterminada del sistema."""
    system = platform.system()
    if system == "Darwin":
        subprocess.Popen(["open", str(path)])
    elif system == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def open_note(svc: AppServices, note_rel: str) -> None:
    """Abre una nota: en Obsidian si se puede; si no, con el programa del sistema.

    Nunca falla en silencio: si la nota no existe o no hay con qué abrirla, avisa.
    """
    path = svc.vault_root / note_rel
    if not path.exists():
        ui.notify("No se encontró la nota en la carpeta", type="warning")
        return
    try:
        if svc.can_open_in_obsidian:
            open_external(obsidian_uri(svc.vault_root, note_rel))
        else:
            open_path(path)
    except Exception as exc:
        ui.notify(f"No se pudo abrir la nota: {exc}", type="negative")


def reveal_in_folder(path: Path) -> None:
    if not path.exists():
        ui.notify("El archivo ya no está en esa carpeta", type="warning")
        return
    system = platform.system()
    if system == "Darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif system == "Windows":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


# ------------------------------------------------------------------ componentes
def speaker_label(name: str) -> None:
    kind = "profesor" if name == "Profesor" else "yo" if name == "Yo" else "participante"
    ui.html(f'<span class="speaker {kind}">{name or "—"}</span>', sanitize=False)


def banner(kind: str, text: str, icon: str = "info") -> ui.element:
    with ui.element("div").classes(f"banner {kind}") as el:
        ui.icon(icon).style("font-size:20px")
        ui.markdown(text).classes("flex-1").style("margin:0")
    return el


def pill(text: str, kind: str = "") -> None:
    ui.html(f'<span class="pill {kind}">{text}</span>', sanitize=False)


def course_bar(course: str | None) -> None:
    ui.element("span").classes("course-bar").style(f"background:{course_color(course or '')}")


def highlight_doubts(text: str) -> str:
    """==palabra== → <mark> (palabras de baja confianza)."""
    import html
    import re

    safe = html.escape(text)
    return re.sub(r"==(.+?)==", r'<mark class="doubt">\1</mark>', safe)


def ts(seconds: float | None) -> str:
    if seconds is None:
        return ""
    stamp = format_ts(seconds)
    return stamp[3:] if stamp.startswith("00:") else stamp


# ------------------------------------------------------------------ marco
@contextmanager
def frame(
    svc: AppServices,
    active: str,
    title: str,
    subtitle: str = "",
    show_search: bool = True,
    actions: Callable[[], Any] | None = None,
):  # type: ignore[no-untyped-def]
    """Ventana principal con barra lateral. El contenido se escribe dentro del ``with``."""
    from transcriptor.ui.recording import open_new_recording

    ui.page_title(f"{title} · {svc.brand()}")
    _shortcuts(svc)
    with ui.element("div").classes("app-shell"):
        with ui.element("nav").classes("sidebar").props('aria-label="Navegación principal"'):
            with ui.element("div").classes("brand"):
                ui.image("/branding/icon.png").style("width:22px;height:22px")
                ui.label(svc.brand())
            for key, label, icon, path in NAV:
                _nav_item(label, icon, path, key == active)
            if svc.recording:
                with (
                    ui.element("div")
                    .classes("nav-item")
                    .style("color:var(--grabar);font-weight:600")
                    .on("click", lambda: ui.navigate.to("/grabando"))
                ):
                    ui.html(
                        '<span class="rec-dot" style="background:var(--grabar)"></span>',
                        sanitize=False,
                    )
                    ui.label("Grabando…")
            ui.element("div").style("flex:1")
            _status_card(svc)
            _nav_item("Configuración", "settings", "/configuracion", active == "configuracion")
        with ui.element("main").classes("main"):
            with ui.element("header").classes("topbar"):
                with ui.column().classes("gap-0 flex-1"):
                    ui.html(f"<h1>{title}</h1>", sanitize=False)
                    if subtitle:
                        ui.label(subtitle).classes("sub")
                if actions:
                    actions()
                if show_search:
                    search = (
                        ui.input(placeholder="Buscar en tus clases")
                        .props('dense outlined rounded clearable aria-label="Buscar en tus clases"')
                        .style("width:280px")
                    )
                    search.on(
                        "keydown.enter",
                        lambda: ui.navigate.to(f"/chat?q={quote(search.value or '')}"),
                    )
                if svc.recording:
                    ui.button("Grabando", on_click=lambda: ui.navigate.to("/grabando")).classes(
                        "btn-rec"
                    )
                else:
                    with (
                        ui.button(on_click=lambda: open_new_recording(svc))
                        .classes("btn-rec")
                        .props(f'aria-label="Grabar ({MOD}+Alt+R)"')
                    ):
                        ui.html('<span class="rec-dot"></span>', sanitize=False)
                        ui.label("Grabar").classes("q-ml-sm")
            with ui.element("div").classes("content") as content:
                yield content


def _nav_item(label: str, icon: str, path: str, active: bool) -> None:
    with (
        ui.element("a")
        .classes("nav-item" + (" active" if active else ""))
        .props(f'href="{path}"' + (' aria-current="page"' if active else ""))
    ):
        ui.icon(icon)
        ui.label(label)


def _status_card(svc: AppServices) -> None:
    with ui.element("div").classes("status-card"):
        status = svc.models_status
        if status == "cargando":
            ui.label("Preparando el modelo de voz…")
            ui.linear_progress(show_value=False).props("indeterminate size=4px")
        elif status.startswith("error"):
            ui.label("No se pudo preparar el modelo de voz").style("color:var(--grabar)")
        if svc.ai_available:
            ui.label(
                f"IA: {svc.cfg.ai.provider.capitalize()} · {svc.tokens_this_month():,} tokens este mes".replace(
                    ",", "."
                )
            )
        else:
            ui.label("Modo local (sin IA)").classes("muted")


def _shortcuts(svc: AppServices) -> None:
    from transcriptor.ui.recording import open_new_recording

    def handle(e: Any) -> None:
        if not e.action.keydown:
            return
        mod = e.modifiers.meta or e.modifiers.ctrl
        if mod and e.key == ",":
            ui.navigate.to("/configuracion")
        elif mod and e.modifiers.alt and str(e.key).lower() in ("r", "®"):
            if svc.recording:
                ui.navigate.to("/grabando")
            else:
                open_new_recording(svc)
        elif mod and str(e.key) in ("1", "2", "3", "4"):
            ui.navigate.to(NAV[int(str(e.key)) - 1][3])

    ui.keyboard(on_key=handle)


def empty_state(icon: str, title: str, text: str) -> None:
    with ui.column().classes("items-center justify-center w-full q-pa-xl gap-2"):
        ui.icon(icon).style("font-size:40px;color:var(--texto-3)")
        ui.label(title).style("font-size:15px;font-weight:700")
        ui.label(text).classes("muted").style("max-width:420px;text-align:center")


def now_text() -> str:
    return long_date(datetime.now().date())
