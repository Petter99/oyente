"""Sugerencia de curso y profesor (Google Calendar + IA)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from transcriptor.calendar.ics import CalendarEntry
from transcriptor.calendar.service import CalendarService
from transcriptor.config.models import Course, GoogleConfig
from transcriptor.nlp.course_info import course_from_title, teacher_from_text
from transcriptor.session.models import ClassSummary
from transcriptor.session.suggest import build_suggestion


def _entry(title: str, description: str = "") -> CalendarEntry:
    now = datetime.now().astimezone()
    return CalendarEntry("1", title, now.date(), now, now + timedelta(hours=1), description, "google")


def test_teacher_from_text() -> None:
    assert teacher_from_text("Profesor: Juan Pérez López") == "Juan Pérez López"
    assert teacher_from_text("Aula 3. Ing. Marta Ruiz") == "Ing. Marta Ruiz"
    assert teacher_from_text("Cálculo II") == ""


def test_course_from_title() -> None:
    assert course_from_title("Cálculo II - Aula 3") == "Cálculo II"
    assert course_from_title("Física (Clase)") == "Física"


def test_new_course_from_calendar() -> None:
    mate = Course(name="Matemáticas")
    sug = build_suggestion(
        mate, [mate], _entry("Cálculo II - Aula 3", "Profesor: Juan Pérez"), None, use_ai=False
    )
    assert sug is not None
    assert (sug.course_name, sug.teacher, sug.course_id, sug.source) == (
        "Cálculo II",
        "Juan Pérez",
        None,
        "calendario",
    )


def test_teacher_for_existing_course_only_when_new() -> None:
    calc = Course(name="Cálculo II")
    entry = _entry("Cálculo II", "Docente: Juan Pérez")
    sug = build_suggestion(calc, [calc], entry, None, use_ai=False)
    assert sug is not None and sug.course_id == calc.id and sug.teacher == "Juan Pérez"
    calc.teacher = "Juan Perez"  # mismo nombre sin tilde: no se vuelve a preguntar
    assert build_suggestion(calc, [calc], entry, None, use_ai=False) is None


def test_ai_used_only_when_enabled() -> None:
    summary = ClassSummary(source="llm", suggested_course="Historia", suggested_teacher="Ana Gil")
    base = Course(name="Otra")
    assert build_suggestion(base, [base], None, summary, use_ai=False) is None
    sug = build_suggestion(base, [base], None, summary, use_ai=True)
    assert sug is not None and sug.source == "ia" and sug.teacher == "Ana Gil"


def test_nothing_detected_no_suggestion() -> None:
    c = Course(name="X")
    assert build_suggestion(c, [c], None, ClassSummary(source="llm"), use_ai=True) is None


def test_event_during_picks_largest_overlap() -> None:
    now = datetime.now().astimezone().replace(microsecond=0)
    short = CalendarEntry("a", "Café", date.today(), now, now + timedelta(minutes=5), "", "google")
    long = CalendarEntry("b", "Física", date.today(), now, now + timedelta(hours=1), "", "google")
    canvas = CalendarEntry("c", "Tarea 3", date.today(), now, now + timedelta(hours=2), "", "universidad")
    svc = CalendarService(GoogleConfig(), None)
    svc.calendar_entries = lambda *_: [short, long, canvas]  # type: ignore[method-assign]
    found = svc.event_during(now, now + timedelta(minutes=50))
    assert found is long
