"""Interfaz con la simulación de usuario de NiceGUI (T5.1, T7.2, T10.5, T13.1)."""

from __future__ import annotations

import pytest
from nicegui.testing import User

pytestmark = pytest.mark.nicegui_main_file("tests/ui/ui_main.py")


async def test_t5_1_navigation_all_sections(user: User) -> None:
    await user.open("/")
    await user.should_see("Tareas pendientes")
    await user.should_see("Clases recientes")
    for path, text in [
        ("/clases", "Todas tus clases grabadas"),
        ("/chat", "Pregúntale a tus clases"),
        ("/cursos", "Agregar curso"),
        ("/configuracion?tab=audio", "Guardar el audio de las clases"),
        ("/configuracion?tab=ia", "Proveedor de IA"),
        ("/configuracion?tab=google", "Conectar con Google"),
        ("/configuracion?tab=universidad", "Importar el calendario de actividades"),
        ("/configuracion?tab=transcripcion", "Modelo de voz"),
        ("/configuracion?tab=hablantes", "Identificar al profesor"),
        ("/configuracion?tab=obsidian", "Relaciones automáticas"),
        ("/configuracion?tab=general", "Carpeta compartida"),
    ]:
        await user.open(path)
        await user.should_see(text)


async def test_canvas_recommendation_card(user: User) -> None:
    await user.open("/")
    await user.should_see("¿Usas Canvas?")
    user.find("Ahora no").click()
    await user.should_not_see("¿Usas Canvas?")


async def test_t10_5_chat_without_ai(user: User) -> None:
    await user.open("/chat?q=¿Qué tareas tengo?")
    await user.should_see("Tareas pendientes:")
    await user.should_see("sin IA · 0 tokens")


async def test_class_detail_shows_transcript_and_teacher_question(user: User) -> None:
    await user.open("/clases")
    from transcriptor.session.store import SessionStore

    session = SessionStore().all()[0]
    await user.open(f"/clase/{session.id}")
    await user.should_see("Transcripción")
    await user.should_see("¿Esta es la voz del profesor?")
    await user.should_see("Hoy vamos a ver integración por partes.")


async def test_new_recording_sheet(user: User) -> None:
    await user.open("/")
    user.find(marker="grabar-ahora").click()
    await user.should_see("Nueva grabación")
    await user.should_see("Solo una aplicación")
    await user.should_see("Una pestaña del navegador")
    await user.should_see("MB por hora")


async def test_t7_2_storage_estimate_updates(user: User) -> None:
    await user.open("/configuracion?tab=audio")
    await user.should_see("22 MB por hora de clase")
    user.find("FLAC — sin pérdida (idéntico a lo grabado), ≈ 60 MB por hora y canal").click()
    await user.should_see("120 MB por hora de clase")
