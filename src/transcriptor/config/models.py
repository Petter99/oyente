"""Modelo de configuración. Nunca contiene secretos (van al llavero, ver secrets.py)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

CONFIG_VERSION = 2

SourceKind = Literal["app", "tab", "system", "mic"]
RetentionMode = Literal["always", "days", "never"]
AudioFormat = Literal["opus", "flac"]
ProviderName = Literal["none", "anthropic", "gemini", "ollama"]
Tier = Literal["economico", "estandar", "maximo"]


class RetentionConfig(BaseModel):
    mode: RetentionMode = "days"
    days: int = Field(30, ge=1, le=3650)


class Course(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    teacher: str = ""
    language: str = "es"
    glossary: list[str] = Field(default_factory=list)
    # Sobrescrituras por curso (None = usar el ajuste global).
    retention: RetentionConfig | None = None
    audio_format: AudioFormat | None = None
    last_source: SourceKind | None = None
    last_app: str | None = None
    # Palabras para reconocer el curso en eventos del calendario.
    calendar_keywords: list[str] = Field(default_factory=list)


class GeneralConfig(BaseModel):
    vault_path: str | None = None
    classes_folder: str = "Clases"
    topics_folder: str = "Temas"
    teachers_folder: str = "Profesores"
    start_at_login: bool = False
    global_hotkey: bool = True


class AudioConfig(BaseModel):
    default_source: SourceKind = "app"
    preferred_app: str | None = None
    include_mic: bool = True
    mic_device: str | None = None
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    format: AudioFormat = "opus"
    store_in_vault: bool = False
    extension_hint_dismissed: bool = False


class TranscriptionConfig(BaseModel):
    model: str = "auto"
    language: str = "es"
    beam_size: int = 5
    keep_live_txt: bool = True
    low_confidence: float = Field(0.45, ge=0, le=1)


class SpeakersConfig(BaseModel):
    distinguish_participants: bool = True
    # None = umbral recomendado del extractor de voz en uso.
    teacher_threshold: float | None = Field(None, ge=0, le=1)
    use_pyannote: bool = False


class ProviderModels(BaseModel):
    economico: str
    estandar: str
    maximo: str


def _default_models() -> dict[str, ProviderModels]:
    return {
        "anthropic": ProviderModels(
            economico="claude-haiku-5-5", estandar="claude-sonnet-5-5", maximo="claude-opus-5-5"
        ),
        "gemini": ProviderModels(
            economico="gemini-flash-lite-latest",
            estandar="gemini-flash-latest",
            maximo="gemini-pro-latest",
        ),
        "ollama": ProviderModels(economico="qwen3:8b", estandar="qwen3:8b", maximo="qwen3:14b"),
    }


class AIConfig(BaseModel):
    provider: ProviderName = "none"
    fallback_provider: ProviderName = "none"
    models: dict[str, ProviderModels] = Field(default_factory=_default_models)
    extraction_tier: Tier = "economico"
    chat_tier: Tier = "estandar"
    live_suggestions: bool = True
    suggest_courses: bool = True  # proponer curso/profesor al terminar una clase
    live_interval_min: int = Field(10, ge=5, le=30)
    live_max_calls: int = Field(12, ge=1, le=100)
    monthly_token_limit: int | None = 2_000_000
    use_batch_for_pending: bool = True
    ollama_url: str = "http://localhost:11434"


class ObsidianConfig(BaseModel):
    link_course: bool = True
    link_prev_next: bool = True
    link_teacher: bool = True
    link_topics: bool = True
    link_related: bool = True
    link_user_notes: bool = True
    # None = umbral recomendado del modelo de embeddings en uso.
    related_threshold: float | None = Field(None, ge=0, le=1)
    related_max: int = Field(5, ge=0, le=20)
    excluded_folders: list[str] = Field(default_factory=list)


class GoogleConfig(BaseModel):
    connected: bool = False
    read_schedule: bool = True
    reminders: bool = True
    tasks: bool = True
    calendar_id: str | None = None
    tasklist_id: str | None = None
    reminder_advance_days: int = Field(1, ge=0, le=14)
    auto_add: bool = False
    notify_before_min: int = Field(5, ge=0, le=60)
    auto_record: bool = False
    canvas_card_dismissed: bool = False
    canvas_imported: bool = False


class UniversityConfig(BaseModel):
    platform: Literal["canvas", "moodle", "blackboard", "otra"] = "canvas"
    has_ics_url: bool = False
    compare_dates: bool = True
    create_tasks_from_ics: bool = False


class UIConfig(BaseModel):
    theme: Literal["system", "light", "dark"] = "system"
    onboarding_done: bool = False


class AppConfig(BaseModel):
    version: int = CONFIG_VERSION
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    speakers: SpeakersConfig = Field(default_factory=SpeakersConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    obsidian: ObsidianConfig = Field(default_factory=ObsidianConfig)
    google: GoogleConfig = Field(default_factory=GoogleConfig)
    university: UniversityConfig = Field(default_factory=UniversityConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    courses: list[Course] = Field(default_factory=list)

    def course(self, course_id: str) -> Course | None:
        return next((c for c in self.courses if c.id == course_id), None)

    def retention_for(self, course: Course | None) -> RetentionConfig:
        return (course.retention if course and course.retention else None) or self.audio.retention

    def format_for(self, course: Course | None) -> AudioFormat:
        return (
            course.audio_format if course and course.audio_format else None
        ) or self.audio.format
