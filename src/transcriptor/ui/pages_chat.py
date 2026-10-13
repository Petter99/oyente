"""Chat sobre las clases y gestión de cursos."""

from __future__ import annotations

from typing import Any

from nicegui import run, ui

from transcriptor.chat.service import Answer
from transcriptor.services import AppServices
from transcriptor.ui.common import empty_state, frame, nice_date, open_note, pill
from transcriptor.ui.pages_main import add_as_dialog

SUGGESTIONS = [
    "¿Qué tareas tengo esta semana?",
    "¿Qué temas vimos esta semana?",
    "¿Cuándo es el próximo examen?",
    "Resume la última clase de {curso}",
]


def chat_page(svc: AppServices, initial: str = "") -> None:
    history: list[tuple[str, Answer | None]] = []

    def actions() -> None:
        options = {None: "Todas las clases", **{c.name: c.name for c in svc.cfg.courses}}
        ui.select(options, value=None, on_change=lambda e: scope.update(course=e.value)).props(
            'dense outlined aria-label="Alcance"'
        ).style("width:200px")
        if svc.ai_available:
            pill(f"IA: {svc.cfg.ai.provider.capitalize()}", "ok")
        else:
            pill("Modo local · sin IA")

    scope: dict[str, Any] = {"course": None}
    with frame(
        svc,
        "chat",
        "Chat",
        "Pregunta por tus clases anteriores",
        show_search=False,
        actions=actions,
    ):
        messages = ui.column().classes("w-full gap-3").style("max-width:860px;margin:0 auto;flex:1")

        @ui.refreshable
        def render() -> None:
            messages.clear()
            with messages:
                if not history:
                    empty_state(
                        "forum",
                        "Pregúntale a tus clases",
                        "Las preguntas sobre tareas se responden sin IA (gratis). Las demás usan solo "
                        "los fragmentos relevantes de tus clases.",
                    )
                    with ui.row().classes("justify-center w-full gap-2"):
                        course = svc.cfg.courses[0].name if svc.cfg.courses else "mi curso"
                        for s in SUGGESTIONS:
                            text = s.format(curso=course)
                            ui.button(text, on_click=lambda t=text: send(t)).classes(
                                "btn-plain"
                            ).props("dense rounded")
                for question, answer in history:
                    ui.label(question).classes("chat-bubble user")
                    if answer is None:
                        with ui.element("div").classes("chat-bubble bot"):
                            ui.spinner("dots")
                        continue
                    with ui.element("div").classes("chat-bubble bot"):
                        ui.markdown(answer.text)
                        if answer.reminder_request:
                            _reminder_picker(svc)
                        if answer.citations and answer.intent == "pregunta":
                            with ui.row().classes("gap-1 q-mt-sm"):
                                for c in answer.citations[:5]:
                                    ui.button(
                                        c.label or c.title,
                                        icon="link",
                                        on_click=lambda c=c: open_note(svc, c.note_path),
                                    ).classes("btn-plain").props("dense no-caps size=sm")
                        foot = (
                            "sin IA · 0 tokens"
                            if not answer.used_llm
                            else f"{answer.tokens:,} tokens".replace(",", ".")
                        )
                        ui.label(foot).classes("small muted q-mt-xs")

        async def send(text: str) -> None:
            text = (text or "").strip()
            if not text:
                return
            history.append((text, None))
            render.refresh()
            answer = await run.io_bound(svc.ask, text, scope["course"])
            history[-1] = (text, answer)
            render.refresh()

        render()
        with ui.row().classes("w-full items-center gap-2").style("max-width:860px;margin:0 auto"):
            box = (
                ui.input(placeholder="Escribe tu pregunta…")
                .props("outlined rounded")
                .classes("flex-1")
            )

            async def submit() -> None:
                value = box.value
                box.set_value("")
                await send(value)

            box.on("keydown.enter", submit)
            ui.button(icon="send", on_click=submit).classes("btn-primary").props(
                'round aria-label="Enviar"'
            )
        if initial:
            ui.timer(0.1, lambda: send(initial), once=True)


def _reminder_picker(svc: AppServices) -> None:
    rows = svc.tasks_pending(10)
    if not rows:
        ui.label("No hay tareas pendientes para recordar.").classes("muted small")
        return
    ui.label("¿De cuál tarea?").classes("small muted q-mt-sm")
    for r in rows[:6]:
        ui.button(
            f"{r['description']} · {nice_date(r['due']) or 'sin fecha'}",
            on_click=lambda r=r: add_as_dialog(svc, r["session_id"], r["id"]),
        ).classes("btn-plain").props("dense no-caps align=left")


