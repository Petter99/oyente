"""Asistente de primera vez: se puede recorrer completo sin quedar atascado en una página en blanco."""

from __future__ import annotations

import pytest
from nicegui import ui
from nicegui.testing import User

pytestmark = pytest.mark.nicegui_main_file("tests/ui/ui_main.py")


@pytest.fixture
def first_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UI_TEST_ONBOARDED", "0")


async def _skip_to_last_step(user: User) -> None:
    await user.open("/bienvenida")
    await user.should_see("Elige la carpeta")
    user.find(kind=ui.button, content="Continuar").click()  # la carpeta no se puede omitir
    for heading in ["Tus cursos", "Inteligencia artificial", "Conectar con Google", "Audio"]:
        await user.should_see(heading)
        user.find(kind=ui.button, content="Omitir").click()
    await user.should_see("Relaciones con Obsidian")


async def test_last_step_has_no_skip_button(first_run: None, user: User) -> None:
    await _skip_to_last_step(user)
    await user.should_see("Terminar")
    await user.should_not_see("Omitir")


async def test_finish_without_excluded_folders_goes_home(first_run: None, user: User) -> None:
    await _skip_to_last_step(user)
    user.find(kind=ui.button, content="Terminar").click()
    await user.should_see("Clases recientes")


async def test_finish_survives_vault_analysis_error(
    first_run: None, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    import transcriptor.links.vault as vault

    def broken(*_args: object) -> int:
        raise OSError("disco no disponible")

    monkeypatch.setattr(vault, "sync_vault", broken)
    await _skip_to_last_step(user)
    user.find(kind=ui.button, content="Terminar").click()
    await user.should_see("Clases recientes")
