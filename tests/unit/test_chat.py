"""Búsqueda y chat (T10.*)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tests.fakes import FakeLLM
from transcriptor.chat.service import ChatService
from transcriptor.llm.composite import NullLLMProvider
from transcriptor.nlp.embed import HashingEmbedder
from transcriptor.search.index import SearchIndex
from transcriptor.session.models import TaskItem


@pytest.fixture
def index(tmp_path: Path) -> SearchIndex:
    idx = SearchIndex(tmp_path / "i.sqlite", HashingEmbedder())
    idx.upsert_note(path="Clases/Cálculo II/2026-10-12 Integración.md", title="2026-10-12 Integración",
                    kind="clase", owned=True, mtime=1, course="Cálculo II", day="2026-10-12",
                    summary="Integración por partes y la regla LIATE",
                    chunks=[("resumen", None, None, "Se explicó la integración por partes usando la regla LIATE."),
                            ("transcripcion", 872.0, "Profesor",
                             "La regla LIATE nos dice que primero se eligen los logaritmos como u.")])
    idx.upsert_note(path="Clases/Física I/2026-10-08 Newton.md", title="2026-10-08 Newton",
                    kind="clase", owned=True, mtime=1, course="Física I", day="2026-10-08",
                    summary="Leyes de Newton",
                    chunks=[("resumen", None, None, "Se vieron las tres leyes de Newton y la fuerza neta."),
                            ("transcripcion", 300.0, "Profesor", "La segunda ley dice que fuerza es masa por aceleración.")])
    idx.upsert_tasks([TaskItem(description="Hoja 4", due=date(2026, 10, 15), t=900.0)],
                     session_id="s1", course="Cálculo II", note_path="Clases/Cálculo II/2026-10-12 Integración.md")
    idx.upsert_tasks([TaskItem(description="Informe de laboratorio", due=date(2026, 10, 30), confirmed=False)],
                     session_id="s2", course="Física I", note_path="Clases/Física I/2026-10-08 Newton.md")
    return idx


def test_t10_1_tasks_answered_without_llm(index: SearchIndex) -> None:
    llm = FakeLLM()
    chat = ChatService(index, llm, ["Cálculo II", "Física I"])
    ans = chat.ask("¿Qué tareas tengo de Cálculo esta semana?", today=date(2026, 10, 12))
    assert ans.intent == "tareas" and not ans.used_llm and ans.tokens == 0
    assert "Hoja 4" in ans.text and "Informe" not in ans.text
    assert not llm.calls
    all_tasks = chat.ask("¿Qué tengo pendiente?", today=date(2026, 10, 12))
    assert "Informe de laboratorio" in all_tasks.text and "(a confirmar)" in all_tasks.text


def test_open_question_sends_few_fragments(index: SearchIndex) -> None:
    llm = FakeLLM()
    chat = ChatService(index, llm, ["Cálculo II", "Física I"])
    ans = chat.ask("¿Cómo explicó el profe la regla LIATE?")
    assert ans.used_llm and ans.intent == "pregunta"
    sent = llm.calls[0][0].messages[-1].content
    assert "LIATE" in sent and len(sent) < 4 * 4000  # T10.4: pocos tokens
    assert ans.citations and ans.citations[0].course == "Cálculo II"


def test_t10_2_search_finds_right_class(index: SearchIndex) -> None:
    hits = index.search("segunda ley fuerza masa")
    assert hits[0].course == "Física I"
    hits = index.search("logaritmos LIATE", course="Cálculo II")
    assert all(h.course == "Cálculo II" for h in hits)


def test_t10_5_search_mode_without_api_key(index: SearchIndex) -> None:
    chat = ChatService(index, NullLLMProvider(), ["Cálculo II"])
    ans = chat.ask("¿Qué es la regla LIATE?")
    assert not ans.used_llm and ans.text.startswith("Modo búsqueda")
    assert "Cálculo II" in ans.text


def test_t10_6_accent_insensitive_search(index: SearchIndex) -> None:
    assert index.search("integracion por partes")[0].course == "Cálculo II"


def test_reminder_intent(index: SearchIndex) -> None:
    chat = ChatService(index, FakeLLM(), [])
    ans = chat.ask("Recuérdame la hoja 4 un día antes")
    assert ans.intent == "recordatorio" and ans.reminder_request


def test_citation_link_points_to_minute(index: SearchIndex) -> None:
    chat = ChatService(index, NullLLMProvider(), ["Cálculo II"])
    ans = chat.ask("logaritmos como u")
    link = next(c for c in ans.citations if c.t is not None).obsidian_link
    assert "#^t-00-14-32" in link
