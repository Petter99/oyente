"""Hoja «Nueva grabación» (elegir fuente) y vista «Grabando» (modo enfoque)."""

from __future__ import annotations

import logging
import time
from typing import Any

from nicegui import run, ui

from transcriptor.audio.decode import estimate_mb
from transcriptor.audio.sources.base import SourceUnavailableError
from transcriptor.audio.sources.system import BROWSERS, RunningApp, list_audio_apps
from transcriptor.services import AppServices
from transcriptor.ui.common import banner, open_external, speaker_label, ts
from transcriptor.ui.theme import MOD, PLATFORM

log = logging.getLogger(__name__)

SOURCES = [
    (
        "app",
        "apps",
        "Solo una aplicación",
        "Recomendado. Captura solo el navegador o la app de la llamada "
        "(Zoom, Teams, Meet en Chrome…). Funciona con cualquier navegador.",
    ),
    (
        "tab",
        "tab",
        "Una pestaña del navegador",
        "Solo el audio de una pestaña (Chrome o Edge). Lo más limpio.",
    ),
    (
        "system",
        "volume_up",
        "Todo el sistema",
        "Todo lo que suena en el equipo (también notificaciones y música).",
    ),
    ("mic", "mic", "Solo micrófono", "Para clases presenciales o una grabadora externa."),
]
RETENTION_TEXT = {"always": "se guarda siempre", "never": "se borra al terminar"}


