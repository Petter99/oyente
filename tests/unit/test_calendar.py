"""Google Calendar + Tasks (API simulada) y calendarios de la universidad (T12.*)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from icalendar import Calendar

from transcriptor.calendar.google_auth import (
    SCOPE_APP_CALENDAR,
    SCOPE_READ_SCHEDULE,
    SCOPE_TASKS,
    scopes_for,
)
from transcriptor.calendar.ics import IcsFeed, export_tasks_ics, parse_ics
from transcriptor.calendar.service import CalendarService, match_entry
from transcriptor.config.models import Course, GoogleConfig
from transcriptor.logging_setup import redact
from transcriptor.session.models import TaskItem

FIX = Path(__file__).parents[1] / "fixtures" / "ics"


# ----------------------------------------------------------------------------- API simulada
class Call:
    def __init__(self, fn: Any) -> None:
        self.fn = fn

    def execute(self) -> Any:
        return self.fn()


class FakeGoogle:
    """Imita la forma de googleapiclient: service.recurso().método(...).execute()."""

    def __init__(self) -> None:
        self.calendars_db: dict[str, dict] = {
            "primary": {"id": "primary", "summary": "Personal"},
            "canvas@import.calendar.google.com": {
                "id": "canvas@import.calendar.google.com",
                "summary": "Canvas",
            },
        }
        self.events_db: dict[str, list[dict]] = {
            "primary": [],
            "canvas@import.calendar.google.com": [],
        }
        self.tasklists_db: list[dict] = []
        self.tasks_db: dict[str, list[dict]] = {}
        self.inserts = 0

    # Calendar
    def calendarList(self) -> Any:
        return type(
            "R",
            (),
            {"list": lambda s, **k: Call(lambda: {"items": list(self.calendars_db.values())})},
        )()

    def calendars(self) -> Any:
        fake = self

        class R:
            def get(self, calendarId: str) -> Call:
                return Call(lambda: fake.calendars_db[calendarId])

            def insert(self, body: dict) -> Call:
                def run() -> dict:
                    cid = f"app{len(fake.calendars_db)}@group.calendar.google.com"
                    fake.calendars_db[cid] = {"id": cid, **body}
                    fake.events_db[cid] = []
                    return fake.calendars_db[cid]

                return Call(run)

            def patch(self, calendarId: str, body: dict) -> Call:
                return Call(
                    lambda: (
                        fake.calendars_db[calendarId].update(body) or fake.calendars_db[calendarId]
                    )
                )

        return R()

    def events(self) -> Any:
        fake = self

        class R:
            def list(
                self, calendarId: str, privateExtendedProperty: str | None = None, **kw: Any
            ) -> Call:
                def run() -> dict:
                    items = fake.events_db[calendarId]
                    if privateExtendedProperty:
                        k, v = privateExtendedProperty.split("=")
                        items = [
                            e
                            for e in items
                            if e.get("extendedProperties", {}).get("private", {}).get(k) == v
                        ]
                    return {"items": items}

                return Call(run)

            def insert(self, calendarId: str, body: dict) -> Call:
                def run() -> dict:
                    fake.inserts += 1
                    ev = {"id": f"ev{fake.inserts}", **body}
                    fake.events_db[calendarId].append(ev)
                    return ev

                return Call(run)

        return R()

    # Tasks
    def tasklists(self) -> Any:
        fake = self

        class R:
            def list(self, **kw: Any) -> Call:
                return Call(lambda: {"items": fake.tasklists_db})

            def get(self, tasklist: str) -> Call:
                def run() -> dict:
                    found = [t for t in fake.tasklists_db if t["id"] == tasklist]
                    if not found:
                        raise KeyError(tasklist)
                    return found[0]

                return Call(run)

            def insert(self, body: dict) -> Call:
                def run() -> dict:
                    tl = {"id": f"tl{len(fake.tasklists_db) + 1}", **body}
                    fake.tasklists_db.append(tl)
                    fake.tasks_db[tl["id"]] = []
                    return tl

                return Call(run)

        return R()

    def tasks(self) -> Any:
        fake = self

        class R:
            def list(self, tasklist: str, **kw: Any) -> Call:
                return Call(lambda: {"items": fake.tasks_db[tasklist]})

            def insert(self, tasklist: str, body: dict) -> Call:
                def run() -> dict:
                    fake.inserts += 1
                    t = {"id": f"t{fake.inserts}", "status": "needsAction", **body}
                    fake.tasks_db[tasklist].append(t)
                    return t

                return Call(run)

        return R()


class FakeAuth:
    def __init__(self, scopes: list[str]) -> None:
        self.scopes = scopes

    def credentials(self, needed: list[str] | None = None) -> Any:
        if needed and not set(needed) <= set(self.scopes):
            return None
        return object()


def service(
    google: FakeGoogle,
    scopes: list[str] | None = None,
    cfg: GoogleConfig | None = None,
    ics: IcsFeed | None = None,
) -> CalendarService:
    scopes = (
        scopes if scopes is not None else SCOPE_READ_SCHEDULE + SCOPE_APP_CALENDAR + SCOPE_TASKS
    )
    return CalendarService(
        cfg or GoogleConfig(connected=True),
        FakeAuth(scopes),
        ics,  # type: ignore[arg-type]
        [Course(name="Cálculo II", calendar_keywords=["CAL-II"]), Course(name="Física I")],
        build_service=lambda api, version, credentials: google,
    )


TASK = TaskItem(
    description="Hoja de ejercicios 4, integrales por partes",
    due=date(2026, 10, 15),
    due_text="el próximo jueves",
)


# ----------------------------------------------------------------------------- pruebas
def test_t12_1_reminder_and_task_without_duplicates() -> None:
    google = FakeGoogle()
    svc = service(google)
    first = svc.create_reminder(TASK.model_copy(), "Cálculo II")
    second = svc.create_reminder(TASK.model_copy(), "Cálculo II")
    assert first == second and google.inserts == 1
    cal = google.calendars_db[svc.cfg.calendar_id]  # type: ignore[index]
    assert cal["summary"] == "Clases – Oyente"
    ev = google.events_db[svc.cfg.calendar_id][0]  # type: ignore[index]
    assert ev["start"] == {"date": "2026-10-15"}
    assert ev["reminders"]["overrides"][0]["minutes"] == 24 * 60

    t1 = svc.create_task(TASK.model_copy(), "Cálculo II", "obsidian://open?file=x")
    t2 = svc.create_task(TASK.model_copy(), "Cálculo II")
    assert t1 == t2
    tl = google.tasklists_db[0]
    assert tl["title"] == "Tareas de clase"
    assert google.tasks_db[tl["id"]][0]["due"] == "2026-10-15T00:00:00.000Z"


def test_reminder_only_writes_in_app_calendar() -> None:
    google = FakeGoogle()
    svc = service(google)
    svc.create_reminder(TASK.model_copy(), "Cálculo II")
    assert google.events_db["primary"] == []


def test_t12_2_only_requested_scopes() -> None:
    cfg = GoogleConfig(read_schedule=True, reminders=False, tasks=True)
    assert scopes_for(cfg) == SCOPE_READ_SCHEDULE + SCOPE_TASKS
    # Sin permiso de calendario propio, no se intenta crear nada.
    svc = service(FakeGoogle(), scopes=SCOPE_READ_SCHEDULE)
    assert svc.create_reminder(TASK.model_copy(), "Cálculo II") is None


def test_renaming_app_renames_calendar_not_duplicates() -> None:
    google = FakeGoogle()
    svc = service(google)
    cid = svc.ensure_app_calendar()
    google.calendars_db[cid]["summary"] = "Clases – NombreViejo"  # type: ignore[index]
    assert service(google, cfg=svc.cfg).ensure_app_calendar() == cid
    assert google.calendars_db[cid]["summary"] == "Clases – Oyente"  # type: ignore[index]


@pytest.mark.parametrize("name", ["canvas", "moodle", "blackboard"])
def test_t12_3_parse_university_ics(name: str) -> None:
    entries = parse_ics((FIX / f"{name}.ics").read_bytes())
    assert entries and all(e.title for e in entries)


def test_ics_timezones_and_all_day() -> None:
    entries = {e.uid: e for e in parse_ics((FIX / "canvas.ics").read_bytes())}
    assert entries["event-assignment-90001"].day == date(2026, 10, 16)
    assert entries["event-assignment-90001"].start is None
    clase = entries["event-calendar-event-777"]
    assert clase.start is not None and clase.start.tzinfo is not None


def test_t12_4_cross_check_title_and_dates() -> None:
    entries = parse_ics((FIX / "canvas.ics").read_bytes())
    match = match_entry(
        TaskItem(description="Hoja 4 de integrales", due=date(2026, 10, 15)), "Cálculo II", entries
    )
    assert match is not None and match.title.startswith("Tarea 4")
    assert match.day == date(2026, 10, 16)  # «el profesor dijo 15, Canvas dice 16»
    assert (
        match_entry(
            TaskItem(description="Leer capítulo 9", due=date(2026, 10, 15)), "Cálculo II", entries
        )
        is None
    )


def test_cross_check_marks_tasks(tmp_path: Path) -> None:
    cache = tmp_path / "ics.cache"
    cache.write_bytes((FIX / "canvas.ics").read_bytes())
    svc = service(FakeGoogle(), ics=IcsFeed("https://x", cache))
    tasks = [
        TaskItem(description="Hoja 4 de integrales", due=date(2026, 10, 15)),
        TaskItem(description="Parcial", due=None, kind="examen"),
    ]
    svc.cross_check(tasks, "Cálculo II")
    assert tasks[0].canvas_due == date(2026, 10, 16)
    assert tasks[1].due == date(2026, 10, 19)  # la fecha oficial completa la que faltaba


def test_upcoming_class_detection() -> None:
    google = FakeGoogle()
    now = datetime(2026, 10, 12, 17, 55).astimezone()
    google.events_db["primary"].append(
        {
            "id": "c1",
            "summary": "Cálculo II (sección A)",
            "start": {"dateTime": (now + timedelta(minutes=5)).isoformat()},
            "end": {"dateTime": (now + timedelta(minutes=95)).isoformat()},
        }
    )
    up = service(google).upcoming_classes(now=now)
    assert len(up) == 1 and up[0].course.name == "Cálculo II"


def test_university_calendar_imported_is_detected() -> None:
    assert service(FakeGoogle()).university_imported()


def test_t12_7_export_ics_without_google(tmp_path: Path) -> None:
    out = export_tasks_ics(
        [(TASK, "Cálculo II"), (TaskItem(description="Leer"), "Física I")], tmp_path / "tareas.ics"
    )
    cal = Calendar.from_ical(out.read_bytes())
    names = [c.name for c in cal.walk()]
    assert "VEVENT" in names and "VTODO" in names and "VALARM" in names


def test_t12_9_ics_url_never_logged(tmp_path: Path) -> None:
    url = "https://canvas.ejemplo.edu/feeds/calendars/user_SECRETO123.ics"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=(FIX / "canvas.ics").read_bytes())

    feed = IcsFeed(
        url.replace("https://", "webcal://"),
        tmp_path / "c.ics",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert len(feed.refresh()) == 3
    assert calls[0].startswith("https://")
    assert "SECRETO123" not in redact(f"descargando {url}")


def test_google_token_roundtrip_in_keyring() -> None:
    from transcriptor.calendar.google_auth import GoogleAuth
    from transcriptor.config.secrets import Secret, SecretStore

    secrets = SecretStore()
    secrets.set(
        Secret.GOOGLE_TOKEN,
        json.dumps(
            {
                "token": "ya29.abc",
                "refresh_token": "1//refresh",
                "client_id": "id",
                "client_secret": "s",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": SCOPE_TASKS,
                "expiry": (datetime.utcnow() + timedelta(hours=1)).isoformat(),
            }
        ),
    )
    auth = GoogleAuth(secrets)
    assert auth.credentials(SCOPE_TASKS) is not None
    assert auth.credentials(SCOPE_APP_CALENDAR) is None  # permiso no concedido todavía
    auth._creds = None
    secrets.delete(Secret.GOOGLE_TOKEN)
    assert auth.credentials() is None