# ------------------------------------------------------------------ cursos
def courses_page(svc: AppServices) -> None:
    with frame(svc, "cursos", "Cursos", "Profesor, idioma, glosario y ajustes por curso"):

        @ui.refreshable
        def listing() -> None:
            if not svc.cfg.courses:
                empty_state("school", "Sin cursos", "Agrega tus cursos para empezar a grabar.")
            for course in svc.cfg.courses:
                with ui.expansion(
                    course.name, caption=course.teacher or "Sin profesor", icon="school"
                ).classes("card w-full"):
                    name = ui.input("Nombre", value=course.name).classes("w-full")
                    teacher = ui.input("Profesor", value=course.teacher).classes("w-full")
                    langs = {"es": "Español", "en": "Inglés", "pt": "Portugués", "fr": "Francés"}
                    langs.setdefault(course.language, course.language)
                    lang = ui.select(langs, label="Idioma de la clase", value=course.language)
                    glossary = ui.input_chips(
                        "Glosario (nombres propios y términos técnicos)",
                        value=list(course.glossary),
                    ).classes("w-full")
                    keywords = ui.input_chips(
                        "Cómo aparece en tu calendario (palabras clave)",
                        value=list(course.calendar_keywords),
                    ).classes("w-full")
                    with ui.row().classes("items-center gap-4"):
                        retention = ui.select(
                            {
                                "global": "Usar ajuste general",
                                "always": "Siempre",
                                "days": "Durante N días",
                                "never": "Nunca",
                            },
                            label="Guardar audio",
                            value=course.retention.mode if course.retention else "global",
                        )
                        fmt = ui.select(
                            {
                                "global": "Usar ajuste general",
                                "opus": "Opus (≈ 11 MB/h)",
                                "flac": "FLAC (≈ 60 MB/h)",
                            },
                            label="Formato",
                            value=course.audio_format or "global",
                        )

                    def save(
                        c=course,
                        name=name,
                        teacher=teacher,
                        lang=lang,
                        glossary=glossary,  # type: ignore[no-untyped-def]
                        keywords=keywords,
                        retention=retention,
                        fmt=fmt,
                    ) -> None:
                        from transcriptor.config.models import RetentionConfig

                        c.name, c.teacher, c.language = (
                            name.value.strip() or c.name,
                            teacher.value.strip(),
                            lang.value,
                        )
                        c.glossary = [g.strip() for g in glossary.value if g.strip()]
                        c.calendar_keywords = [k.strip() for k in keywords.value if k.strip()]
                        c.retention = (
                            None
                            if retention.value == "global"
                            else RetentionConfig(
                                mode=retention.value, days=svc.cfg.audio.retention.days
                            )
                        )
                        c.audio_format = None if fmt.value == "global" else fmt.value
                        svc.save_config()
                        ui.notify("Curso guardado", type="positive")
                        listing.refresh()

                    def delete_profile(c=course) -> None:  # type: ignore[no-untyped-def]
                        svc.profiles.delete(c.id)
                        ui.notify("Perfil de voz del profesor borrado")

                    async def remove(c=course) -> None:  # type: ignore[no-untyped-def]
                        with ui.dialog() as d, ui.card():
                            ui.label(f"¿Quitar «{c.name}»?").style("font-weight:700")
                            ui.label("Las notas y clases ya grabadas no se borran.").classes(
                                "muted"
                            )
                            with ui.row().classes("justify-end w-full"):
                                ui.button("Cancelar", on_click=lambda: d.submit(False)).classes(
                                    "btn-plain"
                                )
                                ui.button("Quitar", on_click=lambda: d.submit(True)).props(
                                    "color=negative"
                                )
                        if await d:
                            svc.cfg.courses = [x for x in svc.cfg.courses if x.id != c.id]
                            svc.save_config()
                            listing.refresh()

                    with ui.row().classes("q-mt-md gap-2"):
                        ui.button("Guardar", on_click=save).classes("btn-primary")
                        ui.button("Borrar perfil de voz", on_click=delete_profile).classes(
                            "btn-plain"
                        )
                        ui.button("Quitar curso", on_click=remove).props("flat color=negative")

        listing()
        with ui.element("section").classes("card"):
            ui.html("<h2>Agregar curso</h2>", sanitize=False)
            with ui.row().classes("items-end gap-3 w-full"):
                name = ui.input("Nombre del curso").classes("flex-1")
                teacher = ui.input("Profesor (opcional)").classes("flex-1")

                def add() -> None:
                    if not (name.value or "").strip():
                        ui.notify("Escribe el nombre del curso", type="warning")
                        return
                    svc.add_course(name.value, teacher.value or "")
                    name.set_value("")
                    teacher.set_value("")
                    listing.refresh()

                ui.button("Agregar", on_click=add).classes("btn-primary")
