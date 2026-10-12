"""Formulario para elegir dónde se guardan las clases (asistente y Configuración).

Con Obsidian: se elige una de sus bóvedas y la app crea ahí su propia subcarpeta (es lo único a lo
que accede). Sin Obsidian: una carpeta normal. Las transcripciones (.txt) van en otra carpeta.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from nicegui import ui

from transcriptor.links.obsidian_env import (
    DOWNLOAD_URL,
    find_vault,
    list_vaults,
    obsidian_installed,
)
from transcriptor.notes.writer import VaultLayout
from transcriptor.services import AppServices
from transcriptor.ui.common import banner, open_external
from transcriptor.workspace import DEFAULT_SUBFOLDER, default_transcripts_dir, move_workspace

OTHER = "__otra__"


def workspace_form(svc: AppServices, allow_move: bool = False) -> Callable[[], bool]:
    """Dibuja el formulario y devuelve ``save()``: valida, crea las carpetas y guarda la config."""
    from transcriptor.ui.pages_settings import pick_folder

    g = svc.cfg.general
    current = Path(g.vault_path) if g.vault_path else None
    vaults = list_vaults()
    by_path = {str(v.path): v for v in vaults}

    # estado inicial a partir de la configuración actual
    choice = OTHER
    sub = DEFAULT_SUBFOLDER
    other_path = str(current) if current else str(Path.home() / "Documents" / "Clases")
    if current is not None:
        vault = find_vault(current)
        if vault is not None and str(vault) in by_path and vault != current.resolve():
            choice, sub = str(vault), current.resolve().relative_to(vault).as_posix()
    elif vaults:
        choice = str(vaults[0].path)
    state = {
        "choice": choice,
        "sub": sub,
        "other": other_path,
        "transcripts": g.transcripts_path or "",
        "last_suggested": "",
        "move": True,
    }

    def work_dir() -> Path | None:
        if state["choice"] == OTHER:
            value = state["other"].strip()
            return Path(value) if value else None
        name = state["sub"].strip().strip("/\\") or DEFAULT_SUBFOLDER
        return Path(state["choice"]) / name

    def suggested_transcripts() -> str:
        work = work_dir()
        if work is None:
            return ""
        vault = None if state["choice"] == OTHER else Path(state["choice"])
        return str(default_transcripts_dir(work, vault))

    if not obsidian_installed():
        with ui.element("div").classes("w-full"):
            banner(
                "info",
                "**Obsidian es gratis y opcional.** Con él ves el grafo de relaciones entre tus "
                "clases y abres las notas con un clic. Sin él, las notas se guardan igual como "
                "archivos .md en la carpeta que elijas.",
                "hub",
            )
            ui.button(
                "Descargar Obsidian", icon="download", on_click=lambda: open_external(DOWNLOAD_URL)
            ).props("flat")

    options: dict[str, str] = {str(v.path): f"{v.name} — {v.path}" for v in vaults}
    options[OTHER] = "Otra carpeta (sin Obsidian)"

    @ui.refreshable
    def fields() -> None:
        if state["choice"] == OTHER:
            path = ui.input(
                "Carpeta de las clases",
                value=state["other"],
                on_change=lambda e: state.update(other=e.value or ""),
            ).classes("w-full")

            async def browse() -> None:
                chosen = await pick_folder(path.value)
                if chosen:
                    path.set_value(chosen)

            ui.button("Examinar…", on_click=browse).classes("btn-plain")
        else:
            ui.input(
                "Subcarpeta dentro de la bóveda",
                value=state["sub"],
                on_change=lambda e: (state.update(sub=e.value or ""), preview.refresh()),
            ).classes("w-full")
            ui.label(
                "La app solo lee y escribe en esa subcarpeta; el resto de tu bóveda no se toca."
            ).classes("small muted")

    @ui.refreshable
    def preview() -> None:
        work = work_dir()
        if work is not None:
            ui.label(f"Las notas irán a: {work}").classes("small muted")

    def on_choice(e) -> None:  # type: ignore[no-untyped-def]
        state["choice"] = e.value
        fields.refresh()
        preview.refresh()
        if state["transcripts"] == state["last_suggested"]:  # el usuario no la cambió a mano
            state["last_suggested"] = suggested_transcripts()
            transcripts_input.set_value(state["last_suggested"])

    if vaults:
        ui.label("Bóvedas de Obsidian encontradas").style("font-weight:600")
    elif obsidian_installed():
        ui.label(
            "No encontré bóvedas en Obsidian. Crea una en Obsidian y reabre este paso, o elige una "
            "carpeta normal."
        ).classes("small muted")
    ui.radio(options, value=state["choice"], on_change=on_choice).classes("q-mt-sm")
    fields()
    preview()

    ui.label("Carpeta de transcripciones (.txt)").style("font-weight:600").classes("q-mt-md")
    ui.label(
        "Aparte de las notas: aquí se guarda el texto completo de cada clase y sus segmentos."
    ).classes("small muted")
    state["last_suggested"] = suggested_transcripts()
    transcripts_input = ui.input(
        value=state["transcripts"] or state["last_suggested"],
        on_change=lambda e: state.update(transcripts=e.value or ""),
    ).classes("w-full")
    state["transcripts"] = transcripts_input.value or ""

    async def browse_tr() -> None:
        chosen = await pick_folder(transcripts_input.value)
        if chosen:
            transcripts_input.set_value(chosen)

    ui.button("Examinar…", on_click=browse_tr).classes("btn-plain")

    if allow_move and current is not None:
        ui.checkbox(
            "Mover mis clases y transcripciones actuales a la nueva ubicación",
            value=True,
            on_change=lambda e: state.update(move=bool(e.value)),
        ).classes("q-mt-sm")

    def save() -> bool:
        work = work_dir()
        tr_text = (state["transcripts"] or "").strip()
        if work is None:
            ui.notify("Elige una carpeta para las clases", type="warning")
            return False
        if not tr_text:
            ui.notify("Elige una carpeta para las transcripciones", type="warning")
            return False
        transcripts = Path(tr_text)
        if transcripts.resolve() == work.resolve():
            ui.notify(
                "Las transcripciones deben ir en una carpeta distinta a las notas", type="warning"
            )
            return False
        old = svc.layout()
        try:
            work.mkdir(parents=True, exist_ok=True)
            transcripts.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ui.notify(f"No se pudo crear la carpeta: {exc}", type="negative")
            return False
        had_config = bool(g.vault_path)
        g.vault_path = str(work)
        g.transcripts_path = str(transcripts)
        svc.save_config()
        if allow_move and had_config and state["move"]:
            moved, skipped = move_workspace(old, VaultLayout.from_config(svc.cfg, work))
            if moved or skipped:
                ui.notify(
                    f"Se movieron {moved} carpetas"
                    + (f"; {skipped} se omitieron porque ya existían" if skipped else ""),
                    type="positive" if not skipped else "warning",
                )
        return True

    return save
