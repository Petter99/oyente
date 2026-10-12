"""Modelo de una sesión de grabación (una clase)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from transcriptor.config.models import SourceKind

SessionStatus = Literal["recording", "finalizing", "done", "error"]


class Marker(BaseModel):
    t: float
    label: str = "Momento marcado"


class TaskItem(BaseModel):
    """Tarea de clase (extraída por el LLM o por reglas)."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    description: str
    due_text: str = ""
    due: date | None = None
    t: float | None = None  # minuto de la clase donde se mencionó
    confirmed: bool = True  # False = «posible tarea, a confirmar» (modo sin API key)
    done: bool = False
    kind: Literal["tarea", "examen", "lectura", "otro"] = "tarea"
    google_task_id: str | None = None
    google_event_id: str | None = None
    canvas_match: str | None = None  # título del evento de Canvas que coincide
    canvas_due: date | None = None


class KeyPoint(BaseModel):
    text: str
    t: float | None = None


class CourseSuggestion(BaseModel):
    """Curso y profesor detectados (Google Calendar o IA). Nunca se aplican sin preguntar."""

    course_name: str
    teacher: str = ""
    course_id: str | None = None  # curso existente al que corresponde (None = crear uno nuevo)
    source: Literal["calendario", "ia"] = "ia"
    status: Literal["pending", "accepted", "dismissed"] = "pending"


class ClassSummary(BaseModel):
    title: str = ""
    summary: str = ""
    key_points: list[KeyPoint] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    tasks: list[TaskItem] = Field(default_factory=list)
    source: Literal["llm", "local"] = "local"
    suggested_course: str = ""
    suggested_teacher: str = ""


class RecordingSession(BaseModel):
    id: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    )
    course_id: str
    course_name: str
    teacher: str = ""
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime | None = None
    status: SessionStatus = "recording"

    source_kind: SourceKind = "app"
    source_label: str = ""
    include_mic: bool = True

    work_dir: str = ""  # carpeta local de la sesión (audio, segmentos)
    transcript_dir: str = ""  # carpeta .transcriptor/<fecha> dentro de la bóveda
    note_path: str = ""  # ruta de la nota .md
    audio_files: list[str] = Field(default_factory=list)
    audio_deleted: bool = False
    audio_expires_at: datetime | None = None

    markers: list[Marker] = Field(default_factory=list)
    asr_model_live: str = ""
    asr_model_final: str = ""
    final_pass_ok: bool = False
    speaker_embedder: str = ""
    teacher_needs_confirmation: bool = False
    teacher_example_t: float | None = None
    low_confidence_words: int = 0

    suggestion: CourseSuggestion | None = None
    summary: ClassSummary | None = None
    llm_processed: bool = False
    llm_tokens: int = 0
    live_insights: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def day(self) -> date:
        return self.started_at.date()

    @property
    def duration_s(self) -> float:
        end = self.ended_at or datetime.now()
        return max(0.0, (end - self.started_at).total_seconds())

    @property
    def title(self) -> str:
        if self.summary and self.summary.title:
            return self.summary.title
        return self.course_name
