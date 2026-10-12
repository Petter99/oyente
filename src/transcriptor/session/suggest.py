"""Sugerencia de curso y profesor al terminar una clase.

Fuentes, en orden de confianza: el evento de Google Calendar que coincide con la hora de la
clase y, si hay IA, lo que dedujo el modelo de la transcripción. Solo se PROPONE; el usuario
confirma (o edita) desde la clase.
"""

from __future__ import annotations

from rapidfuzz import fuzz

from transcriptor.calendar.ics import CalendarEntry
from transcriptor.config.models import Course
from transcriptor.nlp.course_info import course_from_title, teacher_from_text
from transcriptor.nlp.text import fold
from transcriptor.session.models import ClassSummary, CourseSuggestion

SAME_NAME = 85
SAME_PERSON = 80


def same_name(a: str, b: str) -> bool:
    fa, fb = fold(a).strip(), fold(b).strip()
    return bool(fa and fb) and (fa == fb or fuzz.token_set_ratio(fa, fb) >= SAME_NAME)


def calendar_hint(entry: CalendarEntry | None) -> str:
    """Texto del evento para dárselo al modelo como contexto."""
    if entry is None:
        return ""
    desc = " ".join((entry.description or "").split())[:300]
    return f"{entry.title}" + (f" — {desc}" if desc else "")


def find_course(name: str, courses: list[Course]) -> Course | None:
    return next((c for c in courses if same_name(c.name, name)), None)


def build_suggestion(
    session_course: Course,
    courses: list[Course],
    entry: CalendarEntry | None,
    summary: ClassSummary | None,
    use_ai: bool,
) -> CourseSuggestion | None:
    """Devuelve la propuesta, o None si no aporta nada nuevo."""
    name = teacher = ""
    source = "ia"
    if entry is not None:
        name = course_from_title(entry.title)
        teacher = teacher_from_text(f"{entry.title}\n{entry.description}")
        if name:
            source = "calendario"
    if use_ai and summary is not None:
        if not name:
            name = summary.suggested_course
        if not teacher:
            teacher = summary.suggested_teacher
    name, teacher = name.strip(), teacher.strip()
    if not name and not teacher:
        return None
    if not name:
        name = session_course.name  # solo se detectó el profesor

    existing = find_course(name, courses)
    if existing is None:
        return CourseSuggestion(course_name=name, teacher=teacher, source=source)  # type: ignore[arg-type]
    if not teacher:
        return None
    if existing.teacher and fuzz.token_set_ratio(fold(existing.teacher), fold(teacher)) >= SAME_PERSON:
        return None
    return CourseSuggestion(
        course_name=existing.name, teacher=teacher, course_id=existing.id, source=source  # type: ignore[arg-type]
    )