def open_new_recording(svc: AppServices) -> None:
    if not svc.cfg.courses:
        ui.notify("Primero crea un curso en «Cursos».", type="warning")
        ui.navigate.to("/cursos")
        return
    state: dict[str, Any] = {
        "kind": svc.cfg.audio.default_source,
        "course": svc.cfg.courses[0].id,
        "mic": svc.cfg.audio.include_mic,
        "app": None,
        "apps": [],
    }
    last = max(svc.cfg.courses, key=lambda c: c.last_source is not None)
    state["course"] = last.id
    if last.last_source:
        state["kind"] = last.last_source

    with ui.dialog() as dialog, ui.card().style("width:640px;max-width:95vw;padding:24px"):
        ui.label("Nueva grabación").style("font-size:17px;font-weight:700")
        ui.select(
            {c.id: c.name for c in svc.cfg.courses},
            label="Curso",
            value=state["course"],
            on_change=lambda e: state.update(course=e.value),
        ).classes("w-full")
        ui.label("¿De dónde tomamos el audio de la clase?").classes("q-mt-md").style(
            "font-weight:600"
        )

        @ui.refreshable
        def sources() -> None:
            for key, icon, title, text in SOURCES:
                selected = state["kind"] == key
                with (
                    ui.element("div")
                    .classes("source-option w-full" + (" selected" if selected else ""))
                    .on(
                        "click",
                        lambda k=key: (state.update(kind=k), sources.refresh(), apps.refresh()),
                    )
                ):
                    ui.icon(icon).style("font-size:22px;color:var(--acento)")
                    with ui.column().classes("gap-0 flex-1"):
                        ui.label(title).style("font-weight:600")
                        ui.label(text).classes("muted small")

        sources()

        @ui.refreshable
        def apps() -> None:
            if state["kind"] == "app":
                options = {
                    a.id: f"{a.name}{' · sonando' if a.playing else ''}" for a in state["apps"]
                }
                if not options:
                    ui.label(
                        "Buscando aplicaciones abiertas…"
                        if state.get("loading")
                        else "No se encontraron aplicaciones. Abre la llamada y pulsa «Actualizar»."
                    ).classes("muted small")
                else:
                    preferred = next(
                        (a.id for a in state["apps"] if a.name == svc.cfg.audio.preferred_app), None
                    )
                    default = preferred or next(iter(options))
                    if state["app"] is None or state["app"] not in options:
                        state["app"] = default
                    ui.select(
                        options,
                        label="Aplicación",
                        value=state["app"],
                        on_change=lambda e: state.update(app=e.value),
                    ).classes("w-full")
                ui.button("Actualizar lista", icon="refresh", on_click=lambda: load_apps()).props(
                    "flat dense"
                )
            elif state["kind"] == "tab":
                ext = svc.hub.extension
                if ext.connected:
                    banner(
                        "ok",
                        "Extensión conectada ✔. Al empezar, pulsa su ícono en la pestaña de la clase.",
                        "check_circle",
                    )
                else:
                    ui.label(
                        "Al empezar se abrirá una página en Chrome/Edge para elegir la pestaña."
                    ).classes("muted small")
                    if not svc.cfg.audio.extension_hint_dismissed:
                        with banner(
                            "info",
                            "**Opcional:** con la extensión capturas la pestaña con un clic.",
                            "extension",
                        ):
                            ui.button(
                                "Instalar",
                                on_click=lambda: ui.navigate.to("/configuracion?tab=audio"),
                            ).props("flat dense")
                            ui.button("Ahora no", on_click=lambda: None).props("flat dense")

                            def never() -> None:
                                svc.cfg.audio.extension_hint_dismissed = True
                                svc.save_config()
                                apps.refresh()

                            ui.button("No volver a mostrar", on_click=never).props("flat dense")

        apps()

        if state["kind"] != "mic":
            ui.switch(
                "Incluir mi micrófono (tu voz se etiqueta como «Yo»)",
                value=state["mic"],
                on_change=lambda e: state.update(mic=e.value),
            ).classes("q-mt-sm")

        fmt = svc.cfg.audio.format
        retention = svc.cfg.audio.retention
        keep = RETENTION_TEXT.get(retention.mode, f"se guarda {retention.days} días")
        channels = 2 if state["mic"] else 1
        with ui.row().classes("items-center q-mt-sm gap-1"):
            ui.label(
                f"Audio: {fmt.upper()} (≈ {estimate_mb(fmt, 1, channels):.0f} MB por hora), {keep}."
            ).classes("muted small")
            ui.link("Cambiar", "/configuracion?tab=audio").classes("small")

        with ui.row().classes("w-full justify-end q-mt-md gap-2"):
            ui.button("Cancelar", on_click=dialog.close).classes("btn-plain")
            start_btn = ui.button("Empezar a grabar", on_click=lambda: start()).classes("btn-rec")

    async def load_apps() -> None:
        state["loading"] = True
        apps.refresh()
        try:
            state["apps"] = await run.io_bound(list_audio_apps)
        except Exception:
            state["apps"] = []
        state["loading"] = False
        apps.refresh()

    async def start() -> None:
        kind = state["kind"]
        app = (
            next((a for a in state["apps"] if a.id == state["app"]), None)
            if kind == "app"
            else None
        )
        if kind == "app" and app is None:
            ui.notify("Elige la aplicación de la llamada.", type="warning")
            return
        start_btn.props("loading")
        try:
            await run.io_bound(
                svc.start_recording,
                state["course"],
                kind,
                app,
                state["mic"] if kind != "mic" else False,
            )
        except SourceUnavailableError as exc:
            start_btn.props(remove="loading")
            ui.notify(str(exc), type="negative", multi_line=True, close_button="Cerrar")
            return
        except Exception as exc:
            log.exception("No se pudo empezar a grabar")
            start_btn.props(remove="loading")
            ui.notify(f"No se pudo empezar a grabar: {exc}", type="negative", multi_line=True)
            return
        if app is not None:
            svc.cfg.audio.preferred_app = app.name
            svc.save_config()
        if kind == "tab" and not svc.hub.extension.connected:
            open_external(svc.hub.capture_url())
        dialog.close()
        ui.navigate.to("/grabando")

    dialog.open()
    if state["kind"] == "app":
        ui.timer(0.1, load_apps, once=True)


def is_browser(app: RunningApp) -> bool:
    return any(b in app.name.lower() for b in BROWSERS)


