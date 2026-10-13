"""Inicio, lista de clases y detalle de una clase."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nicegui import app, run, ui

from transcriptor.audio.decode import decode_file
from transcriptor.audio.retention import delete_now, keep_forever
from transcriptor.calendar.ics import GOOGLE_ADD_BY_URL
from transcriptor.notes.markdown import get_block
from transcriptor.services import AppServices
from transcriptor.session.models import RecordingSession, TaskItem
from transcriptor.ui.common import (
    banner,
    course_bar,
    duration_text,
    empty_state,
    frame,
    highlight_doubts,
    long_date,
    nice_date,
    open_note,
    pill,
    reveal_in_folder,
    speaker_label,
    ts,
)
from transcriptor.ui.recording import open_new_recording

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ tareas
def task_row(svc: AppServices, row: dict[str, Any], on_change: Any = None) -> None:
    with ui.element("div").classes("list-row"):

        def toggle(e: Any) -> None:
            svc.set_task_done(row["session_id"], row["id"], bool(e.value))
            ui.notify("Tarea completada ✔" if e.value else "Tarea pendiente")
            if on_change:
                on_change()

        ui.checkbox(value=bool(row["done"]), on_change=toggle).props(
            f'aria-label="{row["description"]}"'
        )
        with ui.column().classes("gap-0 flex-1"):
            ui.label(row["description"])
            meta = row["course"] + (f" · {nice_date(row['due'])}" if row["due"] else " · sin fecha")
            if not row["confirmed"]:
                meta += " · a confirmar"
            ui.label(meta).classes("small muted")
        if row.get("canvas_match"):
            if row.get("canvas_due") and row.get("due") and row["canvas_due"] != row["due"]:
                pill(f"Canvas: {nice_date(row['canvas_due'])}", "warn")
            else:
                pill("En Canvas", "ok")
        if row.get("google_task_id"):
            ui.html('<span class="small" style="color:var(--ok)">✓ En Tasks</span>', sanitize=False)
        else:
            ui.button(
                "Agregar", on_click=lambda: add_as_dialog(svc, row["session_id"], row["id"])
            ).classes("btn-plain").props("dense")


def add_as_dialog(svc: AppServices, session_id: str, task_id: str) -> None:
    """Tarjeta «Agregar como: Tarea (Tasks) / Recordatorio (Calendar)» con avisos de Canvas."""
    session = svc.store.get(session_id)
    if session is None or session.summary is None:
        return
    task = next((t for t in session.summary.tasks if t.id == task_id), None)
    if task is None:
        return
    with ui.dialog() as dialog, ui.card().style("width:480px;padding:22px"):
        ui.label("Agregar tarea").style("font-weight:700;font-size:15px")
        ui.label(task.description).style("font-size:14px")
        ui.label(
            f"{session.course_name} · "
            + (nice_date(task.due) if task.due else "sin fecha")
            + (f" (dicho: «{task.due_text}»)" if task.due_text else "")
        ).classes("muted small")
        if task.canvas_match:
            if task.canvas_due and task.due and task.canvas_due != task.due:
                banner(
                    "warn",
                    f"Las fechas no coinciden: el profesor dijo **{nice_date(task.due)}**, "
                    f"Canvas dice **{nice_date(task.canvas_due)}**.",
                    "warning",
                )
            else:
                banner(
                    "ok",
                    f"Ya está en Canvas («{task.canvas_match}»). No se duplicará en el calendario.",
                    "check_circle",
                )
        if not svc.cfg.google.connected:
            ui.label(
                "Google no está conectado. Puedes exportar tus tareas como archivo .ics."
            ).classes("muted q-mt-sm")
            with ui.row().classes("justify-end w-full q-mt-md"):
                ui.button(
                    "Conectar con Google",
                    on_click=lambda: ui.navigate.to("/configuracion?tab=google"),
                ).classes("btn-primary")
                ui.button("Cerrar", on_click=dialog.close).classes("btn-plain")
            dialog.open()
            return
        ui.label("Agregar como:").classes("q-mt-sm").style("font-weight:600")
        as_task = ui.checkbox(
            "Tarea (Google Tasks)", value=svc.cfg.google.tasks and task.kind != "examen"
        )
        as_rem = ui.checkbox(
            "Recordatorio (Google Calendar)",
            value=svc.cfg.google.reminders and (task.kind == "examen" or not task.canvas_match),
        )
        if task.due is None:
            as_rem.disable()
            as_rem.set_value(False)

        async def confirm() -> None:
            done = await run.io_bound(
                svc.add_task_to_google, session_id, task_id, as_task.value, as_rem.value
            )
            ui.notify(
                "Agregada a " + " y ".join(done)
                if done
                else "No se pudo agregar (revisa la conexión con Google)",
                type="positive" if done else "warning",
            )
            dialog.close()

        with ui.row().classes("justify-end w-full q-mt-md gap-2"):
            ui.button("Cancelar", on_click=dialog.close).classes("btn-plain")
            ui.button("Agregar", on_click=confirm).classes("btn-primary")
    dialog.open()


def canvas_card(svc: AppServices, on_close: Any) -> None:
    """Recomendación no bloqueante: importar el calendario de Canvas a Google Calendar."""

    def dismiss(imported: bool) -> None:
        svc.cfg.google.canvas_card_dismissed = True
        svc.cfg.google.canvas_imported = imported or svc.cfg.google.canvas_imported
        svc.save_config()
        on_close()

    with ui.element("section").classes("banner info").props('aria-label="Recomendación"'):
        ui.icon("school").style("font-size:20px;color:var(--acento)")
        ui.markdown(
            "**¿Usas Canvas?** Importa tu calendario de actividades a Google Calendar para ver tus "
            "entregas y detectar fechas que no coinciden."
        ).classes("flex-1").style("margin:0")
        ui.button(
            "Ver cómo hacerlo", on_click=lambda: ui.navigate.to("/configuracion?tab=universidad")
        ).classes("btn-primary").props("dense")
        ui.button("Ya lo hice", on_click=lambda: dismiss(True)).classes("btn-plain").props("dense")
        ui.button("Ahora no", on_click=lambda: dismiss(False)).props("flat dense")


# ------------------------------------------------------------------ Inicio
def home_page(svc: AppServices) -> None:
    today = datetime.now().date()
    with frame(svc, "inicio", "Inicio", long_date(today)):
        _notices(svc)
        _next_class(svc)

        @ui.refreshable
        def canvas() -> None:
            g = svc.cfg.google
            if not g.canvas_card_dismissed and not g.canvas_imported:
                canvas_card(svc, canvas.refresh)

        canvas()
        with ui.element("div").style(
            "display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px"
        ):
            with ui.element("section").classes("card"):

                @ui.refreshable
                def tasks() -> None:
                    rows = svc.tasks_pending(8)
                    with ui.row().classes("items-baseline gap-2 q-mb-sm w-full"):
                        ui.html("<h2>Tareas pendientes</h2>", sanitize=False)
                        ui.label(str(len(rows))).classes("muted small")
                        ui.element("div").classes("flex-1")
                        ui.link(
                            "Preguntar en el chat", "/chat?q=¿Qué tareas tengo esta semana?"
                        ).classes("small")
                    if not rows:
                        ui.label("No tienes tareas pendientes. 🎉").classes("muted")
                    for row in rows:
                        task_row(svc, row, tasks.refresh)

                tasks()
            with ui.element("section").classes("card"):
                with ui.row().classes("items-baseline w-full q-mb-sm"):
                    ui.html("<h2>Clases recientes</h2>", sanitize=False)
                    ui.element("div").classes("flex-1")
                    ui.link("Ver todas", "/clases").classes("small")
                sessions = svc.sessions()[:5]
                if not sessions:
                    empty_state(
                        "mic",
                        "Todavía no hay clases",
                        "Pulsa «Grabar» cuando empiece tu próxima clase.",
                    )
                for s in sessions:
                    class_row(svc, s)
        _ask_bar()


def _notices(svc: AppServices) -> None:
    if svc.provider.exhausted:
        banner(
            "warn", "Llegaste al límite de tokens del mes; la app sigue en modo local.", "savings"
        )
    pending = svc.pending_llm_sessions() if svc.ai_available else []
    if pending:
        with banner(
            "info",
            f"Hay **{len(pending)}** clase(s) procesadas sin IA. Ahora que tienes IA puedes "
            "agregarles resumen y tareas.",
            "auto_awesome",
        ):

            async def process() -> None:
                n = await run.io_bound(svc.process_pending)
                ui.notify(f"{n} clase(s) procesadas con IA", type="positive")
                ui.navigate.reload()

            ui.button("Procesar clases pendientes", on_click=process).classes("btn-primary").props(
                "dense"
            )
    for sid, st in list(svc.finalizing.items()):
        if not st.done and not st.error:
            with banner(
                "info", f"Procesando la última clase: {st.step or 'empezando'}…", "hourglass_top"
            ):
                ui.button("Ver", on_click=lambda s=sid: ui.navigate.to(f"/clase/{s}")).props(
                    "flat dense"
                )


def _next_class(svc: AppServices) -> None:
    upcoming = svc.upcoming()
    course = None
    when = ""
    if upcoming:
        u = upcoming[0]
        course = u.course
        minutes = int((u.start - datetime.now().astimezone()).total_seconds() // 60)
        when = f"PRÓXIMA CLASE · HOY {u.start:%H:%M} · " + (
            "AHORA" if minutes <= 0 else f"EN {minutes} MIN"
        )
    elif svc.cfg.courses:
        course = max(svc.cfg.courses, key=lambda c: c.last_source is not None)
        when = "GRABAR UNA CLASE"
    if course is None:
        with ui.element("section").classes("card soft"):
            empty_state(
                "school",
                "Crea tu primer curso",
                "Agrega tus cursos para empezar a grabar. Puedes indicar el profesor y un glosario.",
            )
            ui.button("Agregar curso", on_click=lambda: ui.navigate.to("/cursos")).classes(
                "btn-primary self-center"
            )
        return
    with (
        ui.element("section").classes("card soft").style("display:flex;align-items:center;gap:24px")
    ):
        ui.icon("event").style(
            "font-size:30px;color:var(--acento);background:var(--acento-suave);"
            "border-radius:13px;padding:11px"
        )
        with ui.column().classes("gap-1 flex-1"):
            ui.label(when).classes("small muted").style("font-weight:600;letter-spacing:.04em")
            ui.label(course.name).style("font-size:22px;font-weight:700")
            if course.teacher:
                ui.label(course.teacher).classes("muted2")
            src = {
                "app": "una aplicación",
                "tab": "una pestaña",
                "system": "todo el sistema",
                "mic": "solo micrófono",
            }.get(course.last_source or svc.cfg.audio.default_source, "")
            ui.label(
                f"Audio: {course.last_app or src}"
                + (" + tu micrófono" if svc.cfg.audio.include_mic else "")
            ).classes("muted small")
        with ui.column().classes("items-stretch gap-1"):
            with (
                ui.button(on_click=lambda: open_new_recording(svc))
                .classes("btn-rec")
                .mark("grabar-ahora")
                .style("height:44px;padding:0 22px;font-size:15px")
            ):
                ui.html(
                    '<span class="rec-dot" style="width:10px;height:10px"></span>', sanitize=False
                )
                ui.label("Grabar ahora").classes("q-ml-sm")


def _ask_bar() -> None:
    with (
        ui.element("section")
        .classes("banner")
        .style("background:var(--fondo-lateral);border-radius:14px")
    ):
        ui.icon("forum").style("color:var(--acento)")
        q = (
            ui.input(placeholder="Pregúntale algo a tus clases…")
            .props("borderless dense")
            .classes("flex-1")
        )
        q.on("keydown.enter", lambda: ui.navigate.to(f"/chat?q={q.value}"))
        for text in ("¿Qué tareas tengo esta semana?", "¿Qué temas vimos esta semana?"):
            ui.button(text, on_click=lambda t=text: ui.navigate.to(f"/chat?q={t}")).classes(
                "btn-plain"
            ).props("dense rounded")


def class_row(svc: AppServices, s: RecordingSession) -> None:
    with (
        ui.element("a")
        .classes("list-row clickable")
        .props(f'href="/clase/{s.id}"')
        .style("color:inherit;text-decoration:none;padding:10px 8px")
    ):
        course_bar(s.course_name)
        with ui.column().classes("gap-0 flex-1"):
            ui.label(s.title).style("font-weight:600")
            ui.label(
                f"{s.course_name} · {nice_date(s.day)} · {duration_text(s.duration_s)}"
            ).classes("small muted")
        st = svc.finalizing.get(s.id)
        if s.status in ("recording", "finalizing") or (st and not st.done and not st.error):
            pill("Procesando…")
        elif s.status == "error":
            pill("Con errores", "warn")
        else:
            if s.low_confidence_words:
                pill(f"{s.low_confidence_words} palabras por revisar", "warn")
            if not s.llm_processed:
                pill("Sin resumen")
            if s.teacher_needs_confirmation:
                pill("Confirmar profesor")


# ------------------------------------------------------------------ Clases
def classes_page(svc: AppServices) -> None:
    with frame(svc, "clases", "Clases", "Todas tus clases grabadas"):
        state: dict[str, Any] = {"course": None, "text": ""}
        with ui.row().classes("items-center gap-3"):
            ui.select(
                {None: "Todos los cursos", **{c.name: c.name for c in svc.cfg.courses}},
                value=None,
                on_change=lambda e: (state.update(course=e.value), listing.refresh()),
            ).props("dense outlined").style("width:220px")
            ui.input(
                placeholder="Filtrar por título…",
                on_change=lambda e: (state.update(text=e.value or ""), listing.refresh()),
            ).props("dense outlined clearable").style("width:260px")

        @ui.refreshable
        def listing() -> None:
            sessions = [
                s
                for s in svc.sessions()
                if (not state["course"] or s.course_name == state["course"])
                and state["text"].lower() in s.title.lower()
            ]
            if not sessions:
                empty_state(
                    "library_books", "No hay clases", "Las clases que grabes aparecerán aquí."
                )
                return
            with ui.element("section").classes("card"):
                current = None
                for s in sessions:
                    month = s.started_at.strftime("%Y-%m")
                    if month != current:
                        current = month
                        ui.label(s.started_at.strftime("%B %Y").capitalize()).classes(
                            "small muted q-mt-sm"
                        )
                    class_row(svc, s)

        listing()


# ------------------------------------------------------------------ Detalle de clase
def class_page(svc: AppServices, session_id: str) -> None:
    session = svc.store.get(session_id)
    if session is None:
        with frame(svc, "clases", "Clase no encontrada"):
            empty_state("search_off", "No se encontró la clase", "Puede que se haya borrado.")
        return
    st = svc.finalizing.get(session_id)
    processing = session.status in ("recording", "finalizing") or (
        st is not None and not st.done and not st.error
    )

    def actions() -> None:
        ui.button(
            "Curso y profesor",
            icon="school",
            on_click=lambda: course_info_dialog(svc, session),
        ).classes("btn-plain")
        if session.note_path:
            ui.button(
                "Abrir en Obsidian" if svc.can_open_in_obsidian else "Abrir nota",
                icon="open_in_new",
                on_click=lambda: open_note(svc, session.note_path),
            ).classes("btn-plain")
            txt_folder = svc.layout().transcript_folder(session)
            with ui.button("Archivos", icon="folder_open").classes("btn-plain"):
                with ui.menu():
                    ui.menu_item(
                        "Mostrar .md en la carpeta",
                        on_click=lambda: reveal_in_folder(svc.vault_root / session.note_path),
                    )
                    item = ui.menu_item(
                        "Mostrar .txt en la carpeta",
                        on_click=lambda: reveal_in_folder(txt_folder / "transcripcion.txt"),
                    )
                    if txt_folder is None:
                        item.props("disable")

    subtitle = (
        f"{session.course_name} · {nice_date(session.day)} · {duration_text(session.duration_s)}"
    )
    with frame(svc, "clases", session.title, subtitle, show_search=False, actions=actions):
        if processing:
            progress = ui.label(f"Procesando: {st.step if st and st.step else 'empezando'}…").classes("muted")
            ui.linear_progress(show_value=False).props("indeterminate")

            def poll() -> None:
                cur = svc.finalizing.get(session_id)
                fresh = svc.store.get(session_id)
                if (cur and (cur.done or cur.error)) or (
                    fresh is not None and fresh.status in ("done", "error")
                ):
                    ui.navigate.reload()
                elif cur:
                    progress.set_text(f"Procesando: {cur.step or 'empezando'}…")

            ui.timer(1.0, poll)
            segments = svc.store.load_segments(session_id, "live")
            _transcript(segments, live=True)
            return
        if session.status == "error":
            banner(
                "warn", "La clase se guardó con errores: " + "; ".join(session.errors[-3:]), "error"
            )
        if session.teacher_needs_confirmation:
            _teacher_confirmation(svc, session)
        if session.suggestion is not None and session.suggestion.status == "pending":
            _course_suggestion(svc, session)
        _audio_panel(svc, session)
        segments = svc.store.load_segments(session_id, "final") or svc.store.load_segments(
            session_id, "live"
        )
        note_text = ""
        if session.note_path and (svc.vault_root / session.note_path).exists():
            note_text = (svc.vault_root / session.note_path).read_text(encoding="utf-8")
        with ui.element("div").style(
            "display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:20px"
        ):
            with ui.column().classes("gap-4"):
                with ui.element("section").classes("card"):
                    ui.html("<h2>Resumen</h2>", sanitize=False)
                    summary = session.summary
                    if summary and summary.summary:
                        ui.markdown(summary.summary)
                    else:
                        ui.label("Sin resumen: esta clase se procesó sin IA.").classes("muted")
                        if svc.ai_available:

                            async def reprocess() -> None:
                                await run.io_bound(
                                    svc.finalizer(final_transcriber=_no_asr()).reprocess_with_llm,
                                    session,
                                    svc.course(session.course_id),
                                )
                                ui.navigate.reload()

                            ui.button("Procesar con IA", on_click=reprocess).classes(
                                "btn-primary"
                            ).props("dense")
                    if summary and summary.key_points:
                        ui.label("Puntos importantes").style("font-weight:700").classes("q-mt-md")
                        for kp in summary.key_points:
                            ui.label(
                                f"• {kp.text}" + (f"  ({ts(kp.t)})" if kp.t is not None else "")
                            )
                _transcript(segments)
            with ui.column().classes("gap-4"):
                with ui.element("section").classes("card"):
                    ui.html("<h2>Tareas</h2>", sanitize=False)
                    tasks = session.summary.tasks if session.summary else []
                    if not tasks:
                        ui.label("No se detectaron tareas.").classes("muted")
                    for t in tasks:
                        _task_item(svc, session, t)
                with ui.element("section").classes("card"):
                    ui.html("<h2>Temas y relaciones</h2>", sanitize=False)
                    topics = (get_block(note_text, "temas") or "").splitlines()
                    related = (get_block(note_text, "relacionado") or "").splitlines()
                    for line in topics + related:
                        _obsidian_line(svc, line)
                    if not topics and not related:
                        ui.label("Sin relaciones todavía.").classes("muted")
                if session.markers:
                    with ui.element("section").classes("card"):
                        ui.html("<h2>Momentos marcados</h2>", sanitize=False)
                        for m in session.markers:
                            ui.label(f"{ts(m.t)} · {m.label}")
                with ui.element("section").classes("card"):
                    ui.html("<h2>Detalles</h2>", sanitize=False)
                    ui.label(f"Fuente: {session.source_label}").classes("small muted2")
                    ui.label(
                        f"Modelo: {session.asr_model_final or session.asr_model_live}"
                    ).classes("small muted2")
                    if session.low_confidence_words:
                        ui.label(
                            f"{session.low_confidence_words} palabras dudosas resaltadas"
                        ).classes("small muted2")
                    if session.llm_tokens:
                        ui.label(f"IA: {session.llm_tokens:,} tokens".replace(",", ".")).classes(
                            "small muted2"
                        )


def _no_asr() -> Any:
    from transcriptor.services import _NoTranscriber

    return _NoTranscriber()


def _obsidian_line(svc: AppServices, line: str) -> None:
    import re

    m = re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", line)
    if not m:
        if line.strip() and not line.startswith("_"):
            ui.label(line.strip("- ")).classes("small")
        return
    target, alias = m.group(1), m.group(2) or m.group(1).split("/")[-1]
    rest = line[m.end() :].strip(" —-")
    with ui.row().classes("items-center gap-1 no-wrap"):
        ui.link(alias, "#").on("click.prevent", lambda t=target: open_note(svc, t + ".md")).classes(
            "small"
        )
        if rest:
            ui.label(f"— {rest}").classes("small muted")


def _task_item(svc: AppServices, session: RecordingSession, t: TaskItem) -> None:
    with ui.element("div").classes("list-row").style("align-items:flex-start"):
        ui.checkbox(
            value=t.done, on_change=lambda e: svc.set_task_done(session.id, t.id, bool(e.value))
        )
        with ui.column().classes("gap-0 flex-1"):
            ui.label(t.description)
            meta = nice_date(t.due) if t.due else "sin fecha"
            if t.due_text:
                meta += f" · «{t.due_text}»"
            if not t.confirmed:
                meta += " · a confirmar"
            ui.label(meta).classes("small muted")
            if t.canvas_match and t.canvas_due and t.due and t.canvas_due != t.due:
                ui.html(
                    f'<span class="small" style="color:var(--aviso)">⚠ Las fechas no coinciden: '
                    f"Canvas dice {nice_date(t.canvas_due)}</span>",
                    sanitize=False,
                )
            elif t.canvas_match:
                ui.html(
                    '<span class="small" style="color:var(--ok)">✓ Ya está en Canvas</span>',
                    sanitize=False,
                )
        if t.google_task_id or t.google_event_id:
            ui.html(
                '<span class="small" style="color:var(--ok)">✓ En Google</span>', sanitize=False
            )
        else:
            ui.button("Agregar", on_click=lambda: add_as_dialog(svc, session.id, t.id)).classes(
                "btn-plain"
            ).props("dense")


def marked_text(seg: Any, threshold: float = 0.45) -> str:
    """Texto del segmento con ==palabras dudosas== (baja confianza de Whisper)."""
    if not seg.words:
        return seg.text.strip()
    out = []
    for w in seg.words:
        token, stripped = w.text, w.text.strip()
        if stripped and w.probability < threshold and any(c.isalnum() for c in stripped):
            token = f"{token[: len(token) - len(token.lstrip())]}=={stripped}=="
        out.append(token)
    return "".join(out).strip()


def _transcript(segments: list, live: bool = False) -> None:
    with ui.element("section").classes("card"):
        state = {"speaker": "Todos"}
        speakers = ["Todos"] + sorted({s.speaker for s in segments if s.speaker})
        with ui.row().classes("items-center w-full"):
            ui.html("<h2>Transcripción" + (" en vivo" if live else "") + "</h2>", sanitize=False)
            ui.element("div").classes("flex-1")
            if len(speakers) > 2:
                ui.toggle(
                    speakers,
                    value="Todos",
                    on_change=lambda e: (state.update(speaker=e.value), body.refresh()),
                ).props("dense no-caps")

        @ui.refreshable
        def body() -> None:
            shown = [s for s in segments if state["speaker"] in ("Todos", s.speaker)]
            if not shown:
                ui.label("Sin texto.").classes("muted")
            for seg in shown[:3000]:
                with ui.element("div").classes("transcript-line"):
                    ui.label(ts(seg.start)).classes("ts tnum")
                    with ui.column().classes("gap-1"):
                        speaker_label(seg.speaker)
                        ui.html(highlight_doubts(marked_text(seg)), sanitize=False)

        body()


def _audio_panel(svc: AppServices, session: RecordingSession) -> None:
    files = [Path(p) for p in session.audio_files if Path(p).exists()]
    with (
        ui.element("section")
        .classes("card")
        .style("display:flex;align-items:center;gap:16px;padding:12px 16px")
    ):
        ui.icon("graphic_eq").style("font-size:22px;color:var(--acento)")
        if not files:
            ui.label("El audio de esta clase no se guardó (según tu configuración).").classes(
                "muted flex-1"
            )
            return
        clase = next((f for f in files if f.stem == "clase"), files[0])
        url = app.add_media_file(local_file=clase, url_path=f"/media/{session.id}/{clase.name}")
        ui.audio(url).style("flex:1")
        if session.audio_expires_at:
            days = (session.audio_expires_at.date() - date.today()).days
            ui.label(f"Se borra en {days} días").classes("small muted")

            def keep() -> None:
                keep_forever(clase.parent)
                session.audio_expires_at = None
                svc.store.save(session)
                ui.notify("El audio se conservará")
                ui.navigate.reload()

            ui.button("Conservar", on_click=keep).classes("btn-plain").props("dense")

        async def delete() -> None:
            await run.io_bound(delete_now, clase.parent)
            session.audio_files, session.audio_deleted = [], True
            svc.store.save(session)
            ui.navigate.reload()

        ui.button("Borrar ahora", on_click=delete).props("flat dense color=negative")

        async def retranscribe() -> None:
            ui.notify("Re-transcribiendo… puede tardar varios minutos", type="ongoing")
            try:
                await run.io_bound(
                    svc.finalizer().retranscribe, session, svc.course(session.course_id)
                )
                ui.navigate.reload()
            except Exception as exc:
                ui.notify(f"No se pudo re-transcribir: {exc}", type="negative")

        ui.button("Re-transcribir", on_click=retranscribe).props("flat dense")


def course_info_dialog(
    svc: AppServices, session: RecordingSession, name: str = "", teacher: str = ""
) -> None:
    """Crear/asignar curso y profesor a mano (o corregir la propuesta antes de aceptarla)."""
    with ui.dialog() as dlg, ui.card().style("width:460px"):
        ui.label("Curso y profesor").style("font-weight:700")
        ui.label(
            "Si el curso no existe se crea; si existe, se completa el profesor."
        ).classes("small muted")
        name_in = ui.input("Nombre del curso", value=name or session.course_name).classes("w-full")
        teacher_in = ui.input("Profesor", value=teacher or session.teacher).classes("w-full")

        async def save() -> None:
            if not (name_in.value or "").strip():
                ui.notify("Escribe el nombre del curso", type="warning")
                return
            course, created = await run.io_bound(
                svc.apply_course_info, session.id, name_in.value, teacher_in.value or ""
            )
            dlg.close()
            ui.notify(
                f"Curso «{course.name}» creado." if created else f"Curso «{course.name}» actualizado.",
                type="positive",
            )
            ui.navigate.reload()

        with ui.row().classes("gap-2"):
            ui.button("Guardar", on_click=save).classes("btn-primary")
            ui.button("Cancelar", on_click=dlg.close).classes("btn-plain")
    dlg.open()


def _course_suggestion(svc: AppServices, session: RecordingSession) -> None:
    sug = session.suggestion
    assert sug is not None
    origin = "tu Google Calendar" if sug.source == "calendario" else "la IA"
    creates = sug.course_id is None
    teacher_txt = f" con el profesor {sug.teacher}" if sug.teacher else ""
    question = (
        f"Según {origin}, esta clase es de «{sug.course_name}»{teacher_txt}. "
        + ("¿Quieres crear ese curso?" if creates else "¿Guardar el profesor en el curso?")
    )

    async def accept() -> None:
        await run.io_bound(svc.apply_course_info, session.id, sug.course_name, sug.teacher)
        ui.notify("Listo, se guardó.", type="positive")
        ui.navigate.reload()

    def dismiss() -> None:
        svc.dismiss_suggestion(session.id)
        ui.navigate.reload()

    with ui.element("section").classes("banner purple").style("flex-wrap:wrap"):
        ui.icon("auto_awesome").style("font-size:22px")
        with ui.column().classes("flex-1 gap-1"):
            ui.label("¿Curso y profesor correctos?").style("font-weight:700")
            ui.label(question).classes("small")
        ui.button("Crear" if creates else "Guardar", on_click=accept).classes("btn-primary")
        ui.button(
            "Editar",
            on_click=lambda: course_info_dialog(svc, session, sug.course_name, sug.teacher),
        ).classes("btn-plain")
        ui.button("No", on_click=dismiss).classes("btn-plain")


def _teacher_confirmation(svc: AppServices, session: RecordingSession) -> None:
    work = svc.store.dir(session.id)
    voices_file = work / "voces.json"
    if not voices_file.exists():
        return
    voices = json.loads(voices_file.read_text(encoding="utf-8"))
    clase = Path(session.work_dir) / "clase.flac"
    audio_src = next((Path(p) for p in session.audio_files if Path(p).stem == "clase"), clase)

    def clip(start: float, end: float, name: str) -> str | None:
        if not audio_src.exists():
            return None
        out = work / f"muestra_{name}.wav"
        if not out.exists():
            import soundfile as sf

            data = decode_file(audio_src)
            a = int(start * 16000)
            b = min(len(data), a + int(min(end - start, 6.0) * 16000))
            sf.write(str(out), data[a:b], 16000)
        return app.add_media_file(local_file=out, url_path=f"/media/{session.id}/{out.name}")

    with ui.element("section").classes("banner purple").style("flex-wrap:wrap"):
        ui.icon("record_voice_over").style("font-size:22px")
        teacher = next((v for v in voices if v["label"] == "Profesor"), None)
        with ui.column().classes("flex-1 gap-1"):
            ui.label("¿Esta es la voz del profesor?").style("font-weight:700")
            ui.label(
                "Solo se pregunta en la primera clase del curso. Con tu respuesta, la app lo "
                "reconocerá en las próximas."
            ).classes("small")
            if teacher:
                url = clip(teacher["example_t"], teacher["example_end"], "profesor")
                if url:
                    ui.audio(url).style("height:32px")

        async def yes() -> None:
            await run.io_bound(
                svc.finalizer(final_transcriber=_no_asr()).confirm_teacher,
                session,
                svc.course(session.course_id),
            )
            ui.notify("¡Listo! Se recordará la voz del profesor.", type="positive")
            ui.navigate.reload()

        def no() -> None:
            others = [v for v in voices if v["label"] != "Profesor"]
            with ui.dialog() as dlg, ui.card().style("width:460px"):
                ui.label("¿Cuál de estas voces es la del profesor?").style("font-weight:700")
                if not others:
                    ui.label("No se detectaron otras voces en el audio de la clase.").classes(
                        "muted"
                    )
                for v in others:
                    with ui.row().classes("items-center w-full"):
                        ui.label(f"{v['label']} · {duration_text(v['seconds'])} hablando").classes(
                            "flex-1"
                        )
                        url = clip(v["example_t"], v["example_end"], v["label"].replace(" ", "_"))
                        if url:
                            ui.audio(url).style("height:32px;width:180px")

                        async def choose(label: str = v["label"]) -> None:
                            await run.io_bound(
                                svc.finalizer(final_transcriber=_no_asr()).relabel_teacher,
                                session,
                                svc.course(session.course_id),
                                label,
                            )
                            dlg.close()
                            ui.navigate.reload()

                        ui.button("Es esta", on_click=choose).classes("btn-primary").props("dense")
                ui.button("Cancelar", on_click=dlg.close).classes("btn-plain")
            dlg.open()

        ui.button("Sí", on_click=yes).classes("btn-primary")
        ui.button("No, es otra voz", on_click=no).classes("btn-plain")


GOOGLE_ADD_URL = GOOGLE_ADD_BY_URL
