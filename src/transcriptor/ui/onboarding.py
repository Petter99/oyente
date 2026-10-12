"""Asistente de primera vez (6 pasos; se pueden omitir todos salvo la carpeta y el último)."""

from __future__ import annotations

import platform
from pathlib import Path

from nicegui import run, ui

from transcriptor.audio.decode import estimate_mb
from transcriptor.calendar.google_auth import GoogleNotConfiguredError
from transcriptor.services import AppServices
from transcriptor.ui.common import banner, open_external
from transcriptor.ui.pages_settings import pick_folder

STEPS = ["Carpeta", "Cursos", "IA", "Google", "Audio", "Obsidian"]


def onboarding_page(svc: AppServices) -> None:
    ui.page_title(f"Bienvenido a {svc.brand()}")
    svc.warm_up_models()  # la descarga del modelo de voz empieza ya, en segundo plano
    with ui.element("div").classes("app-shell"):
        with ui.element("nav").classes("sidebar").style("width:260px"):
            with ui.element("div").classes("brand"):
                ui.image("/branding/icon.png").style("width:26px;height:26px")
                ui.label(svc.brand())
            ui.label("Configuración inicial").classes("muted small q-px-sm")

            @ui.refreshable
            def progress() -> None:
                for i, name in enumerate(STEPS):
                    active = i == state["step"]
                    done = i < state["step"]
                    with ui.element("div").classes("nav-item" + (" active" if active else "")):
                        ui.icon("check_circle" if done else "radio_button_unchecked")
                        ui.label(f"{i + 1}. {name}")

            state = {"step": 0}
            progress()
            ui.element("div").style("flex:1")

            @ui.refreshable
            def model_status() -> None:
                with ui.element("div").classes("status-card"):
                    st = svc.models_status
                    if st == "listo":
                        ui.label("✔ Modelo de voz listo")
                    elif st.startswith("error"):
                        ui.label("No se pudo descargar el modelo de voz. Se reintentará al grabar.")
                    else:
                        ui.label("Descargando el modelo de voz (solo la primera vez)…")
                        ui.linear_progress(show_value=False).props("indeterminate size=4px")

            model_status()
            ui.timer(2.0, model_status.refresh)
        with ui.element("main").classes("main"):
            body = ui.column().classes("content").style("max-width:760px")

    def finish() -> None:
        svc.cfg.ui.onboarding_done = True
        svc.save_config()
        ui.navigate.to("/")

    def go(step: int) -> None:
        if step >= len(STEPS):  # nunca renderizar un paso que no existe (página en blanco)
            finish()
            return
        state["step"] = step
        progress.refresh()
        render()

    def nav(
        back: bool = True, skip: bool = True, next_label: str = "Continuar", on_next=None
    ) -> None:  # type: ignore[no-untyped-def]
        with ui.row().classes("w-full justify-between q-mt-lg"):
            if back and state["step"] > 0:
                ui.button("Atrás", on_click=lambda: go(state["step"] - 1)).classes("btn-plain")
            else:
                ui.element("div")
            with ui.row().classes("gap-2"):
                # en el último paso "Omitir" no tiene a dónde ir: solo queda "Terminar"
                if skip and state["step"] + 1 < len(STEPS):
                    ui.button("Omitir", on_click=lambda: go(state["step"] + 1)).props("flat")

                async def nxt() -> None:
                    if on_next is not None:
                        ok = await on_next() if callable(on_next) else True
                        if ok is False:
                            return
                    go(state["step"] + 1)

                ui.button(next_label, on_click=nxt).classes("btn-primary")

    def title(text: str, sub: str) -> None:
        ui.label(text).style("font-size:24px;font-weight:700")
        ui.label(sub).classes("muted").style("font-size:14px")

    def render() -> None:
        body.clear()
        with body:
            [step_folder, step_courses, step_ai, step_google, step_audio, step_obsidian][
                state["step"]
            ]()

    # 1 ------------------------------------------------------------ carpeta
    def step_folder() -> None:
        title(
            f"Bienvenido a {svc.brand()}",
            "Elige la carpeta donde se guardarán tus clases. Puede ser tu bóveda "
            "de Obsidian, dentro de Google Drive, OneDrive, Dropbox o iCloud.",
        )
        path = ui.input(
            "Carpeta", value=svc.cfg.general.vault_path or str(Path.home() / "Documents" / "Clases")
        ).classes("w-full q-mt-md")

        async def browse() -> None:
            chosen = await pick_folder(path.value)
            if chosen:
                path.set_value(chosen)

        ui.button("Examinar…", on_click=browse).classes("btn-plain")
        ui.label("Si la carpeta no existe, se creará.").classes("small muted")

        async def save() -> bool:
            value = (path.value or "").strip()
            if not value:
                ui.notify("Elige una carpeta", type="warning")
                return False
            Path(value).mkdir(parents=True, exist_ok=True)
            svc.cfg.general.vault_path = value
            svc.save_config()
            return True

        nav(back=False, skip=False, on_next=save)

    # 2 ------------------------------------------------------------ cursos
    def step_courses() -> None:
        title(
            "Tus cursos",
            "Agrega los cursos que vas a grabar. El glosario ayuda a escribir bien nombres y "
            "términos técnicos.",
        )

        @ui.refreshable
        def listing() -> None:
            for c in svc.cfg.courses:
                ui.label(f"• {c.name}" + (f" — {c.teacher}" if c.teacher else "")).style(
                    "font-size:14px"
                )

        listing()
        with ui.row().classes("w-full items-end gap-2 q-mt-md"):
            name = ui.input("Curso").classes("flex-1")
            teacher = ui.input("Profesor (opcional)").classes("flex-1")
        glossary = ui.input_chips("Glosario (opcional)").classes("w-full")

        def add() -> None:
            if (name.value or "").strip():
                svc.add_course(name.value, teacher.value or "", list(glossary.value or []))
                name.set_value("")
                teacher.set_value("")
                glossary.set_value([])
                listing.refresh()

        ui.button("Agregar curso", icon="add", on_click=add).classes("btn-plain")

        async def check() -> bool:
            add()
            return True

        nav(on_next=check)

    # 3 ------------------------------------------------------------ IA
    def step_ai() -> None:
        title(
            "Inteligencia artificial (opcional)",
            "Sin IA la app transcribe, crea y relaciona tus notas igual. Con IA obtienes resúmenes, tareas "
            "precisas, sugerencias en vivo y un chat que responde con tus clases.",
        )
        choice = {"value": svc.cfg.ai.provider}
        options = {
            "anthropic": "Claude (recomendado)",
            "gemini": "Gemini",
            "ollama": "Local (Ollama)",
            "none": "Ninguno: solo transcribir",
        }
        ui.radio(
            options,
            value=choice["value"],
            on_change=lambda e: (choice.update(value=e.value), key_area.refresh()),
        ).classes("q-mt-md")

        @ui.refreshable
        def key_area() -> None:
            prov = choice["value"]
            if prov in ("anthropic", "gemini"):
                url = (
                    "https://console.anthropic.com/settings/keys"
                    if prov == "anthropic"
                    else "https://aistudio.google.com/apikey"
                )
                key = ui.input(
                    "Pega tu API key", password=True, password_toggle_button=True
                ).classes("w-full")
                result = ui.label("").classes("small")

                async def test() -> None:
                    result.set_text("Probando…")
                    ok, msg = await run.io_bound(svc.test_provider, prov, key.value or None)
                    result.set_text(("✔ " if ok else "✖ ") + msg)

                with ui.row():
                    ui.button("Probar conexión", on_click=test).classes("btn-plain")
                    ui.button(
                        "¿Cómo consigo una clave?", on_click=lambda: open_external(url)
                    ).props("flat")
                ui.label("La clave se guarda en el llavero del sistema.").classes("small muted")
            elif prov == "ollama":
                ui.label("Instala Ollama y descarga un modelo (por ejemplo «qwen3:8b»).").classes(
                    "muted"
                )
                ui.button(
                    "Abrir ollama.com", on_click=lambda: open_external("https://ollama.com")
                ).props("flat")
            else:
                banner(
                    "info",
                    "Perfecto: podrás agregar IA más tarde en Configuración → IA y procesar las clases "
                    "pendientes.",
                    "info",
                )

        key_area()

        async def save() -> bool:
            svc.cfg.ai.provider = choice["value"]  # type: ignore[assignment]
            svc.save_config()
            return True

        nav(on_next=save)

    # 4 ------------------------------------------------------------ Google
    def step_google() -> None:
        title(
            "Conectar con Google (opcional)",
            "Para avisarte cuando empieza tu clase, crear recordatorios en un calendario propio y enviar tus "
            "tareas a Google Tasks. No hace falta para usar la app.",
        )

        async def connect() -> None:
            try:
                await run.io_bound(svc.connect_google)
                ui.notify("Conectado con Google ✔", type="positive")
                render()
            except GoogleNotConfiguredError as exc:
                ui.notify(str(exc), type="warning", multi_line=True)
            except Exception as exc:
                ui.notify(f"No se pudo conectar: {exc}", type="negative")

        if svc.cfg.google.connected:
            banner("ok", "Conectado con Google ✔", "check_circle")
        else:
            ui.button("Conectar con Google", icon="login", on_click=connect).classes(
                "btn-primary q-mt-md"
            )
        with ui.element("section").classes("banner info q-mt-md"):
            ui.icon("school").style("font-size:20px;color:var(--acento)")
            ui.markdown(
                "**¿Usas Canvas o Moodle?** Importa tu calendario de actividades a Google Calendar para "
                "ver todas tus entregas. Te guiamos en Configuración → Universidad."
            ).classes("flex-1").style("margin:0")
        nav()

    # 5 ------------------------------------------------------------ audio
    def step_audio() -> None:
        a = svc.cfg.audio
        title(
            "Audio", "¿De dónde tomamos el audio de tus clases? Se puede cambiar en cada grabación."
        )
        ui.radio(
            {
                "app": "Solo una aplicación — tu navegador o la app de la llamada (recomendado)",
                "tab": "Una pestaña del navegador (Chrome/Edge)",
                "system": "Todo el sistema",
                "mic": "Solo micrófono (clases presenciales)",
            },
            value=a.default_source,
            on_change=lambda e: setattr(a, "default_source", e.value),
        ).classes("q-mt-md")
        ui.switch(
            "Incluir mi micrófono",
            value=a.include_mic,
            on_change=lambda e: setattr(a, "include_mic", e.value),
        )
        if platform.system() == "Darwin":
            banner(
                "info",
                "macOS pedirá permiso de **Grabación de pantalla y audio del sistema** la primera vez "
                "que grabes una aplicación. Es necesario para capturar el audio de la llamada.",
                "lock",
            )
        ui.label("¿Guardar el audio de las clases?").style("font-weight:600").classes("q-mt-md")
        ui.radio(
            {"always": "Siempre", "days": "Durante 30 días", "never": "Nunca"},
            value=a.retention.mode,
            on_change=lambda e: setattr(a.retention, "mode", e.value),
        )
        ui.label("Formato (si se guarda)").style("font-weight:600").classes("q-mt-sm")
        ui.radio(
            {
                "opus": f"Opus ≈ {estimate_mb('opus', 1, 2):.0f} MB/h con micrófono (recomendado)",
                "flac": f"FLAC ≈ {estimate_mb('flac', 1, 2):.0f} MB/h con micrófono (sin pérdida)",
            },
            value=a.format,
            on_change=lambda e: setattr(a, "format", e.value),
        )

        async def save() -> bool:
            svc.save_config()
            return True

        nav(on_next=save)

    # 6 ------------------------------------------------------------ Obsidian
    def step_obsidian() -> None:
        title(
            "Relaciones con Obsidian",
            "Cada clase nueva se enlazará con su curso, la clase anterior, el "
            "profesor, los temas y tus propias notas. Tu texto nunca se modifica.",
        )
        chips = ui.input_chips(
            "Carpetas a excluir (p. ej. Diario, Privado)",
            value=list(svc.cfg.obsidian.excluded_folders),
        ).classes("w-full q-mt-md")
        result = ui.label("").classes("muted")

        async def analyze() -> bool:
            from transcriptor.links.vault import sync_vault

            svc.cfg.obsidian.excluded_folders = list(chips.value or [])
            svc.save_config()
            result.set_text("Analizando tu bóveda…")
            try:
                n = await run.io_bound(
                    sync_vault, svc.index, svc.vault_root, svc.cfg.obsidian.excluded_folders
                )
                ui.notify(f"{n} notas analizadas", type="positive")
            except Exception as exc:  # un fallo del análisis no debe dejar al usuario atascado
                ui.notify(
                    f"No se pudo analizar la bóveda ({exc}). Puedes reintentarlo en "
                    "Configuración → Obsidian → Reconstruir relaciones.",
                    type="warning",
                    multi_line=True,
                )
            return True

        ui.label(
            "Si no quieres excluir ninguna carpeta, deja la lista vacía y pulsa Terminar."
        ).classes("small muted")
        nav(skip=False, next_label="Terminar", on_next=analyze)

    render()
