"""Cierre de una clase (plan §6.4).

1. Pasada final de alta fidelidad sobre el audio completo (reemplaza al texto en vivo).
2. Identificación final de hablantes (profesor / yo / participantes).
3. transcripcion.txt + segmentos.json en la bóveda.
4. Resumen, tareas y temas: LLM (una llamada) o modo local.
5. Nota .md con relaciones automáticas (curso, anterior/siguiente, profesor, temas, relacionadas).
6. Índice de búsqueda y tareas.
7. Audio según la configuración (formato y retención).

Cada paso es tolerante a fallos: si algo falla, se registra y la nota se genera igual.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from transcriptor.asr.base import Segment, TranscribeOptions, Transcriber
from transcriptor.audio.decode import decode_file
from transcriptor.audio.retention import finalize_audio
from transcriptor.audio.types import SAMPLE_RATE
from transcriptor.calendar.ics import CalendarEntry
from transcriptor.config.models import AppConfig, Course
from transcriptor.events import EventBus
from transcriptor.links.builder import LinkBuilder
from transcriptor.links.vault import sync_vault
from transcriptor.llm.schemas import ChunkInsights, LLMClassSummary
from transcriptor.notes.markdown import replace_block
from transcriptor.notes.writer import NoteWriter, VaultLayout, transcript_markdown
from transcriptor.processing.postprocess import PostProcessor, from_llm
from transcriptor.search.index import SearchIndex
from transcriptor.session.models import ClassSummary, RecordingSession, TaskItem
from transcriptor.session.store import SessionStore
from transcriptor.session.suggest import build_suggestion, calendar_hint
from transcriptor.speakers.embedder import SpeakerEmbedder
from transcriptor.speakers.identifier import ME, PARTICIPANT, TEACHER, SpeakerIdentifier
from transcriptor.speakers.profiles import SpeakerProfileStore

log = logging.getLogger(__name__)


@dataclass
class FinalizeDeps:
    cfg: AppConfig
    store: SessionStore
    bus: EventBus
    index: SearchIndex
    layout: VaultLayout
    final_transcriber: Transcriber
    postprocessor: PostProcessor
    speaker_embedder: SpeakerEmbedder | None
    profiles: SpeakerProfileStore
    task_checker: object | None = None  # CalendarService.cross_check (Canvas)
    schedule: object | None = None  # CalendarService.event_during (Google Calendar)


class Finalizer:
    def __init__(self, deps: FinalizeDeps) -> None:
        self.d = deps
        self.writer = NoteWriter(deps.layout, deps.cfg)
        self.links = LinkBuilder(deps.layout, deps.cfg.obsidian, deps.index, self.writer)

    def _step(self, session: RecordingSession, name: str) -> None:
        self.d.bus.emit("finalize_step", session_id=session.id, step=name)

    # ------------------------------------------------------------------ principal
    def run(
        self,
        session: RecordingSession,
        course: Course,
        live_segments: list[Segment],
        audio_files: list[Path],
        partials: list[ChunkInsights] | None = None,
    ) -> RecordingSession:
        cfg = self.d.cfg
        session.status = "finalizing"
        session.ended_at = session.ended_at or datetime.now()
        self.d.store.save_segments(session.id, live_segments, "live")
        self.d.store.save(session)
        opts = TranscribeOptions(
            language=course.language or cfg.transcription.language,
            beam_size=cfg.transcription.beam_size,
            glossary=tuple(course.glossary),
        )

        self._step(session, "Pasada final de transcripción (la primera vez descarga el modelo)")
        segments = self.final_pass(session, audio_files, opts, live_segments)

        self._step(session, "Identificando al profesor")
        self.identify_speakers(session, course, segments, audio_files)
        self.d.store.save_segments(session.id, segments, "final")

        self._step(session, "Guardando transcripción")
        self.writer.write_transcript_files(session, segments)

        self._step(session, "Extrayendo resumen, tareas y temas")
        entry = self._calendar_event(session)
        summary, used_llm = self.d.postprocessor.summarize(
            segments, session.day, course.name, partials, calendar_hint(entry)
        )
        session.summary = summary
        session.llm_processed = used_llm
        session.llm_tokens += self.d.postprocessor.tokens_used
        self._cross_check(summary.tasks, course.name)
        self._suggest_course(session, course, entry)

        self._step(session, "Creando la nota y sus relaciones")
        self.write_note(session, segments)

        self._step(session, "Aplicando ajustes de audio")
        self.apply_retention(session, course, audio_files)

        session.status = "done"
        self.d.store.save(session)
        self.d.bus.emit(
            "session_done",
            session_id=session.id,
            tasks=[t for t in summary.tasks if t.due or t.confirmed],
            needs_teacher=session.teacher_needs_confirmation,
        )
        return session

    # ------------------------------------------------------------------ pasos
    def final_pass(
        self,
        session: RecordingSession,
        audio_files: list[Path],
        opts: TranscribeOptions,
        live_segments: list[Segment],
    ) -> list[Segment]:
        segments: list[Segment] = []
        try:
            for path in audio_files:
                if not path.exists():
                    continue
                channel = "mic" if path.stem == "mic" else "clase"
                for seg in self.d.final_transcriber.transcribe_file(path, opts):
                    seg.channel = channel  # type: ignore[assignment]
                    seg.final = True
                    segments.append(seg)
            segments.sort(key=lambda s: (s.start, 0 if s.channel == "clase" else 1))
            session.final_pass_ok = True
            session.asr_model_final = self.d.final_transcriber.name
            return segments
        except Exception as exc:
            log.exception("Falló la pasada final; se conserva la transcripción en vivo")
            session.errors.append(f"Pasada final: {exc}")
            session.final_pass_ok = False
            return list(live_segments)

    def identify_speakers(
        self,
        session: RecordingSession,
        course: Course,
        segments: list[Segment],
        audio_files: list[Path],
    ) -> None:
        for seg in segments:
            if seg.channel == "mic":
                seg.speaker = ME
        embedder = self.d.speaker_embedder
        clase = next((p for p in audio_files if p.stem == "clase" and p.exists()), None)
        if embedder is None or clase is None:
            for seg in segments:
                if seg.channel != "mic":
                    seg.speaker = seg.speaker or TEACHER
            return
        try:
            audio = decode_file(clase)
            ident = SpeakerIdentifier(
                embedder,
                self.d.profiles,
                course.id,
                threshold=self.d.cfg.speakers.teacher_threshold,
                distinguish_participants=self.d.cfg.speakers.distinguish_participants,
            )
            embs: list[np.ndarray | None] = []
            for seg in segments:
                if seg.channel == "mic":
                    embs.append(None)
                    continue
                a, b = int(seg.start * SAMPLE_RATE), int(seg.end * SAMPLE_RATE)
                embs.append(embedder.embed(audio[a:b]))
            result = ident.label_final(segments, embs)
            for seg, label in zip(segments, result.labels, strict=True):
                seg.speaker = label
            session.speaker_embedder = embedder.name
            session.teacher_needs_confirmation = result.needs_confirmation
            if result.teacher_cluster is not None and result.clusters:
                idx, _ = result.clusters[result.teacher_cluster]
                session.teacher_example_t = segments[idx].start
            self._save_clusters(session, segments, embs, result)
            ident.learn(result)
        except Exception as exc:
            log.exception("No se pudo identificar a los hablantes")
            session.errors.append(f"Hablantes: {exc}")

    def _save_clusters(
        self,
        session: RecordingSession,
        segments: list[Segment],
        embs: list[np.ndarray | None],
        result: object,
    ) -> None:
        """Guarda los grupos de voces para la confirmación «¿Este es el profesor?»."""
        work = self.d.store.dir(session.id)
        groups: dict[str, list[int]] = {}
        for i, seg in enumerate(segments):
            if seg.channel != "mic":
                groups.setdefault(seg.speaker, []).append(i)
        info: list[dict] = []
        for label, idxs in groups.items():
            vecs = [e for i in idxs if (e := embs[i]) is not None]
            if not vecs:
                continue
            centroid = np.mean(np.vstack(vecs), axis=0)
            np.save(work / f"voz_{len(info)}.npy", centroid.astype(np.float32))
            longest = max(idxs, key=lambda i: segments[i].end - segments[i].start)
            info.append(
                {
                    "label": label,
                    "file": f"voz_{len(info)}.npy",
                    "example_t": segments[longest].start,
                    "example_end": segments[longest].end,
                    "seconds": sum(segments[i].end - segments[i].start for i in idxs),
                }
            )
        (work / "voces.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")

    def _cross_check(self, tasks: list[TaskItem], course: str = "") -> None:
        checker = self.d.task_checker
        if checker is None or not tasks:
            return
        try:
            checker.cross_check(tasks, course)  # type: ignore[attr-defined]
        except Exception:
            log.exception("No se pudo comparar con el calendario de la universidad")

    def _calendar_event(self, session: RecordingSession) -> CalendarEntry | None:
        """Evento de Google Calendar que coincide con la hora de la clase (si está conectado)."""
        schedule = self.d.schedule
        if schedule is None:
            return None
        try:
            return schedule.event_during(session.started_at, session.ended_at)  # type: ignore[attr-defined,no-any-return]
        except Exception:
            log.exception("No se pudo leer el evento de la clase en Google Calendar")
            return None

    def _suggest_course(
        self, session: RecordingSession, course: Course, entry: CalendarEntry | None
    ) -> None:
        """Guarda la propuesta de curso/profesor; la interfaz pregunta, nunca se aplica sola."""
        if session.suggestion is not None:
            return  # ya propuesta (o resuelta): no se vuelve a preguntar
        use_ai = self.d.cfg.ai.suggest_courses and session.llm_processed
        try:
            session.suggestion = build_suggestion(
                course, self.d.cfg.courses, entry, session.summary, use_ai
            )
        except Exception:
            log.exception("No se pudo proponer el curso y el profesor")

    def write_note(self, session: RecordingSession, segments: list[Segment]) -> Path:
        root = self.d.layout.root
        try:
            sync_vault(self.d.index, root, self.d.cfg.obsidian.excluded_folders)
        except Exception:
            log.exception("No se pudo indexar la bóveda")
        path = self.writer.class_note_path(session)
        data = self.links.prepare(session, segments, path)
        if session.note_path and path.exists():
            self.writer.update_class(path, data)
        else:
            path = self.writer.write_class(data)
        try:
            self.links.after_write(data, path)
        except Exception as exc:
            log.exception("Error actualizando relaciones")
            session.errors.append(f"Relaciones: {exc}")
        rel = path.relative_to(root).as_posix()
        if session.summary:
            self.d.index.upsert_tasks(
                session.summary.tasks,
                session_id=session.id,
                course=session.course_name,
                note_path=rel,
            )
        return path

    def apply_retention(
        self, session: RecordingSession, course: Course, audio_files: list[Path]
    ) -> None:
        cfg = self.d.cfg
        dest = None
        if cfg.audio.store_in_vault and session.transcript_dir:
            dest = self.d.layout.root / session.transcript_dir
        try:
            res = finalize_audio(
                audio_files,
                cfg.format_for(course),
                cfg.retention_for(course),
                final_pass_ok=session.final_pass_ok,
                dest_dir=dest,
            )
            session.audio_files = [str(p) for p in res.kept]
            session.audio_deleted = not res.kept
            session.audio_expires_at = res.expires_at
        except Exception as exc:
            log.exception("Error aplicando la retención de audio")
            session.errors.append(f"Audio: {exc}")
            session.audio_files = [str(p) for p in audio_files if p.exists()]

    # ------------------------------------------------------------------ acciones posteriores
    def reprocess_with_llm(self, session: RecordingSession, course: Course) -> RecordingSession:
        """«Procesar clases pendientes»: agrega resumen y tareas con IA a una clase ya guardada."""
        segments = self.d.store.load_segments(session.id, "final") or self.d.store.load_segments(
            session.id, "live"
        )
        entry = self._calendar_event(session)
        summary, used = self.d.postprocessor.summarize(
            segments, session.day, course.name, calendar_hint=calendar_hint(entry)
        )
        if not used:
            return session
        session.summary = summary
        session.llm_processed = True
        session.llm_tokens += self.d.postprocessor.tokens_used
        self._cross_check(summary.tasks, course.name)
        self._suggest_course(session, course, entry)
        self.write_note(session, segments)
        self.d.store.save(session)
        self.d.bus.emit("session_updated", session_id=session.id)
        return session

    def apply_llm_result(self, session: RecordingSession, course: Course, result: LLMClassSummary,
                         tokens: int = 0) -> RecordingSession:
        """Aplica un resumen obtenido aparte (p. ej. del procesamiento por lotes)."""
        segments = self.d.store.load_segments(session.id, "final") or self.d.store.load_segments(
            session.id, "live"
        )
        session.summary = from_llm(result, session.day)
        session.llm_processed = True
        session.llm_tokens += tokens
        self._cross_check(session.summary.tasks, course.name)
        self._suggest_course(session, course, self._calendar_event(session))
        self.write_note(session, segments)
        self.d.store.save(session)
        self.d.bus.emit("session_updated", session_id=session.id)
        return session

    def retranscribe(self, session: RecordingSession, course: Course) -> RecordingSession:
        files = [Path(p) for p in session.audio_files if Path(p).exists()]
        if not files:
            raise FileNotFoundError("El audio de esta clase ya no está guardado.")
        opts = TranscribeOptions(
            language=course.language,
            beam_size=self.d.cfg.transcription.beam_size,
            glossary=tuple(course.glossary),
        )
        segments = self.final_pass(session, [f for f in files], opts, [])
        clase = [f for f in files if f.stem == "clase"]
        self.identify_speakers(session, course, segments, clase)
        self.d.store.save_segments(session.id, segments, "final")
        self.writer.write_transcript_files(session, segments)
        self._rewrite_transcript_block(session, segments)
        self.d.store.save(session)
        return session

    def relabel_teacher(
        self, session: RecordingSession, course: Course, teacher_label: str
    ) -> None:
        """Corrección del usuario: «esta voz es la del profesor»."""
        work = self.d.store.dir(session.id)
        voices = json.loads((work / "voces.json").read_text(encoding="utf-8"))
        chosen = next(v for v in voices if v["label"] == teacher_label)
        embedding = np.load(work / chosen["file"])
        if self.d.speaker_embedder is not None:
            self.d.profiles.replace(
                course.id, self.d.speaker_embedder.name, embedding, confirmed=True
            )
        segments = self.d.store.load_segments(session.id, "final")
        n = 0
        mapping: dict[str, str] = {}
        for v in voices:
            if v["label"] == teacher_label:
                mapping[v["label"]] = TEACHER
            elif v["label"] == TEACHER or v["label"].startswith(PARTICIPANT):
                n += 1
                mapping[v["label"]] = (
                    f"{PARTICIPANT} {n}"
                    if self.d.cfg.speakers.distinguish_participants
                    else PARTICIPANT
                )
        for seg in segments:
            if seg.channel != "mic":
                seg.speaker = mapping.get(seg.speaker, seg.speaker)
        for v in voices:
            v["label"] = mapping.get(v["label"], v["label"])
        (work / "voces.json").write_text(json.dumps(voices, ensure_ascii=False), encoding="utf-8")
        session.teacher_needs_confirmation = False
        self.d.store.save_segments(session.id, segments, "final")
        self.writer.write_transcript_files(session, segments)
        self._rewrite_transcript_block(session, segments)
        self.d.store.save(session)

    def confirm_teacher(self, session: RecordingSession, course: Course) -> None:
        self.relabel_teacher(session, course, TEACHER)

    def _rewrite_transcript_block(self, session: RecordingSession, segments: list[Segment]) -> None:
        from transcriptor.fsutil import atomic_write_text, read_text

        path = self.d.layout.root / session.note_path
        if not path.exists():
            return
        text, mtime = read_text(path)
        md, doubtful = transcript_markdown(segments, self.d.cfg.transcription.low_confidence)
        session.low_confidence_words = doubtful
        atomic_write_text(
            path, replace_block(text, "transcripcion", md, "## Transcripción"), expected_mtime=mtime
        )

    def set_task_done(self, session: RecordingSession, task_id: str, done: bool) -> None:
        if not session.summary:
            return
        for task in session.summary.tasks:
            if task.id == task_id:
                task.done = done
        segments = self.d.store.load_segments(session.id, "final")
        self.write_note(session, segments)
        self.d.store.save(session)


def empty_summary() -> ClassSummary:
    return ClassSummary(source="local")
