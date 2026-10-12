"""«Posibles tareas» sin LLM: frases con palabras clave + fecha. Se marcan «a confirmar»."""

from __future__ import annotations

import re
from datetime import date

from transcriptor.asr.base import Segment
from transcriptor.nlp.dates import find_date_phrase, resolve
from transcriptor.nlp.text import fold
from transcriptor.session.models import TaskItem

TASK_WORDS = re.compile(
    r"\b(tarea|tareas|entregar|entrega|entreguen|deber|deberes|hoja de (?:ejercicios|trabajo)|"
    r"ejercicios|laboratorio|proyecto|investigar|investiguen|leer|lean|lectura|ensayo|informe|"
    r"resumen|exposicion|presentacion|trabajo|practica|subir|suban)\b"
)
EXAM_WORDS = re.compile(r"\b(examen|parcial|quiz|prueba corta|evaluacion|control de lectura)\b")
TRIGGER_WORDS = re.compile(
    r"\b(para el|para la|hasta el|antes del|no olviden|recuerden|tienen que|deben|hay que|queda de)\b"
)


def is_task_sentence(text: str) -> tuple[bool, str]:
    t = fold(text)
    if EXAM_WORDS.search(t) and (find_date_phrase(t) or TRIGGER_WORDS.search(t)):
        return True, "examen"
    if TASK_WORDS.search(t) and (find_date_phrase(t) or TRIGGER_WORDS.search(t)):
        kind = "lectura" if re.search(r"\b(leer|lean|lectura)\b", t) else "tarea"
        return True, kind
    return False, ""


def extract_tasks(segments: list[Segment], class_day: date) -> list[TaskItem]:
    """Tareas candidatas a partir de la transcripción (solo frases del profesor o de la clase)."""
    tasks: list[TaskItem] = []
    seen: set[str] = set()
    for i, seg in enumerate(segments):
        if seg.speaker == "Yo":
            continue
        ok, kind = is_task_sentence(seg.text)
        if not ok and i + 1 < len(segments) and TASK_WORDS.search(fold(seg.text)):
            ok, kind = is_task_sentence(f"{seg.text} {segments[i + 1].text}")
        if not ok:
            continue
        # La fecha puede venir en la frase siguiente («…la hoja cuatro. Para el jueves.»).
        context = seg.text
        phrase = find_date_phrase(context)
        if phrase is None and i + 1 < len(segments):
            nxt = segments[i + 1].text
            if find_date_phrase(nxt):
                context = f"{seg.text} {nxt}"
                phrase = find_date_phrase(nxt)
        key = fold(seg.text)[:80]
        if key in seen:
            continue
        seen.add(key)
        tasks.append(TaskItem(
            description=seg.text.strip(),
            due_text=phrase or "",
            due=resolve(phrase, class_day) if phrase else None,
            t=seg.start,
            confirmed=False,
            kind=kind,  # type: ignore[arg-type]
        ))
    return tasks


def live_trigger(text: str) -> bool:
    """Disparador local (sin tokens) de las sugerencias en vivo."""
    t = fold(text)
    return bool(
        (TASK_WORDS.search(t) and TRIGGER_WORDS.search(t))
        or EXAM_WORDS.search(t)
        or re.search(r"\b(importante|no olviden|esto viene|va a venir|para el proximo|ojo con)\b", t)
    )