# ------------------------------------------------------------------ vista «Grabando»
def recording_page(svc: AppServices) -> None:
    if not svc.recording:
        ui.navigate.to("/")
        return
    live = svc.live
    session = live.session
    assert session is not None
    ui.page_title(f"Grabando · {session.course_name}")
    seen = {"version": -1, "count": 0, "insights": 0}

    with ui.element("div").classes("app-shell"):
        with ui.element("main").classes("main"):
            with ui.element("header").classes("topbar").style("height:64px"):
                ui.html(
                    '<span class="rec-dot" style="background:var(--grabar);width:12px;height:12px"></span>',
                    sanitize=False,
                )
                with ui.column().classes("gap-0"):
                    ui.label(session.course_name).style("font-weight:700;font-size:15px")
                    source = ui.label(
                        f"Audio: {session.source_label}"
                        + (" + tu micrófono" if session.include_mic else "")
                    ).classes("sub")
                timer = (
                    ui.label("00:00")
                    .classes("tnum")
                    .style("font-size:22px;font-weight:700;margin-left:16px")
                )
                with ui.row().classes("items-center gap-2 q-ml-md"):
                    ui.label("Clase").classes("muted small")
                    clase_meter = ui.html(
                        '<div class="meter"><div style="width:0"></div></div>', sanitize=False
                    )
                    if session.include_mic:
                        ui.label("Yo").classes("muted small")
                        mic_meter = ui.html(
                            '<div class="meter"><div style="width:0"></div></div>', sanitize=False
                        )
                    else:
                        mic_meter = None
                ui.element("div").style("flex:1")
                ui.button(
                    "Marcar momento",
                    icon="bookmark_add",
                    on_click=lambda: (svc.mark(), ui.notify("Momento marcado")),
                ).classes("btn-plain")
                pause_btn = ui.button(
                    "Pausar", icon="pause", on_click=lambda: toggle_pause()
                ).classes("btn-plain")
                ui.button("Detener", icon="stop", on_click=lambda: stop()).classes("btn-rec")
            with ui.element("div").style("flex:1;min-height:0;display:flex"):
                with ui.element("section").style(
                    "flex:1;min-width:0;display:flex;flex-direction:column"
                ):
                    lost_area = ui.column().classes("w-full q-px-lg q-pt-md")
                    with ui.scroll_area().classes("flex-1 q-px-lg") as scroller:
                        transcript = ui.column().classes("w-full gap-0")
                        with transcript:
                            waiting = ui.label(
                                "Escuchando… el texto aparecerá aquí en cuanto alguien hable."
                            ).classes("muted q-pa-md")
                    status = ui.label("").classes("muted small q-px-lg q-pb-sm")
                with ui.element("aside").style(
                    "width:320px;border-left:1px solid var(--borde);display:flex;flex-direction:column;"
                    "background:var(--fondo-suave)"
                ):
                    with ui.row().classes("items-center q-pa-md gap-2"):
                        ui.label("Sugerencias").style("font-weight:700;font-size:15px").classes(
                            "flex-1"
                        )
                        if svc.ai_available:
                            ui.switch(
                                value=live.assistant is not None and live.assistant.enabled,
                                on_change=lambda e: (
                                    live.assistant and live.assistant.set_enabled(e.value)
                                ),
                            ).props('aria-label="Sugerencias durante la clase"')
                    with ui.scroll_area().classes("flex-1 q-px-md"):
                        side = ui.column().classes("w-full gap-3")
                        with side:
                            if not svc.ai_available:
                                ui.label(
                                    "Las sugerencias necesitan IA. La transcripción funciona igual."
                                ).classes("muted small")
                            else:
                                ui.label(
                                    "Aparecerán aquí tareas, puntos importantes y preguntas útiles."
                                ).classes("muted small")
                        related_box = ui.column().classes("w-full gap-1 q-mt-md")

    def toggle_pause() -> None:
        if svc.live.recorder and svc.live.recorder.paused:
            svc.resume()
            pause_btn.set_text("Pausar")
            pause_btn.props("icon=pause")
        else:
            svc.pause()
            pause_btn.set_text("Reanudar")
            pause_btn.props("icon=play_arrow")

    async def stop() -> None:
        with ui.dialog() as confirm, ui.card():
            ui.label("¿Terminar la clase?").style("font-weight:700")
            ui.label(
                "Se hará la transcripción final de alta fidelidad y se creará la nota en Obsidian."
            ).classes("muted")
            with ui.row().classes("justify-end w-full"):
                ui.button("Seguir grabando", on_click=lambda: confirm.submit(False)).classes(
                    "btn-plain"
                )
                ui.button("Terminar", on_click=lambda: confirm.submit(True)).classes("btn-rec")
        if not await confirm:
            return
        sid = await run.io_bound(svc.stop_recording)
        if sid is not None:  # None: otra llamada ya la está deteniendo
            ui.navigate.to(f"/clase/{sid}")

    def change_source(kind: str) -> None:
        try:
            svc.switch_source(kind)  # type: ignore[arg-type]
            if kind == "tab" and not svc.hub.extension.connected:
                open_external(svc.hub.capture_url())
        except Exception as exc:
            ui.notify(f"No se pudo cambiar de fuente: {exc}", type="negative")

    def meter_html(db: float) -> str:
        width = max(0, min(100, (db + 60) * 100 / 60))
        return f'<div class="meter"><div style="width:{width:.0f}%"></div></div>'

    def tick() -> None:
        if not svc.recording:
            return
        rec = svc.live.recorder
        assert rec is not None
        elapsed = int(rec.elapsed)
        timer.set_text(
            f"{elapsed // 3600:d}:{elapsed % 3600 // 60:02d}:{elapsed % 60:02d}"
            if elapsed >= 3600
            else f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        )
        levels = rec.levels()
        clase_meter.set_content(meter_html(levels.get("clase", -120)))
        if mic_meter is not None:
            mic_meter.set_content(meter_html(levels.get("mic", -120)))
        current = svc.live
        if current.version == seen["version"]:
            return
        seen["version"] = current.version
        segments = list(current.segments)
        if segments and seen["count"] == 0:
            waiting.delete()
        for seg in segments[seen["count"] :]:
            with transcript, ui.element("div").classes("transcript-line"):
                ui.label(ts(seg.start)).classes("ts tnum")
                with ui.column().classes("gap-1"):
                    speaker_label(seg.speaker)
                    ui.label(seg.text)
        if len(segments) > seen["count"]:
            seen["count"] = len(segments)
            scroller.scroll_to(percent=1.0)
        status.set_text(
            f"{current.queue} frases en cola de transcripción…" if current.queue > 2 else ""
        )
        lost_area.clear()
        if current.source_lost:
            with (
                lost_area,
                banner(
                    "warn",
                    f"{current.source_lost} La grabación sigue. ¿Cambiar de fuente?",
                    "warning",
                ),
            ):
                ui.button("Todo el sistema", on_click=lambda: change_source("system")).props(
                    "flat dense"
                )
                ui.button("Una pestaña", on_click=lambda: change_source("tab")).props("flat dense")
        if current.error:
            with lost_area:
                banner("warn", current.error, "error")
        for ins in current.insights[seen["insights"] :]:
            with side:
                for task in ins.get("tareas", []):
                    with ui.element("div").classes("card").style("padding:12px"):
                        ui.label("📌 Posible tarea").classes("small muted")
                        ui.label(task["descripcion"]).style("font-weight:600")
                        if task.get("fecha_texto"):
                            ui.label(f"Fecha: {task['fecha_texto']}").classes("small muted2")
                for kp in ins.get("puntos_clave", []):
                    with ui.element("div").classes("card").style("padding:12px"):
                        ui.label("⭐ Importante").classes("small muted")
                        ui.label(kp["texto"])
                for q in ins.get("preguntas_sugeridas", []):
                    with ui.element("div").classes("card").style("padding:12px"):
                        ui.label("❓ Pregunta sugerida").classes("small muted")
                        ui.label(q)
        seen["insights"] = len(current.insights)
        related_box.clear()
        if current.related:
            with related_box:
                ui.label("🔗 Clases relacionadas").classes("small muted")
                for r in current.related:
                    ui.label(f"{r['course']} · {r['day']} — {r['title']}").classes("small")

    ui.timer(0.5, tick)
    ui.keyboard(
        on_key=lambda e: (
            e.action.keydown
            and (e.modifiers.meta or e.modifiers.ctrl)
            and str(e.key).lower() == "m"
            and svc.mark()
        )
    )
    _ = (time, PLATFORM, MOD, source, is_browser)
