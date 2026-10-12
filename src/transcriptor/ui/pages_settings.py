"""Configuración (pestañas) — plan de diseño §5 y plan funcional §13.1."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nicegui import app, run, ui

from transcriptor import __version__
from transcriptor.audio.decode import estimate_mb
from transcriptor.calendar.google_auth import GoogleNotConfiguredError
from transcriptor.calendar.ics import GOOGLE_ADD_BY_URL, GUIDES
from transcriptor.capture_web.hub import EXTENSION_IDS
from transcriptor.config.secrets import Secret
from transcriptor.hardware import detect, plan_asr
from transcriptor.paths import data_dir, log_dir
from transcriptor.services import AppServices
from transcriptor.ui.common import banner, frame, open_external, pill, reveal_in_folder

log = logging.getLogger(__name__)

TABS = [
    ("general", "General", "tune"),
    ("audio", "Audio", "graphic_eq"),
    ("transcripcion", "Transcripción", "subtitles"),
    ("hablantes", "Hablantes", "record_voice_over"),
    ("ia", "IA", "auto_awesome"),
    ("obsidian", "Obsidian", "hub"),
    ("google", "Google", "event"),
    ("universidad", "Universidad", "school"),
    ("acerca", "Acerca de", "info"),
]


async def pick_folder(initial: str | None = None) -> str | None:
    """Selector de carpeta nativo (ventana de la app). En navegador, devuelve None."""
    try:
        import webview

        window = app.native.main_window
        if window is None:
            return None
        result = await window.create_file_dialog(webview.FOLDER_DIALOG, directory=initial or "")
        if result:
            return str(result[0] if isinstance(result, (list, tuple)) else result)
    except Exception:
        log.debug("Selector de carpetas nativo no disponible")
    return None


def settings_page(svc: AppServices, tab: str = "general") -> None:
    with frame(svc, "configuracion", "Configuración", show_search=False):
        with ui.row().classes("w-full no-wrap gap-6 items-start"):
            with ui.column().classes("settings-nav gap-0").style("width:200px;flex-shrink:0"):
                for key, label, icon in TABS:
                    with (
                        ui.element("a")
                        .classes("nav-item" + (" active" if key == tab else ""))
                        .props(f'href="/configuracion?tab={key}"')
                    ):
                        ui.icon(icon)
                        ui.label(label)
            with ui.column().classes("flex-1 gap-4").style("max-width:760px"):
                {
                    "general": _general,
                    "audio": _audio,
                    "transcripcion": _transcription,
                    "hablantes": _speakers,
                    "ia": _ai,
                    "obsidian": _obsidian,
                    "google": _google,
                    "universidad": _university,
                    "acerca": _about,
                }.get(tab, _general)(svc)


def _section(title: str, text: str = "") -> Any:
    card = ui.element("section").classes("card w-full")
    with card:
        ui.html(f"<h2>{title}</h2>", sanitize=False)
        if text:
            ui.label(text).classes("muted q-mb-sm")
    return card


def _saved(svc: AppServices) -> None:
    svc.save_config()
    ui.notify("Guardado", type="positive", position="bottom-right")


# ------------------------------------------------------------------ General
def _general(svc: AppServices) -> None:
    g = svc.cfg.general
    with _section(
        "Carpeta compartida / bóveda de Obsidian",
        "Aquí se guardan las notas. Puede estar en Google Drive, OneDrive, Dropbox o iCloud.",
    ):
        path = ui.input("Carpeta", value=g.vault_path or "").classes("w-full")

        async def browse() -> None:
            chosen = await pick_folder(path.value)
            if chosen:
                path.set_value(chosen)

        def save() -> None:
            value = (path.value or "").strip()
            if value and not Path(value).is_dir():
                ui.notify("La carpeta no existe", type="negative")
                return
            g.vault_path = value or None
            _saved(svc)

        with ui.row().classes("gap-2"):
            ui.button("Examinar…", on_click=browse).classes("btn-plain")
            ui.button("Guardar", on_click=save).classes("btn-primary")
            if g.vault_path:
                ui.button(
                    "Mostrar", on_click=lambda: reveal_in_folder(Path(g.vault_path or ""))
                ).props("flat")
    with _section("Carpetas dentro de la bóveda"):
        with ui.row().classes("gap-3 w-full"):
            ui.input("Clases", value=g.classes_folder).bind_value(g, "classes_folder").classes(
                "flex-1"
            )
            ui.input("Temas", value=g.topics_folder).bind_value(g, "topics_folder").classes(
                "flex-1"
            )
            ui.input("Profesores", value=g.teachers_folder).bind_value(
                g, "teachers_folder"
            ).classes("flex-1")
        ui.button("Guardar", on_click=lambda: _saved(svc)).classes("btn-primary q-mt-sm")
    with _section("Apariencia"):
        ui.toggle(
            {"system": "Como el sistema", "light": "Claro", "dark": "Oscuro"},
            value=svc.cfg.ui.theme,
            on_change=lambda e: (
                setattr(svc.cfg.ui, "theme", e.value),
                _saved(svc),
                _apply_theme(e.value),
            ),
        )


def _apply_theme(value: str) -> None:
    ui.dark_mode(None if value == "system" else value == "dark")


# ------------------------------------------------------------------ Audio
def _audio(svc: AppServices) -> None:
    a = svc.cfg.audio
    with _section("Fuente por defecto", "Se puede cambiar al empezar cada grabación."):
        ui.radio(
            {
                "app": "Solo una aplicación (recomendado)",
                "tab": "Una pestaña del navegador (Chrome/Edge)",
                "system": "Todo el sistema",
                "mic": "Solo micrófono",
            },
            value=a.default_source,
            on_change=lambda e: (setattr(a, "default_source", e.value), _saved(svc)),
        )
        ui.switch(
            "Incluir mi micrófono",
            value=a.include_mic,
            on_change=lambda e: (
                setattr(a, "include_mic", e.value),
                _saved(svc),
                estimate.refresh(),
            ),
        )
        try:
            from transcriptor.audio.sources.mic import list_input_devices

            mics = {
                None: "Micrófono predeterminado",
                **{d["id"]: d["name"] for d in list_input_devices()},
            }
        except Exception:
            mics = {None: "Micrófono predeterminado"}
        ui.select(
            mics,
            label="Micrófono",
            value=a.mic_device if a.mic_device in mics else None,
            on_change=lambda e: (setattr(a, "mic_device", e.value), _saved(svc)),
        ).classes("w-full")

    with _section(
        "Guardar el audio de las clases",
        "La transcripción siempre se guarda. Esto decide qué pasa con el audio al terminar.",
    ):
        mode = ui.radio(
            {
                "always": "Siempre",
                "days": "Durante N días",
                "never": "Nunca (se borra tras la "
                "transcripción final; si falla, se conserva para reintentar)",
            },
            value=a.retention.mode,
            on_change=lambda e: (
                setattr(a.retention, "mode", e.value),
                _saved(svc),
                estimate.refresh(),
            ),
        )
        days = ui.number(
            "Días",
            value=a.retention.days,
            min=1,
            max=3650,
            step=1,
            on_change=lambda e: (
                setattr(a.retention, "days", int(e.value or 30)),
                _saved(svc),
                estimate.refresh(),
            ),
        ).style("width:120px")
        days.bind_visibility_from(mode, "value", value="days")
        ui.label("Formato").style("font-weight:600").classes("q-mt-md")
        ui.radio(
            {
                "opus": f"Opus — calidad muy buena para voz, ≈ {estimate_mb('opus', 1, 1):.0f} MB por hora y canal",
                "flac": f"FLAC — sin pérdida (idéntico a lo grabado), ≈ {estimate_mb('flac', 1, 1):.0f} MB por hora y canal",
            },
            value=a.format,
            on_change=lambda e: (setattr(a, "format", e.value), _saved(svc), estimate.refresh()),
        )
        ui.switch(
            "Guardar el audio dentro de la carpeta compartida (si no, solo en este equipo)",
            value=a.store_in_vault,
            on_change=lambda e: (setattr(a, "store_in_vault", e.value), _saved(svc)),
        )

        @ui.refreshable
        def estimate() -> None:
            est = svc.storage_estimate()
            mic = " con micrófono" if a.include_mic else ""
            text = f"≈ **{est['per_hour_mb']:.0f} MB por hora de clase**{mic}. "
            if a.retention.mode == "never":
                text += "El audio no ocupará espacio después de cada clase."
            else:
                total = est["total_mb"] / 1000
                period = (
                    f"con «durante {a.retention.days} días»"
                    if a.retention.mode == "days"
                    else "en un semestre"
                )
                text += (
                    f"Con {len(svc.cfg.courses)} curso(s) (~{est['hours_per_week']:.0f} h/semana), "
                    f"{period} ocupará ≈ **{total:.1f} GB**."
                )
            banner("info", text, "storage")

        estimate()

    with _section(
        "Extensión del navegador (opcional)",
        "Solo hace falta si capturas por pestaña y quieres hacerlo con un clic. "
        "Sin ella, la captura por pestaña funciona igual con un paso más.",
    ):
        ext = svc.hub.extension
        if ext.connected:
            banner("ok", f"Extensión conectada ✔ (versión {ext.version})", "check_circle")
        else:
            ui.markdown(
                "1. Pulsa **Preparar extensión**: se abrirá la carpeta de la extensión.\n"
                "2. En Chrome abre `chrome://extensions` (en Edge, `edge://extensions`).\n"
                "3. Activa **Modo de desarrollador** (arriba a la derecha).\n"
                "4. Pulsa **Cargar descomprimida** y elige la carpeta que se abrió.\n"
                "5. Vuelve aquí: verás «Extensión conectada ✔»."
            )

            async def prepare() -> None:
                folder = await run.io_bound(_extension_folder)
                reveal_in_folder(folder / "manifest.json")
                ui.notify("Carpeta de la extensión abierta. Sigue los pasos en Chrome/Edge.")

            ui.button("Preparar extensión", icon="extension", on_click=prepare).classes(
                "btn-primary"
            )
        ui.label(f"ID de la extensión: {next(iter(EXTENSION_IDS))}").classes("small muted q-mt-sm")


def _extension_folder() -> Path:
    """La extensión viene en el instalador; en desarrollo se genera desde browser-extension/."""
    import sys

    bundled = Path(getattr(sys, "_MEIPASS", "")) / "browser-extension"
    if bundled.joinpath("manifest.json").exists():
        target = data_dir() / "extension"
        import shutil

        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(bundled, target)
        return target
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    from scripts.build_extension import build

    return build(data_dir() / "extension")


# ------------------------------------------------------------------ Transcripción
def _transcription(svc: AppServices) -> None:
    t = svc.cfg.transcription
    hw = detect()
    plan = plan_asr(hw, t.model)
    with _section(
        "Modelo de voz", "La transcripción se hace en este equipo; el audio nunca sale de él."
    ):
        models = {
            "auto": "Automático (recomendado)",
            "large-v3": "large-v3 (máxima precisión)",
            "large-v3-turbo": "large-v3-turbo (rápido)",
            "medium": "medium (equipos modestos)",
            "small": "small (muy rápido)",
        }
        models.setdefault(t.model, t.model)
        ui.select(
            models,
            label="Modelo",
            value=t.model,
            on_change=lambda e: (setattr(t, "model", e.value), _saved(svc)),
        ).classes("w-full")
        gpu = f"GPU NVIDIA {hw.nvidia_vram_gb:.0f} GB" if hw.nvidia_gpu else "sin GPU NVIDIA"
        banner(
            "info",
            f"Equipo: {hw.system} {hw.machine}, {hw.cpu_count} núcleos, {hw.ram_gb:.0f} GB RAM, {gpu}. "
            f"**{plan.note}** — en vivo: `{plan.live_model}`, pasada final: `{plan.final_model}`.",
            "memory",
        )
        ui.select(
            {"es": "Español", "en": "Inglés", "pt": "Portugués", "fr": "Francés"},
            label="Idioma por defecto",
            value=t.language,
            on_change=lambda e: (setattr(t, "language", e.value), _saved(svc)),
        )
        ui.label("El idioma y el glosario de cada curso se ajustan en «Cursos».").classes(
            "small muted"
        )
    with _section("Palabras dudosas"):
        ui.label(
            "Las palabras con baja confianza se resaltan en amarillo para revisarlas."
        ).classes("muted")
        ui.slider(
            min=0.2,
            max=0.8,
            step=0.05,
            value=t.low_confidence,
            on_change=lambda e: (setattr(t, "low_confidence", e.value), _saved(svc)),
        ).props("label-always")


# ------------------------------------------------------------------ Hablantes
def _speakers(svc: AppServices) -> None:
    s = svc.cfg.speakers
    with _section(
        "Identificar al profesor",
        "Siempre activo. En la primera clase de cada curso se pide "
        "confirmar su voz; después se reconoce solo.",
    ):
        ui.switch(
            "Distinguir entre participantes (Participante 1, 2…)",
            value=s.distinguish_participants,
            on_change=lambda e: (setattr(s, "distinguish_participants", e.value), _saved(svc)),
        )
        embedder = svc.speaker_embedder()
        auto = getattr(embedder, "default_threshold", 0.6)
        ui.label(
            f"Sensibilidad (automática: {auto:.2f} con el extractor «{embedder.name}»)"
        ).classes("q-mt-sm")
        ui.slider(
            min=0.2,
            max=0.99,
            step=0.01,
            value=s.teacher_threshold or auto,
            on_change=lambda e: (setattr(s, "teacher_threshold", e.value), _saved(svc)),
        ).props("label-always")
        ui.button(
            "Volver a automático",
            on_click=lambda: (
                setattr(s, "teacher_threshold", None),
                _saved(svc),
                ui.navigate.reload(),
            ),
        ).props("flat dense")

        def delete_all() -> None:
            svc.profiles.delete_all()
            ui.notify("Perfiles de voz borrados")

        ui.button("Borrar todos los perfiles de voz", on_click=delete_all).props(
            "flat color=negative"
        )
        ui.label(
            "Los perfiles son números (no audio), viven solo en este equipo y no se sincronizan."
        ).classes("small muted")
    with _section(
        "Diarización avanzada (opcional)",
        "Mejora con voces solapadas. Requiere un token gratuito de Hugging Face.",
    ):
        token = ui.input(
            "Token de Hugging Face",
            password=True,
            password_toggle_button=True,
            value="••••••••" if svc.secrets.has(Secret.HF_TOKEN) else "",
        ).classes("w-full")

        def save_token() -> None:
            if token.value and token.value != "••••••••":
                svc.secrets.set(Secret.HF_TOKEN, token.value.strip())
            s.use_pyannote = bool(token.value)
            _saved(svc)

        ui.button("Guardar", on_click=save_token).classes("btn-primary")


# ------------------------------------------------------------------ IA
def _ai(svc: AppServices) -> None:
    ai = svc.cfg.ai
    names = {
        "none": "Ninguno: solo transcribir",
        "anthropic": "Claude (Anthropic)",
        "gemini": "Gemini (Google)",
        "ollama": "Local (Ollama)",
    }
    with _section(
        "Proveedor de IA",
        "Opcional. Sin IA, la app transcribe, crea las notas, las relaciona y "
        "busca en tus clases igual. La IA agrega resúmenes, tareas precisas y chat.",
    ):
        ui.select(
            names,
            label="Proveedor principal",
            value=ai.provider,
            on_change=lambda e: (
                setattr(ai, "provider", e.value),
                _saved(svc),
                ui.navigate.reload(),
            ),
        ).classes("w-full")
        ui.select(
            names,
            label="Respaldo (si el principal falla)",
            value=ai.fallback_provider,
            on_change=lambda e: (setattr(ai, "fallback_provider", e.value), _saved(svc)),
        ).classes("w-full")
    for key, label, secret, url in (
        (
            "anthropic",
            "Clave de Claude",
            Secret.ANTHROPIC_API_KEY,
            "https://console.anthropic.com/settings/keys",
        ),
        ("gemini", "Clave de Gemini", Secret.GEMINI_API_KEY, "https://aistudio.google.com/apikey"),
    ):
        if key not in (ai.provider, ai.fallback_provider):
            continue
        with _section(label, "Se guarda en el llavero del sistema, nunca en archivos de texto."):
            has = svc.secrets.has(secret)
            field = ui.input(
                "API key",
                password=True,
                password_toggle_button=True,
                placeholder="ya guardada" if has else "pega aquí tu clave",
            ).classes("w-full")
            result = ui.label("").classes("small")

            async def test(field=field, key=key, result=result) -> None:  # type: ignore[no-untyped-def]
                result.set_text("Probando…")
                ok, msg = await run.io_bound(svc.test_provider, key, field.value or None)
                result.set_text(("✔ " if ok else "✖ ") + msg)
                result.style(f"color:var({'--ok' if ok else '--grabar'})")
                if ok:
                    field.set_value("")
                    svc._provider = None

            with ui.row().classes("gap-2"):
                ui.button("Guardar y probar conexión", on_click=test).classes("btn-primary")
                ui.button(
                    "¿Cómo consigo una clave?", on_click=lambda u=url: open_external(u)
                ).props("flat")
                if has:
                    ui.button(
                        "Borrar clave",
                        on_click=lambda s=secret: (svc.secrets.delete(s), ui.navigate.reload()),
                    ).props("flat color=negative")
    if ai.provider == "ollama" or ai.fallback_provider == "ollama":
        with _section("Ollama"):
            ui.input("Dirección", value=ai.ollama_url).bind_value(ai, "ollama_url").classes(
                "w-full"
            )
            ui.input(
                "Modelo",
                value=ai.models["ollama"].estandar,
                on_change=lambda e: [
                    setattr(ai.models["ollama"], t, e.value)
                    for t in ("economico", "estandar", "maximo")
                ],
            ).classes("w-full")
            ui.button("Guardar", on_click=lambda: _saved(svc)).classes("btn-primary")
    with _section("Modelos por tarea", "Se puede cambiar sin actualizar la app."):
        for prov in ("anthropic", "gemini"):
            if prov not in (ai.provider, ai.fallback_provider):
                continue
            m = ai.models[prov]
            with ui.row().classes("w-full gap-2"):
                ui.input("Económico (resúmenes, sugerencias)", value=m.economico).bind_value(
                    m, "economico"
                ).classes("flex-1")
                ui.input("Estándar (chat)", value=m.estandar).bind_value(m, "estandar").classes(
                    "flex-1"
                )
                ui.input("Máximo", value=m.maximo).bind_value(m, "maximo").classes("flex-1")
        ui.button("Guardar", on_click=lambda: _saved(svc)).classes("btn-primary")
    with _section(
        "Sugerencias durante la clase",
        "Detecta tareas, puntos importantes y preguntas mientras "
        "grabas. Solo envía el texto nuevo y usa el modelo económico.",
    ):
        ui.switch(
            "Activar sugerencias",
            value=ai.live_suggestions,
            on_change=lambda e: (setattr(ai, "live_suggestions", e.value), _saved(svc)),
        )
        ui.label("Intervalo (minutos) si no hay palabras clave").classes("q-mt-sm")
        ui.slider(
            min=5,
            max=30,
            step=1,
            value=ai.live_interval_min,
            on_change=lambda e: (setattr(ai, "live_interval_min", int(e.value)), _saved(svc)),
        ).props("label-always")
        ui.number(
            "Máximo de llamadas por clase",
            value=ai.live_max_calls,
            min=1,
            max=100,
            on_change=lambda e: (setattr(ai, "live_max_calls", int(e.value or 12)), _saved(svc)),
        )
    with _section("Gasto"):
        used = svc.tokens_this_month()
        limit = ai.monthly_token_limit
        ui.label(
            f"Usados este mes: {used:,} tokens".replace(",", ".")
            + (f" de {limit:,}".replace(",", ".") if limit else "")
        )
        if limit:
            ui.linear_progress(value=min(1.0, used / limit), show_value=False)
        ui.number(
            "Límite mensual de tokens (vacío = sin límite)",
            value=limit,
            min=0,
            step=100000,
            on_change=lambda e: (
                setattr(ai, "monthly_token_limit", int(e.value) if e.value else None),
                _saved(svc),
            ),
        ).classes("w-full")
        ui.switch(
            "Usar procesamiento por lotes para clases pendientes (≈ 50 % más barato)",
            value=ai.use_batch_for_pending,
            on_change=lambda e: (setattr(ai, "use_batch_for_pending", e.value), _saved(svc)),
        )
        pending = len(svc.pending_llm_sessions())
        if pending and svc.ai_available:

            async def process() -> None:
                n = await run.io_bound(svc.process_pending)
                ui.notify(f"{n} clase(s) procesadas", type="positive")

            ui.button(f"Procesar {pending} clase(s) pendientes", on_click=process).classes(
                "btn-primary"
            )


# ------------------------------------------------------------------ Obsidian
def _obsidian(svc: AppServices) -> None:
    o = svc.cfg.obsidian
    with _section(
        "Relaciones automáticas",
        "Se crean al guardar cada clase, sin IA. La app nunca modifica texto escrito por ti.",
    ):
        for attr, label in (
            ("link_course", "Curso"),
            ("link_prev_next", "Clase anterior y siguiente"),
            ("link_teacher", "Profesor"),
            ("link_topics", "Temas (reutiliza tus notas existentes)"),
            ("link_related", "Clases relacionadas por similitud"),
            ("link_user_notes", "Tus notas mencionadas en clase"),
        ):
            ui.switch(
                label,
                value=getattr(o, attr),
                on_change=lambda e, a=attr: (setattr(o, a, e.value), _saved(svc)),
            )
        ui.number(
            "Máximo de clases relacionadas",
            value=o.related_max,
            min=0,
            max=20,
            on_change=lambda e: (setattr(o, "related_max", int(e.value or 0)), _saved(svc)),
        )
    with _section("Carpetas excluidas", "No se leen ni se enlazan (p. ej. Diario, Privado)."):
        chips = ui.input_chips("Carpetas", value=list(o.excluded_folders)).classes("w-full")
        ui.button(
            "Guardar",
            on_click=lambda: (setattr(o, "excluded_folders", list(chips.value)), _saved(svc)),
        ).classes("btn-primary")
    with _section(
        "Reconstruir relaciones", "Vuelve a analizar toda la bóveda (útil la primera vez)."
    ):

        async def rebuild() -> None:
            from transcriptor.links.vault import sync_vault

            n = await run.io_bound(sync_vault, svc.index, svc.vault_root, o.excluded_folders)
            ui.notify(f"{n} notas analizadas", type="positive")

        ui.button("Reconstruir relaciones", icon="sync", on_click=rebuild).classes("btn-primary")


# ------------------------------------------------------------------ Google
def _google(svc: AppServices) -> None:
    g = svc.cfg.google
    with _section(
        "Conectar con Google",
        "No hace falta para usar la app. Sirve para leer tu horario, crear "
        "recordatorios y tareas. Cada función pide su propio permiso.",
    ):
        if g.connected:
            banner("ok", "Conectado con Google ✔", "check_circle")

            async def disconnect() -> None:
                await run.io_bound(svc.disconnect_google)
                ui.navigate.reload()

            ui.button("Desconectar", on_click=disconnect).props("flat color=negative")
        else:

            async def connect() -> None:
                try:
                    ui.notify("Se abrirá tu navegador para autorizar…")
                    await run.io_bound(svc.connect_google)
                    ui.navigate.reload()
                except GoogleNotConfiguredError as exc:
                    ui.notify(str(exc), type="warning", multi_line=True)
                except Exception as exc:
                    ui.notify(f"No se pudo conectar: {exc}", type="negative")

            ui.button("Conectar con Google", icon="login", on_click=connect).classes("btn-primary")
            ui.button(
                "Exportar tareas como .ics", icon="download", on_click=lambda: _export_ics(svc)
            ).props("flat")
    with _section("Funciones"):
        ui.switch(
            "Leer mi horario (aviso «Tu clase empieza en 5 min»)",
            value=g.read_schedule,
            on_change=lambda e: (setattr(g, "read_schedule", e.value), _saved(svc)),
        )
        ui.switch(
            f"Recordatorios en el calendario «{svc.brand_calendar()}»",
            value=g.reminders,
            on_change=lambda e: (setattr(g, "reminders", e.value), _saved(svc)),
        )
        ui.switch(
            "Tareas en Google Tasks (lista «Tareas de clase»)",
            value=g.tasks,
            on_change=lambda e: (setattr(g, "tasks", e.value), _saved(svc)),
        )
        ui.number(
            "Avisar antes de clase (minutos)",
            value=g.notify_before_min,
            min=0,
            max=60,
            on_change=lambda e: (setattr(g, "notify_before_min", int(e.value or 0)), _saved(svc)),
        )
        ui.number(
            "Antelación del recordatorio (días)",
            value=g.reminder_advance_days,
            min=0,
            max=14,
            on_change=lambda e: (
                setattr(g, "reminder_advance_days", int(e.value or 0)),
                _saved(svc),
            ),
        )
        ui.switch(
            "Agregar tareas automáticamente sin preguntar",
            value=g.auto_add,
            on_change=lambda e: (setattr(g, "auto_add", e.value), _saved(svc)),
        )
        if g.connected:
            ui.label(
                "Si activas una función nueva, pulsa «Conectar con Google» otra vez para dar ese permiso."
            ).classes("small muted")
    with _section("Calendario de la universidad"):
        if g.canvas_imported:
            banner(
                "ok",
                "Se detectó un calendario importado (Canvas/Moodle) en tu Google Calendar ✔",
                "check_circle",
            )
        else:
            banner(
                "info",
                "**¿Usas Canvas?** Importa tu calendario de actividades a Google Calendar para ver tus "
                "entregas y detectar fechas que no coinciden.",
                "school",
            )
            with ui.row().classes("gap-2"):
                ui.button(
                    "Ver cómo hacerlo",
                    on_click=lambda: ui.navigate.to("/configuracion?tab=universidad"),
                ).classes("btn-primary").props("dense")
                ui.button(
                    "Ya lo hice",
                    on_click=lambda: (
                        setattr(g, "canvas_imported", True),
                        _saved(svc),
                        ui.navigate.reload(),
                    ),
                ).classes("btn-plain").props("dense")


def _export_ics(svc: AppServices) -> None:
    path = Path.home() / "Downloads" / "tareas-de-clase.ics"
    svc.export_ics(path)
    reveal_in_folder(path)
    ui.notify(f"Exportado: {path}", type="positive")


# ------------------------------------------------------------------ Universidad
def _university(svc: AppServices) -> None:
    u = svc.cfg.university
    with _section(
        "Importar el calendario de actividades",
        "Canvas, Moodle y Blackboard publican un enlace con tus entregas y exámenes.",
    ):
        platforms = {
            "canvas": "Canvas",
            "moodle": "Moodle",
            "blackboard": "Blackboard",
            "otra": "Otra",
        }

        @ui.refreshable
        def guide() -> None:
            ui.markdown("\n".join(f"{i}. {step}" for i, step in enumerate(GUIDES[u.platform], 1)))

        ui.toggle(
            platforms,
            value=u.platform,
            on_change=lambda e: (setattr(u, "platform", e.value), _saved(svc), guide.refresh()),
        )
        guide()
        ui.markdown(
            "Luego, en Google Calendar: **Otros calendarios → + → Desde URL → pega el enlace**."
        )
        link = ui.input(
            "Pega aquí el enlace (opcional, para copiarlo o leerlo localmente)",
            password=True,
            password_toggle_button=True,
        ).classes("w-full")

        async def open_google() -> None:
            if link.value:
                ui.clipboard.write(link.value)
                ui.notify("Enlace copiado. Pégalo en Google Calendar.")
            open_external(GOOGLE_ADD_BY_URL)

        async def save_local() -> None:
            try:
                n = await run.io_bound(svc.set_ics_url, link.value or None)
                link.set_value("")
                ui.notify(
                    f"Calendario guardado ({n} eventos). Se compararán las fechas con tus tareas.",
                    type="positive",
                )
                ui.navigate.reload()
            except Exception as exc:
                ui.notify(f"No se pudo leer el enlace: {exc}", type="negative")

        with ui.row().classes("gap-2"):
            ui.button("Abrir Google Calendar", icon="open_in_new", on_click=open_google).classes(
                "btn-primary"
            )
            ui.button("Leer el enlace en la app (sin Google)", on_click=save_local).classes(
                "btn-plain"
            )
        if u.has_ics_url:
            with ui.row().classes("items-center gap-2"):
                pill("Enlace guardado en el llavero", "ok")
                ui.button(
                    "Quitar", on_click=lambda: (svc.set_ics_url(None), ui.navigate.reload())
                ).props("flat dense color=negative")
    with _section("Opciones"):
        ui.switch(
            "Comparar fechas con mis tareas («Ya está en Canvas» / «Las fechas no coinciden»)",
            value=u.compare_dates,
            on_change=lambda e: (setattr(u, "compare_dates", e.value), _saved(svc)),
        )
        ui.switch(
            "Crear tareas en Google Tasks desde el calendario de la universidad",
            value=u.create_tasks_from_ics,
            on_change=lambda e: (setattr(u, "create_tasks_from_ics", e.value), _saved(svc)),
        )


# ------------------------------------------------------------------ Acerca de
def _about(svc: AppServices) -> None:
    with _section(svc.brand(), f"Versión {__version__}"):
        lic = svc.license.status()
        ui.label("Licencia: " + ("activa" if lic.active else "inactiva")).classes("muted")
        ui.label(
            "Transcripción local con Whisper (faster-whisper / MLX). Tus audios no salen del equipo."
        ).classes("muted")
        ui.button("Ver avisos de licencias de terceros", on_click=lambda: _notices_dialog()).props(
            "flat"
        )
        ui.button(
            "Abrir carpeta de registros",
            on_click=lambda: reveal_in_folder(log_dir() / "oyente.log"),
        ).props("flat")


def _notices_dialog() -> None:
    root = Path(__file__).resolve().parents[3]
    text = ""
    for candidate in (root / "THIRD_PARTY_NOTICES.md",):
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8")
    with ui.dialog() as d, ui.card().style("width:720px;max-width:95vw"):
        ui.markdown(text or "Los avisos se incluyen en el instalador.")
        ui.button("Cerrar", on_click=d.close)
    d.open()
