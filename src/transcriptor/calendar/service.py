"""Calendario y tareas: Google Calendar + Google Tasks + calendario de la universidad.

- Leer el horario: aviso «Tu clase empieza en 5 min», curso preseleccionado.
- Recordatorios en un calendario PROPIO («Clases – {nombre}»): la app no escribe en otros.
- Tareas en Google Tasks (lista «Tareas de clase»), sin duplicados, y sincronización de
  completadas hacia Obsidian.
- Cruce con Canvas/Moodle: «Ya está en Canvas» / «Las fechas no coinciden».
El LLM solo PROPONE; la fecha la resuelve el código y el usuario confirma.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from rapidfuzz import fuzz

from transcriptor.branding import branding
from transcriptor.calendar.google_auth import (
    SCOPE_APP_CALENDAR,
    SCOPE_READ_SCHEDULE,
    SCOPE_TASKS,
    GoogleAuth,
)
from transcriptor.calendar.ics import CalendarEntry, IcsFeed
from transcriptor.config.models import Course, GoogleConfig
from transcriptor.nlp.text import STOPWORDS, fold
from transcriptor.session.models import TaskItem

log = logging.getLogger(__name__)

IMPORTED_SUFFIX = "@import.calendar.google.com"
GENERIC = STOPWORDS | {"tarea", "tareas", "entrega", "hoja", "ejercicios", "trabajo", "assignment",
                       "due", "curso", "semana"}


def task_key(course: str, task: TaskItem) -> str:
    base = f"{fold(course)}|{fold(task.description)[:80]}|{task.due.isoformat() if task.due else ''}"
    return hashlib.sha1(base.encode()).hexdigest()[:16]


@dataclass
class UpcomingClass:
    course: Course
    title: str
    start: datetime
    end: datetime | None


def _content_words(text: str) -> set[str]:
    return {w for w in fold(text).split() if w not in GENERIC and len(w) > 2}


def match_entry(task: TaskItem, course: str, entries: list[CalendarEntry]) -> CalendarEntry | None:
    """Busca la misma tarea en el calendario de la universidad (título parecido, fecha cercana)."""
    best: tuple[float, CalendarEntry | None] = (0.0, None)
    words = _content_words(task.description)
    for e in entries:
        if task.due and abs((e.day - task.due).days) > 10:
            continue
        title = fold(e.title)
        score = fuzz.token_set_ratio(fold(task.description), title)
        shared = words & _content_words(e.title)
        numbers = {w for w in words if w.isdigit()} & {w for w in _content_words(e.title) if w.isdigit()}
        if numbers:
            score += 15
        if fold(course) in title or any(w in title for w in _content_words(course)):
            score += 5
        if shared or numbers:
            score = max(score, 70 + 5 * len(shared))
        if score > best[0]:
            best = (score, e)
    return best[1] if best[0] >= 75 else None


class CalendarService:
    def __init__(self, cfg: GoogleConfig, auth: GoogleAuth | None, ics: IcsFeed | None = None,
                 courses: list[Course] | None = None, build_service: Any = None) -> None:
        self.cfg = cfg
        self.auth = auth
        self.ics = ics
        self.courses = courses or []
        self._build = build_service
        self._services: dict[str, Any] = {}

    # ------------------------------------------------------------------ clientes de la API
    def _service(self, api: str, version: str, scopes: list[str]) -> Any:
        if api in self._services:
            return self._services[api]
        if self.auth is None:
            return None
        creds = self.auth.credentials(scopes)
        if creds is None:
            return None
        if self._build is not None:
            svc = self._build(api, version, credentials=creds)
        else:
            from googleapiclient.discovery import build

            svc = build(api, version, credentials=creds, cache_discovery=False)
        self._services[api] = svc
        return svc

    def calendar_api(self, scopes: list[str]) -> Any:
        return self._service("calendar", "v3", scopes)

    def tasks_api(self) -> Any:
        return self._service("tasks", "v1", SCOPE_TASKS)

    @property
    def connected(self) -> bool:
        return self.auth is not None and self.auth.credentials() is not None

    # ------------------------------------------------------------------ horario
    def calendar_entries(self, start: date, end: date) -> list[CalendarEntry]:
        """Eventos de todos los calendarios del usuario (incluye los importados de Canvas)."""
        api = self.calendar_api(SCOPE_READ_SCHEDULE) if self.cfg.read_schedule else None
        if api is None:
            return []
        out: list[CalendarEntry] = []
        cals = api.calendarList().list().execute().get("items", [])
        for cal in cals:
            items = api.events().list(
                calendarId=cal["id"], singleEvents=True, orderBy="startTime",
                timeMin=datetime.combine(start, datetime.min.time()).astimezone().isoformat(),
                timeMax=datetime.combine(end, datetime.min.time()).astimezone().isoformat(),
                maxResults=250,
            ).execute().get("items", [])
            source = "universidad" if cal["id"].endswith(IMPORTED_SUFFIX) else "google"
            for ev in items:
                s, e = ev.get("start", {}), ev.get("end", {})
                if "dateTime" in s:
                    st = datetime.fromisoformat(s["dateTime"]).astimezone()
                    en = datetime.fromisoformat(e["dateTime"]).astimezone() if "dateTime" in e else None
                    out.append(CalendarEntry(ev.get("id", ""), ev.get("summary", ""), st.date(), st, en,
                                             ev.get("description", "") or "", source))
                elif "date" in s:
                    out.append(CalendarEntry(ev.get("id", ""), ev.get("summary", ""),
                                             date.fromisoformat(s["date"]), source=source,
                                             description=ev.get("description", "") or ""))
        return out

    def university_imported(self) -> bool:
        """¿Hay un calendario suscrito por URL (p. ej. Canvas) en Google Calendar?"""
        api = self.calendar_api(SCOPE_READ_SCHEDULE)
        if api is None:
            return False
        cals = api.calendarList().list().execute().get("items", [])
        return any(c["id"].endswith(IMPORTED_SUFFIX) for c in cals)

    def course_for_event(self, title: str) -> Course | None:
        t = fold(title)
        for course in self.courses:
            keys = [course.name, *course.calendar_keywords]
            if any(fold(k) and fold(k) in t for k in keys):
                return course
            if fuzz.partial_ratio(fold(course.name), t) >= 90:
                return course
        return None

    def upcoming_classes(self, now: datetime | None = None, within_min: int = 60) -> list[UpcomingClass]:
        now = now or datetime.now().astimezone()
        entries = self.calendar_entries(now.date(), now.date() + timedelta(days=1))
        out = []
        for e in entries:
            if e.start is None or e.start < now - timedelta(minutes=10):
                continue
            if e.start > now + timedelta(minutes=within_min):
                continue
            course = self.course_for_event(e.title)
            if course is not None:
                out.append(UpcomingClass(course, e.title, e.start, e.end))
        return sorted(out, key=lambda u: u.start)

    # ------------------------------------------------------------------ calendario propio
    def ensure_app_calendar(self) -> str | None:
        api = self.calendar_api(SCOPE_APP_CALENDAR)
        if api is None:
            return None
        name = branding().calendario_google
        if self.cfg.calendar_id:
            try:
                current = api.calendars().get(calendarId=self.cfg.calendar_id).execute()
                if current.get("summary") != name:
                    # La app cambió de nombre: se renombra el calendario (no se crea otro).
                    api.calendars().patch(calendarId=self.cfg.calendar_id, body={"summary": name}).execute()
                return self.cfg.calendar_id
            except Exception:
                log.info("El calendario propio ya no existe; se crea de nuevo")
        tz = datetime.now().astimezone().tzname()
        body: dict[str, Any] = {"summary": name, "description": "Recordatorios de tareas de clase"}
        try:
            import tzlocal

            body["timeZone"] = tzlocal.get_localzone_name()
        except Exception:
            body["timeZone"] = tz or "UTC"
        created = api.calendars().insert(body=body).execute()
        self.cfg.calendar_id = created["id"]
        return self.cfg.calendar_id

    def create_reminder(self, task: TaskItem, course: str) -> str | None:
        if task.due is None:
            raise ValueError("La tarea no tiene fecha")
        cal_id = self.ensure_app_calendar()
        api = self.calendar_api(SCOPE_APP_CALENDAR)
        if cal_id is None or api is None:
            return None
        key = task_key(course, task)
        found = api.events().list(calendarId=cal_id, privateExtendedProperty=f"transcriptor_key={key}",
                                  maxResults=1).execute().get("items", [])
        if found:
            task.google_event_id = found[0]["id"]
            return task.google_event_id
        minutes = max(0, self.cfg.reminder_advance_days) * 24 * 60
        body = {
            "summary": f"{course}: {task.description}",
            "description": f"Tarea de clase. Mencionada como «{task.due_text}»." if task.due_text else "Tarea de clase.",
            "start": {"date": task.due.isoformat()},
            "end": {"date": (task.due + timedelta(days=1)).isoformat()},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": minutes or 60}]},
            "extendedProperties": {"private": {"transcriptor_key": key}},
        }
        created = api.events().insert(calendarId=cal_id, body=body).execute()
        task.google_event_id = created["id"]
        return task.google_event_id

    # ------------------------------------------------------------------ Google Tasks
    def ensure_tasklist(self) -> str | None:
        api = self.tasks_api()
        if api is None:
            return None
        name = branding().lista_tasks
        if self.cfg.tasklist_id:
            try:
                api.tasklists().get(tasklist=self.cfg.tasklist_id).execute()
                return self.cfg.tasklist_id
            except Exception:
                pass
        for tl in api.tasklists().list(maxResults=100).execute().get("items", []):
            if tl.get("title") == name:
                self.cfg.tasklist_id = tl["id"]
                return tl["id"]
        created = api.tasklists().insert(body={"title": name}).execute()
        self.cfg.tasklist_id = created["id"]
        return created["id"]

    def create_task(self, task: TaskItem, course: str, note_link: str = "") -> str | None:
        list_id = self.ensure_tasklist()
        api = self.tasks_api()
        if list_id is None or api is None:
            return None
        key = task_key(course, task)
        for existing in self._all_tasks(api, list_id):
            if f"[{key}]" in (existing.get("notes") or ""):
                task.google_task_id = existing["id"]
                return task.google_task_id
        notes = f"{course}. {note_link}\n[{key}]".strip()
        body: dict[str, Any] = {"title": f"{course}: {task.description}", "notes": notes}
        if task.due:
            body["due"] = f"{task.due.isoformat()}T00:00:00.000Z"
        created = api.tasks().insert(tasklist=list_id, body=body).execute()
        task.google_task_id = created["id"]
        return task.google_task_id

    @staticmethod
    def _all_tasks(api: Any, list_id: str, completed: bool = True) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token = None
        while True:
            resp = api.tasks().list(tasklist=list_id, showCompleted=completed, showHidden=completed,
                                    maxResults=100, pageToken=token).execute()
            items += resp.get("items", [])
            token = resp.get("nextPageToken")
            if not token:
                return items

    def completed_task_ids(self) -> set[str]:
        """IDs de Google Tasks completadas (para marcar ``- [x]`` en Obsidian)."""
        api = self.tasks_api()
        if api is None or not self.cfg.tasklist_id:
            return set()
        return {t["id"] for t in self._all_tasks(api, self.cfg.tasklist_id) if t.get("status") == "completed"}

    # ------------------------------------------------------------------ cruce con la universidad
    def university_entries(self, around: date | None = None) -> list[CalendarEntry]:
        around = around or date.today()
        entries: list[CalendarEntry] = []
        if self.ics is not None:
            entries += self.ics.entries()
        try:
            google = self.calendar_entries(around - timedelta(days=30), around + timedelta(days=120))
            entries += [e for e in google if e.source == "universidad"]
        except Exception:
            log.warning("No se pudo leer el calendario de Google para el cruce")
        return entries

    def cross_check(self, tasks: list[TaskItem], course: str = "") -> None:
        entries = self.university_entries()
        if not entries:
            return
        for task in tasks:
            entry = match_entry(task, course, entries)
            if entry is not None:
                task.canvas_match = entry.title
                task.canvas_due = entry.day
                if task.due is None:
                    task.due = entry.day
