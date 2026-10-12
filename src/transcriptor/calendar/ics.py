"""Calendarios ICS: lectura del calendario de la universidad (Canvas, Moodle, Blackboard)
y exportación de tareas a .ics cuando no se usa Google."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from icalendar import Calendar, Event, Todo

from transcriptor.branding import branding
from transcriptor.session.models import TaskItem

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalendarEntry:
    uid: str
    title: str
    day: date
    start: datetime | None = None
    end: datetime | None = None
    description: str = ""
    source: str = "ics"


def _as_date(value: object) -> tuple[date, datetime | None]:
    if isinstance(value, datetime):
        local = value.astimezone() if value.tzinfo else value
        return local.date(), local
    if isinstance(value, date):
        return value, None
    raise ValueError(f"fecha no reconocida: {value!r}")


def parse_ics(data: bytes | str) -> list[CalendarEntry]:
    cal = Calendar.from_ical(data)
    entries: list[CalendarEntry] = []
    for comp in cal.walk():
        if comp.name not in ("VEVENT", "VTODO"):
            continue
        raw = comp.get("DTSTART") or comp.get("DUE")
        if raw is None:
            continue
        try:
            day, start = _as_date(raw.dt)
        except ValueError:
            continue
        end_raw = comp.get("DTEND")
        end = None
        if end_raw is not None and isinstance(end_raw.dt, datetime):
            end = end_raw.dt.astimezone() if end_raw.dt.tzinfo else end_raw.dt
        entries.append(CalendarEntry(
            uid=str(comp.get("UID", "")),
            title=str(comp.get("SUMMARY", "")).strip(),
            day=day, start=start, end=end,
            description=str(comp.get("DESCRIPTION", ""))[:500],
        ))
    return entries


class IcsFeed:
    """Enlace ICS privado (contiene un token): se guarda en el llavero, nunca en la bóveda ni en logs."""

    def __init__(self, url: str, cache: Path, client: httpx.Client | None = None) -> None:
        self.url = url.replace("webcal://", "https://", 1)
        self.cache = cache
        self.client = client or httpx.Client(timeout=20.0, follow_redirects=True)

    def refresh(self) -> list[CalendarEntry]:
        try:
            r = self.client.get(self.url)
            r.raise_for_status()
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_bytes(r.content)
        except httpx.HTTPError:
            log.warning("No se pudo actualizar el calendario de la universidad; se usa la copia local")
        return self.entries()

    def entries(self) -> list[CalendarEntry]:
        if not self.cache.exists():
            return []
        try:
            return parse_ics(self.cache.read_bytes())
        except Exception:
            log.exception("Calendario de la universidad ilegible")
            return []


def export_tasks_ics(tasks: list[tuple[TaskItem, str]], path: Path, advance_days: int = 1) -> Path:
    """Tareas → archivo .ics (abre en Outlook, Calendario de Mac, Google). (tarea, curso)."""
    cal = Calendar()
    cal.add("prodid", f"-//{branding().nombre}//ES")
    cal.add("version", "2.0")
    stamp = datetime.now().astimezone()
    for task, course in tasks:
        if task.due:
            ev = Event()
            ev.add("uid", f"{task.id}@transcriptor")
            ev.add("summary", f"{course}: {task.description}")
            ev.add("dtstart", task.due)
            ev.add("dtend", task.due + timedelta(days=1))
            ev.add("dtstamp", stamp)
            ev.add("description", f"Tarea de clase ({task.due_text or 'sin fecha dicha'})")
            from icalendar import Alarm

            alarm = Alarm()
            alarm.add("action", "DISPLAY")
            alarm.add("description", task.description)
            alarm.add("trigger", timedelta(days=-advance_days))
            ev.add_component(alarm)
            cal.add_component(ev)
        else:
            todo = Todo()
            todo.add("uid", f"{task.id}@transcriptor")
            todo.add("summary", f"{course}: {task.description}")
            todo.add("dtstamp", stamp)
            cal.add_component(todo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cal.to_ical())
    return path


GUIDES = {
    "canvas": [
        "Abre Canvas y entra a «Calendario» en el menú de la izquierda.",
        "Abajo a la derecha, pulsa «Fuente de calendario» (Calendar Feed).",
        "Copia el enlace que aparece.",
    ],
    "moodle": [
        "Abre Moodle y entra a «Calendario».",
        "Pulsa «Exportar calendario» (o «Importar o exportar calendarios»).",
        "Elige «Todos los cursos» y «Eventos recientes y próximos», y pulsa «Obtener URL del calendario».",
        "Copia el enlace.",
    ],
    "blackboard": [
        "Abre Blackboard y entra a «Calendario».",
        "Pulsa el engranaje ⚙️ y luego «Compartir calendario».",
        "Copia el enlace.",
    ],
    "otra": [
        "En el calendario de tu plataforma busca «iCal», «ICS», «Exportar» o «Suscribirse».",
        "Copia el enlace que termina en .ics (o similar).",
    ],
}
GOOGLE_ADD_BY_URL = "https://calendar.google.com/calendar/r/settings/addbyurl"
