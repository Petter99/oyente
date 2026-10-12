"""Fechas, tareas por reglas, temas locales, WER y embeddings ligeros."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from transcriptor.asr.base import Segment
from transcriptor.asr.wer import wer
from transcriptor.nlp.dates import resolve
from transcriptor.nlp.embed import HashingEmbedder
from transcriptor.nlp.keywords import tfidf_keywords
from transcriptor.nlp.tasks_rules import extract_tasks, live_trigger
from transcriptor.nlp.text import contains_phrase, fold

MONDAY = date(2026, 10, 12)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("para el próximo jueves", date(2026, 10, 15)),
        ("el jueves", date(2026, 10, 15)),
        ("el lunes", date(2026, 10, 19)),  # el mismo día de la semana → la semana siguiente
        ("jueves de la próxima semana", date(2026, 10, 22)),
        ("el 19 de octubre", date(2026, 10, 19)),
        ("19/10", date(2026, 10, 19)),
        ("3 de enero", date(2027, 1, 3)),
        ("mañana", date(2026, 10, 13)),
        ("pasado mañana", date(2026, 10, 14)),
        ("en dos semanas", date(2026, 10, 26)),
        ("dentro de 3 días", date(2026, 10, 15)),
        ("la próxima semana", date(2026, 10, 19)),
        ("viernes 23", date(2026, 10, 23)),
        ("Para el MIÉRCOLES", date(2026, 10, 14)),
        ("cuando puedan", None),
    ],
)
def test_resolve_spanish_dates(text: str, expected: date | None) -> None:
    assert resolve(text, MONDAY) == expected


def test_rule_based_tasks() -> None:
    segs = [
        Segment(0, 5, "Hoy vemos integrales.", speaker="Profesor"),
        Segment(10, 15, "La tarea es la hoja de ejercicios cuatro.", speaker="Profesor"),
        Segment(15, 18, "Para el próximo jueves.", speaker="Profesor"),
        Segment(20, 25, "El parcial es el 19 de octubre.", speaker="Profesor"),
        Segment(30, 33, "Yo tengo que entregar algo para el jueves.", speaker="Yo"),
    ]
    tasks = extract_tasks(segs, MONDAY)
    assert [t.due for t in tasks] == [date(2026, 10, 15), date(2026, 10, 19)]
    assert tasks[1].kind == "examen"
    assert all(not t.confirmed for t in tasks)
    assert tasks[0].t == 10


def test_live_trigger() -> None:
    assert live_trigger("No olviden que esto viene en el examen")
    assert live_trigger("La tarea es para el jueves")
    assert not live_trigger("Ahora derivamos la función")


def test_tfidf_topics() -> None:
    text = (
        "Hoy vemos integración por partes. La integración por partes sirve con productos. "
        "La regla LIATE ayuda. Con la regla LIATE elegimos u. Producto de funciones. "
        "Producto de funciones otra vez con integración por partes."
    )
    topics = tfidf_keywords(text)
    assert topics[:3] == ["Integración por partes", "Regla LIATE", "Producto de funciones"] or set(
        topics[:3]
    ) == {"Integración por partes", "Regla LIATE", "Producto de funciones"}


def test_wer() -> None:
    assert wer("la tarea es para el jueves", "la tarea es para el jueves") == 0
    assert wer("la tarea es para el jueves", "la tarea para el jueves") == pytest.approx(1 / 6)
    assert wer("Qué función", "que funcion", strip_accents=True) == 0


def test_fold_and_contains() -> None:
    assert fold("Integración") == "integracion"
    assert contains_phrase(fold("vimos la Regla LIATE hoy"), "regla liate")
    assert not contains_phrase(fold("integraciones"), "integración")


def test_t10_6_hashing_embedder_similarity() -> None:
    emb = HashingEmbedder()
    v = emb.embed(
        [
            "integración por partes de logaritmos",
            "integrales por partes con logaritmos",
            "leyes de Newton y fuerzas",
        ]
    )
    assert np.allclose(np.linalg.norm(v, axis=1), 1, atol=1e-5)
    assert v[0] @ v[1] > v[0] @ v[2] + 0.2
