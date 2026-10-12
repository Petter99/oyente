"""Servicios de la app: une configuración, grabación, cierre, chat, IA y calendario.

La interfaz solo habla con esta clase. Todo lo pesado corre en hilos; el estado se publica
por el EventBus y en atributos que la interfaz consulta periódicamente.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from transcriptor.asr.base import Segment, TranscribeOptions, Transcriber
from transcriptor.audio.decode import estimate_mb
from transcriptor.audio.retention import sweep_expired
from transcriptor.audio.sources.base import AudioSource, SourceUnavailableError
from transcriptor.audio.sources.browser_tab import BrowserTabSource
from transcriptor.audio.sources.system import RunningApp
from transcriptor.audio.vad import EnergyDetector, SileroDetector, SpeechDetector
from transcriptor.branding import branding
from transcriptor.calendar.google_auth import GoogleAuth, scopes_for
from transcriptor.calendar.ics import IcsFeed, export_tasks_ics
from transcriptor.calendar.service import CalendarService, UpcomingClass
from transcriptor.capture_web.hub import EXTENSION_VERSION, TabCaptureHub
from transcriptor.chat.service import Answer, ChatService
from transcriptor.config.models import AppConfig, Course, ProviderName, SourceKind
from transcriptor.config.secrets import Secret, SecretStore
from transcriptor.config.store import ConfigStore
from transcriptor.events import Event, EventBus
from transcriptor.licensing import AlwaysActiveLicense
from transcriptor.live.assistant import LiveAssistant
from transcriptor.llm.base import LLMError, LLMMessage, LLMRequest
from transcriptor.llm.composite import BudgetedProvider, TokenLedger
from transcriptor.llm.factory import build_provider, single_provider
from transcriptor.notes.writer import VaultLayout
from transcriptor.paths import data_dir, index_db_path, sessions_dir
from transcriptor.processing.postprocess import PostProcessor
from transcriptor.search.index import SearchIndex
from transcriptor.session.finalize import FinalizeDeps, Finalizer
from transcriptor.session.live import LiveRecorder
from transcriptor.session.models import RecordingSession, TaskItem
from transcriptor.session.store import SessionStore
from transcriptor.speakers.embedder import SpeakerEmbedder, SpectralEmbedder
from transcriptor.speakers.identifier import SpeakerIdentifier
from transcriptor.speakers.profiles import SpeakerProfileStore

log = logging.getLogger(__name__)


@dataclass
class LiveState:
    """Estado de la grabación en curso, consultado por la interfaz."""

    session: RecordingSession | None = None
    recorder: LiveRecorder | None = None
    assistant: LiveAssistant | None = None
    segments: deque[Segment] = field(default_factory=lambda: deque(maxlen=2000))
    insights: list[dict[str, Any]] = field(default_factory=list)
    related: list[dict[str, Any]] = field(default_factory=list)
    source_lost: str | None = None
    error: str | None = None
    queue: int = 0
    version: int = 0  # cambia con cada novedad (para refrescar la UI)

    def bump(self) -> None:
        self.version += 1


@dataclass
class FinalizeState:
    session_id: str | None = None
    step: str = ""
    done: bool = False
    error: str | None = None


class AppServices:
    def __init__(
        self, config_store: ConfigStore | None = None, secrets: SecretStore | None = None
    ) -> None:
        self.config_store = config_store or ConfigStore()
        self.cfg: AppConfig = self.config_store.load()
        self.secrets = secrets or SecretStore()
        self.bus = EventBus()
        self.store = SessionStore()
        self.profiles = SpeakerProfileStore()
        self.ledger = TokenLedger(data_dir() / "uso_tokens.json")
        self.license = AlwaysActiveLicense()
        self.hub = TabCaptureHub(expected_extension_version=EXTENSION_VERSION)
        self.google_auth = GoogleAuth(self.secrets)
        self.live = LiveState()
        self.finalizing: dict[str, FinalizeState] = {}
        self.notices: deque[tuple[str, str]] = deque(maxlen=20)  # (tipo, mensaje) para la UI
        self._index: SearchIndex | None = None
        self._provider: BudgetedProvider | None = None
        self._transcribers: Any = None
        self._speaker_embedder: SpeakerEmbedder | None = None
        self._models_lock = threading.Lock()
        self.models_status = "pendiente"  # pendiente | cargando | listo | error
        self.bus.subscribe(self._on_event)

    # ------------------------------------------------------------------ configuración
    def save_config(self) -> None:
        self.config_store.save(self.cfg)
        self._provider = None  # se reconstruye con la nueva configuración

    @property
    def vault_root(self) -> Path:
        if self.cfg.general.vault_path:
            return Path(self.cfg.general.vault_path)
        fallback = data_dir() / "Bóveda local"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    def layout(self) -> VaultLayout:
        return VaultLayout.from_config(self.cfg, self.vault_root)

    @property
    def index(self) -> SearchIndex:
        if self._index is None:
            from transcriptor.nlp.embed import default_text_embedder

            self._index = SearchIndex(index_db_path(), default_text_embedder())
        return self._index

    @property
    def provider(self) -> BudgetedProvider:
        if self._provider is None:
            self._provider = build_provider(self.cfg.ai, self.secrets, self.ledger)
        return self._provider

    @property
    def ai_available(self) -> bool:
        return self.provider.available and not self.provider.exhausted

    def tokens_this_month(self) -> int:
        return self.ledger.used()

    # ------------------------------------------------------------------ modelos (en segundo plano)
    def warm_up_models(self) -> None:
        """Carga/descarga los modelos de voz en segundo plano (barra de progreso en la UI)."""

        def run() -> None:
            with self._models_lock:
                if self._transcribers is not None:
                    return
                self.models_status = "cargando"
                try:
                    from transcriptor.asr.factory import create_transcribers

                    pair = create_transcribers(self.cfg.transcription)
                    # Cargar el modelo en vivo ahora (el final se carga al terminar la clase).
                    import numpy as np

                    pair.live.transcribe(
                        np.zeros(16000, dtype=np.float32), 0.0, TranscribeOptions()
                    )
                    self._transcribers = pair
                    self.models_status = "listo"
                except Exception as exc:
                    log.exception("No se pudieron preparar los modelos de voz")
                    self.models_status = f"error: {exc}"

        threading.Thread(target=run, name="modelos", daemon=True).start()

    def transcribers(self) -> Any:
        if self._transcribers is None:
            from transcriptor.asr.factory import create_transcribers

            self._transcribers = create_transcribers(self.cfg.transcription)
            self.models_status = "listo"
        return self._transcribers

    def speaker_embedder(self) -> SpeakerEmbedder:
        if self._speaker_embedder is None:
            from transcriptor.speakers.embedder import default_embedder

            try:
                self._speaker_embedder = default_embedder()
            except Exception:
                self._speaker_embedder = SpectralEmbedder()
        return self._speaker_embedder

    # ------------------------------------------------------------------ cursos
    def add_course(self, name: str, teacher: str = "", glossary: list[str] | None = None) -> Course:
        course = Course(name=name.strip(), teacher=teacher.strip(), glossary=glossary or [])
        self.cfg.courses.append(course)
        self.save_config()
        return course

    def course(self, course_id: str) -> Course:
        found = self.cfg.course(course_id)
        if found is None:
            raise KeyError(course_id)
        return found

    # ------------------------------------------------------------------ grabación
    @property
    def recording(self) -> bool:
        return self.live.recorder is not None

    def create_sources(
        self,
        kind: SourceKind,
        app: RunningApp | None,
        include_mic: bool,
        mic_device: str | int | None = None,
    ) -> list[AudioSource]:
        from transcriptor.audio.sources.mic import MicSource
        from transcriptor.audio.sources.system import create_app_source, create_system_source

        sources: list[AudioSource] = []
        if kind == "app" and app is not None:
            sources.append(create_app_source(app))
        elif kind == "system":
            sources.append(create_system_source())
        elif kind == "tab":
            tab = BrowserTabSource()
            self.hub.new_token()
            self.hub.attach(tab)
            sources.append(tab)
        if kind == "mic" or include_mic:
            mic = MicSource(mic_device)
            if kind == "mic":
                mic.info = type(mic.info)("mic", "Micrófono", "clase")  # clase presencial
            sources.append(mic)
        return sources

    def start_recording(
        self,
        course_id: str,
        kind: SourceKind,
        app: RunningApp | None = None,
        include_mic: bool = True,
        sources: list[AudioSource] | None = None,
        transcriber: Transcriber | None = None,
        detector: type[SpeechDetector] | None = None,
    ) -> RecordingSession:
        if self.recording:
            raise RuntimeError("Ya hay una grabación en curso")
        course = self.course(course_id)
        sources = (
            sources
            if sources is not None
            else self.create_sources(kind, app, include_mic, self.cfg.audio.mic_device)
        )
        label = next((s.info.label for s in sources if s.info.channel == "clase"), "Micrófono")
        session = RecordingSession(
            course_id=course.id,
            course_name=course.name,
            teacher=course.teacher,
            source_kind=kind,
            source_label=label,
            include_mic=include_mic,
        )
        work = self.store.dir(session.id)
        session.work_dir = str(work)
        live_txt = work / "transcripcion.live.txt"
        if transcriber is None:
            transcriber = self.transcribers().live
            session.asr_model_live = transcriber.name
        embedder = self.speaker_embedder()
        identifier = SpeakerIdentifier(
            embedder,
            self.profiles,
            course.id,
            threshold=self.cfg.speakers.teacher_threshold,
            distinguish_participants=self.cfg.speakers.distinguish_participants,
        )

        def make_detector() -> SpeechDetector:
            if detector is not None:
                return detector()
            try:
                return SileroDetector()
            except Exception:
                return EnergyDetector()

        recorder = LiveRecorder(
            session,
            sources,
            transcriber,
            identifier,
            make_detector,
            TranscribeOptions(language=course.language, glossary=tuple(course.glossary)),
            work,
            live_txt,
            self.bus,
        )
        self.live = LiveState(session=session, recorder=recorder)
        if self.cfg.ai.live_suggestions and self.ai_available:
            self.live.assistant = LiveAssistant(
                self.provider,
                self.bus,
                course.name,
                self.cfg.ai.live_interval_min,
                self.cfg.ai.live_max_calls,
            )
        course.last_source = kind
        course.last_app = app.name if app else None
        self.save_config()
        self.store.save(session)
        try:
            recorder.start()
        except SourceUnavailableError:
            self.live = LiveState()
            raise
        return session

    def pause(self) -> None:
        if self.live.recorder:
            self.live.recorder.pause()

    def resume(self) -> None:
        if self.live.recorder:
            self.live.recorder.resume()

    def mark(self, label: str = "Momento marcado") -> None:
        if self.live.recorder:
            self.live.recorder.mark(label)

    def switch_source(self, kind: SourceKind, app: RunningApp | None = None) -> None:
        if not self.live.recorder:
            return
        new = self.create_sources(kind, app, include_mic=False)[0]
        self.live.recorder.replace_source("clase", new)
        self.live.source_lost = None
        self.live.bump()

    def stop_recording(
        self, background: bool = True, final_transcriber: Transcriber | None = None
    ) -> str | None:
        live = self.live
        if live.recorder is None or live.session is None:
            return None
        segments = live.recorder.stop()
        audio = live.recorder.audio_files
        assistant = live.assistant
        session = live.session
        session.ended_at = datetime.now()
        self.hub.detach()
        self.live = LiveState()
        state = FinalizeState(session_id=session.id)
        self.finalizing[session.id] = state

        def run() -> None:
            partials = None
            if assistant is not None:
                assistant.flush()  # puede llamar al LLM: fuera del hilo de la interfaz
                partials = assistant.insights or None
                session.live_insights = [i.model_dump() for i in assistant.insights]
                session.llm_tokens += assistant.tokens
            try:
                fin = self.finalizer(final_transcriber)
                fin.run(session, self.course(session.course_id), segments, audio, partials)
                state.done = True
            except Exception as exc:
                log.exception("Error cerrando la clase")
                state.error = str(exc)
                session.status = "error"
                session.errors.append(str(exc))
                self.store.save(session)
            self.bus.emit("finalize_finished", session_id=session.id, error=state.error)

        if background:
            threading.Thread(target=run, name="cierre", daemon=True).start()
        else:
            run()
        return session.id

    def finalizer(self, final_transcriber: Transcriber | None = None) -> Finalizer:
        calendar = self.calendar_service()
        deps = FinalizeDeps(
            cfg=self.cfg,
            store=self.store,
            bus=self.bus,
            index=self.index,
            layout=self.layout(),
            final_transcriber=final_transcriber or self.transcribers().final,
            postprocessor=PostProcessor(self.provider, self.cfg.ai.extraction_tier),
            speaker_embedder=self.speaker_embedder(),
            profiles=self.profiles,
            task_checker=calendar if (calendar.connected or calendar.ics is not None) else None,
        )
        return Finalizer(deps)

    # ------------------------------------------------------------------ eventos
    def _on_event(self, event: Event) -> None:
        live = self.live
        t = event.type
        if t == "segment" and live.session is not None:
            seg: Segment = event.data["segment"]
            live.segments.append(seg)
            if live.assistant is not None:
                live.assistant.on_segment(seg)
            if len(live.segments) % 15 == 0:
                threading.Thread(target=self._related_now, daemon=True).start()
            live.bump()
        elif t == "insights":
            ins = event.data["insights"]
            live.insights.append({"t": event.data.get("start", 0.0), **ins.model_dump()})
            live.bump()
        elif t in ("live_unavailable", "live_paused"):
            self.notices.append(("info", f"Sugerencias: {event.data.get('reason')}"))
            live.bump()
        elif t == "source_lost":
            live.source_lost = f"«{event.data.get('label')}» dejó de sonar."
            live.bump()
        elif t == "source_changed":
            live.source_lost = None
            live.bump()
        elif t == "queue":
            live.queue = event.data.get("pending", 0)
        elif t == "error":
            live.error = event.data.get("message")
            live.bump()
        elif t == "finalize_step":
            st = self.finalizing.get(event.data["session_id"])
            if st:
                st.step = event.data["step"]

    def _related_now(self) -> None:
        """«Esto se vio también el 05/10»: búsqueda local sin tokens durante la clase."""
        live = self.live
        if live.session is None:
            return
        text = " ".join(s.text for s in list(live.segments)[-15:])
        try:
            hits = self.index.search(text, k=3, kinds=("resumen", "punto"))
        except Exception:
            return
        live.related = [
            {"title": h.note_title, "course": h.course, "day": h.day, "path": h.note_path}
            for h in hits
            if h.day != live.session.day.isoformat()
        ]
        live.bump()

    # ------------------------------------------------------------------ sesiones
    def sessions(self) -> list[RecordingSession]:
        return self.store.all()

    def pending_llm_sessions(self) -> list[RecordingSession]:
        return [s for s in self.sessions() if s.status == "done" and not s.llm_processed]

    def process_pending(self, progress: Any = None) -> int:
        """Agrega resumen y tareas con IA a las clases procesadas en modo local.

        Con Claude y varias clases pendientes se usa la Batch API (≈ 50 % más barata): el
        resultado llega en minutos u horas y se aplica solo (ver ``check_batches``).
        """
        if not self.ai_available:
            return 0
        pending = [s for s in self.pending_llm_sessions() if s.id not in self.batched_sessions()]
        anthropic_provider = self._anthropic()
        if anthropic_provider is not None and self.cfg.ai.use_batch_for_pending and len(pending) >= 2:
            return self.submit_pending_batch(anthropic_provider, pending)
        fin = self.finalizer(final_transcriber=_NoTranscriber())
        done = 0
        for s in pending:
            try:
                fin.reprocess_with_llm(s, self.course(s.course_id))
                done += 1
                if progress:
                    progress(done)
            except (LLMError, KeyError):
                log.exception("No se pudo procesar %s", s.id)
        return done

    # -- procesamiento por lotes (Claude Batch API) ------------------------------------------
    @property
    def _batches_file(self) -> Path:
        return data_dir() / "lotes.json"

    def _load_batches(self) -> list[dict[str, Any]]:
        import json

        try:
            return list(json.loads(self._batches_file.read_text(encoding="utf-8")))
        except (FileNotFoundError, ValueError):
            return []

    def _save_batches(self, batches: list[dict[str, Any]]) -> None:
        import json

        from transcriptor.fsutil import atomic_write_text

        atomic_write_text(self._batches_file, json.dumps(batches))

    def batched_sessions(self) -> set[str]:
        return {sid for b in self._load_batches() for sid in b["sessions"]}

    def _anthropic(self) -> Any:
        from transcriptor.llm.anthropic_provider import AnthropicProvider

        inner = getattr(self.provider, "inner", None)
        chain = getattr(inner, "providers", [inner])
        return next((p for p in chain if isinstance(p, AnthropicProvider) and p.available), None)

    def submit_pending_batch(self, provider: Any, sessions: list[RecordingSession]) -> int:
        from transcriptor.llm.schemas import LLMClassSummary

        post = PostProcessor(self.provider, self.cfg.ai.extraction_tier)
        items = []
        for s in sessions:
            segments = self.store.load_segments(s.id, "final") or self.store.load_segments(s.id, "live")
            if segments:
                items.append((s.id, post.summary_request(segments, s.course_name), LLMClassSummary))
        if not items:
            return 0
        batch_id = provider.submit_batch(items)
        batches = self._load_batches()
        batches.append({"id": batch_id, "sessions": [i[0] for i in items],
                        "created": datetime.now().isoformat()})
        self._save_batches(batches)
        self.notices.append(("info", f"{len(items)} clases enviadas a procesar por lotes."))
        return len(items)

    def check_batches(self) -> int:
        """Aplica los resultados de lotes terminados. Se llama periódicamente."""
        from transcriptor.llm.schemas import LLMClassSummary

        batches = self._load_batches()
        provider = self._anthropic()
        if not batches or provider is None:
            return 0
        applied = 0
        remaining = []
        for batch in batches:
            try:
                if provider.batch_status(batch["id"]) != "ended":
                    remaining.append(batch)
                    continue
                results = provider.batch_results(batch["id"], LLMClassSummary)
            except Exception:
                log.warning("No se pudo consultar el lote %s", batch["id"])
                remaining.append(batch)
                continue
            fin = self.finalizer(final_transcriber=_NoTranscriber())
            for sid, (result, resp) in results.items():
                session = self.store.get(sid)
                if session is None or result is None or resp is None:
                    continue
                self.ledger.record(resp)
                fin.apply_llm_result(session, self.course(session.course_id), result, resp.usage.total)
                applied += 1
        self._save_batches(remaining)
        return applied

    def recover_interrupted(self) -> list[str]:
        """Sesiones que quedaron abiertas por un cierre inesperado: se cierran con su audio."""
        recovered = []
        for s in self.store.interrupted():
            work = Path(s.work_dir) if s.work_dir else self.store.dir(s.id)
            audio = [p for p in (work / "clase.flac", work / "mic.flac") if p.exists()]
            try:
                segments = self.store.load_segments(s.id, "live")
                self.finalizer().run(s, self.course(s.course_id), segments, audio)
                recovered.append(s.id)
            except Exception:
                log.exception("No se pudo recuperar la sesión %s", s.id)
        return recovered

    def startup_maintenance(self) -> None:
        def run() -> None:
            try:
                removed = sweep_expired(sessions_dir())
                if removed:
                    log.info("Audios vencidos borrados: %d", len(removed))
                if self.cfg.general.vault_path:
                    from transcriptor.links.vault import sync_vault

                    sync_vault(self.index, self.vault_root, self.cfg.obsidian.excluded_folders)
                self.sync_google_tasks()
            except Exception:
                log.exception("Error en el mantenimiento inicial")

        threading.Thread(target=run, name="mantenimiento", daemon=True).start()

    # ------------------------------------------------------------------ chat
    _chat: ChatService | None = None

    def chat(self) -> ChatService:
        courses = [c.name for c in self.cfg.courses]
        if self._chat is None or self._chat.provider is not self.provider:
            self._chat = ChatService(self.index, self.provider, courses, self.cfg.ai.chat_tier)
        self._chat.courses = courses
        return self._chat

    def ask(self, question: str, course: str | None = None) -> Answer:
        return self.chat().ask(question, course)

    # ------------------------------------------------------------------ IA
    def test_provider(self, name: ProviderName, key: str | None) -> tuple[bool, str]:
        """«Probar conexión» del asistente y de Configuración → IA."""
        if name == "none":
            return True, "Modo local: la app transcribe sin IA."
        secret = {"anthropic": Secret.ANTHROPIC_API_KEY, "gemini": Secret.GEMINI_API_KEY}.get(name)
        previous = self.secrets.get(secret) if secret else None
        try:
            if secret and key:
                self.secrets.set(secret, key.strip())
            provider = single_provider(name, self.cfg.ai, self.secrets)
            if not provider.available:
                return (
                    False,
                    "No se encontró el servicio. Revisa la clave o que Ollama esté abierto.",
                )
            resp = provider.generate(
                LLMRequest(
                    system="Responde solo: ok",
                    messages=[LLMMessage("user", "ping")],
                    max_tokens=20,
                    cache_system=False,
                )
            )
            self.ledger.record(resp)
            return True, f"Conexión correcta ({resp.model})."
        except LLMError as exc:
            if secret:
                if previous:
                    self.secrets.set(secret, previous)
                else:
                    self.secrets.delete(secret)
            return False, str(exc)

    # ------------------------------------------------------------------ calendario
    _calendar: CalendarService | None = None

    def calendar_service(self) -> CalendarService:
        ics = None
        url = self.secrets.get(Secret.ICS_URL)
        if url:
            ics = IcsFeed(url, data_dir() / "universidad.ics")
        if self._calendar is None:
            self._calendar = CalendarService(
                self.cfg.google, self.google_auth, ics, self.cfg.courses
            )
        self._calendar.ics = ics
        self._calendar.courses = self.cfg.courses
        self._calendar.cfg = self.cfg.google
        return self._calendar

    def connect_google(self) -> None:
        self.google_auth.connect(scopes_for(self.cfg.google))
        self.cfg.google.connected = True
        self._calendar = None
        cal = self.calendar_service()
        try:
            self.cfg.google.canvas_imported = cal.university_imported()
        except Exception:
            pass
        self.save_config()

    def disconnect_google(self) -> None:
        self.google_auth.disconnect()
        self.cfg.google.connected = False
        self._calendar = None
        self.save_config()

    def set_ics_url(self, url: str | None) -> int:
        if not url:
            self.secrets.delete(Secret.ICS_URL)
            self.cfg.university.has_ics_url = False
            self.save_config()
            return 0
        self.secrets.set(Secret.ICS_URL, url.strip())
        self.cfg.university.has_ics_url = True
        self.save_config()
        feed = IcsFeed(url.strip(), data_dir() / "universidad.ics")
        return len(feed.refresh())

    def upcoming(self) -> list[UpcomingClass]:
        if not (self.cfg.google.connected and self.cfg.google.read_schedule):
            return []
        try:
            return self.calendar_service().upcoming_classes(within_min=12 * 60)
        except Exception:
            log.warning("No se pudo leer el horario de Google")
            return []

    def add_task_to_google(
        self, session_id: str, task_id: str, as_task: bool, as_reminder: bool
    ) -> list[str]:
        session = self.store.get(session_id)
        if session is None or session.summary is None:
            return []
        task = next(t for t in session.summary.tasks if t.id == task_id)
        cal = self.calendar_service()
        done = []
        note_uri = f"obsidian://open?path={(self.vault_root / session.note_path).as_posix()}"
        if as_task and cal.create_task(task, session.course_name, note_uri):
            done.append("Google Tasks")
        if as_reminder and task.due and cal.create_reminder(task, session.course_name):
            done.append("Google Calendar")
        task.confirmed = True
        self.store.save(session)
        self.index.upsert_tasks(
            session.summary.tasks,
            session_id=session.id,
            course=session.course_name,
            note_path=session.note_path,
        )
        self.save_config()  # ids de calendario/lista creados
        return done

    def export_ics(self, path: Path) -> Path:
        tasks: list[tuple[TaskItem, str]] = []
        for s in self.sessions():
            if s.summary:
                tasks += [(t, s.course_name) for t in s.summary.tasks if not t.done]
        return export_tasks_ics(tasks, path, self.cfg.google.reminder_advance_days)

    def sync_google_tasks(self) -> int:
        """Tareas completadas en Google Tasks → casilla marcada en Obsidian."""
        if not (self.cfg.google.connected and self.cfg.google.tasks):
            return 0
        try:
            completed = self.calendar_service().completed_task_ids()
        except Exception:
            return 0
        changed = 0
        for s in self.sessions():
            if not s.summary:
                continue
            for task in s.summary.tasks:
                if task.google_task_id in completed and not task.done:
                    self.set_task_done(s.id, task.id, True)
                    changed += 1
        return changed

    def set_task_done(self, session_id: str, task_id: str, done: bool) -> None:
        session = self.store.get(session_id)
        if session is None:
            return
        self.finalizer(final_transcriber=_NoTranscriber()).set_task_done(session, task_id, done)
        self.index.set_task_done(task_id, done)

    # ------------------------------------------------------------------ utilidades para la UI
    def storage_estimate(self) -> dict[str, float]:
        """Espacio estimado del audio según formato, retención y horario aproximado."""
        hours_per_week = 3.0 * max(1, len(self.cfg.courses))
        channels = 2 if self.cfg.audio.include_mic else 1
        per_hour = estimate_mb(self.cfg.audio.format, 1.0, channels)
        mode = self.cfg.audio.retention.mode
        if mode == "never":
            total = 0.0
        elif mode == "days":
            total = per_hour * hours_per_week * self.cfg.audio.retention.days / 7
        else:
            total = per_hour * hours_per_week * 16  # un semestre
        return {"per_hour_mb": per_hour, "total_mb": total, "hours_per_week": hours_per_week}

    def onboarding_needed(self) -> bool:
        return not self.cfg.ui.onboarding_done

    def brand(self) -> str:
        return branding().nombre

    def brand_calendar(self) -> str:
        return branding().calendario_google

    def tasks_pending(self, limit: int = 20) -> list[dict]:
        try:
            return self.index.tasks(pending_only=True)[:limit]
        except Exception:
            return []

    def due_soon(self, days: int = 7) -> list[dict]:
        until = date.today() + timedelta(days=days)
        return [t for t in self.tasks_pending(100) if t["due"] and t["due"] <= until.isoformat()]


class _NoTranscriber:
    """Para acciones que no necesitan cargar el modelo de voz (reprocesar, marcar tareas)."""

    name = "ninguno"

    def transcribe(self, *a: Any, **k: Any) -> list[Segment]:
        return []

    def transcribe_file(self, *a: Any, **k: Any) -> list[Segment]:
        raise RuntimeError("sin transcriptor")
