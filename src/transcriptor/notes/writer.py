"""Genera los archivos de una clase: transcripcion.txt, segmentos.json y la nota .md de Obsidian.

La nota se arma con plantillas (sin IA). Todo lo que la app escribe va en bloques gestionados,
así el usuario puede escribir sus propios apuntes en la misma nota sin perderlos.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import jinja2

from transcriptor.asr.base import Segment, format_ts
from transcriptor.config.models import AppConfig
from transcriptor.filenames import safe_filename, slug
from transcriptor.fsutil import atomic_write_text
from transcriptor.notes.markdown import render_frontmatter, replace_block, set_properties, wrap_block
from transcriptor.session.models import ClassSummary, RecordingSession, TaskItem

TRANSCRIPT_DIR = ".transcriptor"


@dataclass
class VaultLayout:
    root: Path
    classes: str = "Clases"
    topics: str = "Temas"
    teachers: str = "Profesores"

    @staticmethod
    def from_config(cfg: AppConfig, root: Path) -> VaultLayout:
        g = cfg.general
        return VaultLayout(root, g.classes_folder, g.topics_folder, g.teachers_folder)

    def course_dir(self, course: str) -> Path:
        return self.root / self.classes / safe_filename(course)

    def course_note(self, course: str) -> Path:
        return self.course_dir(course) / "_Curso.md"

    def index_note(self) -> Path:
        return self.root / self.classes / "_Índice.md"

    def topic_note(self, topic: str) -> Path:
        return self.root / self.topics / f"{safe_filename(topic)}.md"

    def teacher_note(self, teacher: str) -> Path:
        return self.root / self.teachers / f"{safe_filename(teacher)}.md"

    def transcript_dir(self, course: str, session: RecordingSession) -> Path:
        return self.course_dir(course) / TRANSCRIPT_DIR / session.started_at.strftime("%Y-%m-%d_%H%M")

    def rel(self, path: Path) -> str:
        """Ruta relativa a la bóveda, sin .md, con / (para enlaces de Obsidian)."""
        return path.relative_to(self.root).as_posix().removesuffix(".md")


def _env() -> jinja2.Environment:
    loader = jinja2.FunctionLoader(
        lambda name: resources.files("transcriptor.notes").joinpath("templates", name).read_text(encoding="utf-8")
    )
    env = jinja2.Environment(loader=loader, autoescape=False, keep_trailing_newline=True,
                             trim_blocks=True, lstrip_blocks=True)
    env.globals["block"] = wrap_block
    return env


# ------------------------------------------------------------------ fragmentos markdown
def ts_link(t: float | None) -> str:
    if t is None:
        return ""
    stamp = format_ts(t)
    return f" ([[#^t-{stamp.replace(':', '-')}|{stamp[3:] if stamp.startswith('00:') else stamp}]])"


def unique_block_ids(segments: list[Segment]) -> list[str]:
    ids: list[str] = []
    seen: dict[str, int] = {}
    for seg in segments:
        base = seg.block_id
        n = seen.get(base, 0)
        seen[base] = n + 1
        ids.append(base if n == 0 else f"{base}-{n + 1}")
    return ids


def nearest_block(segments: list[Segment], t: float | None) -> float | None:
    """Ajusta un minuto al inicio del segmento que lo contiene (para que el enlace funcione)."""
    if t is None or not segments:
        return t
    best = min(segments, key=lambda s: 0 if s.start <= t <= s.end else min(abs(s.start - t), abs(s.end - t)))
    return best.start


def transcript_markdown(segments: list[Segment], low_confidence: float) -> tuple[str, int]:
    lines = []
    doubtful = 0
    for seg, block_id in zip(segments, unique_block_ids(segments), strict=True):
        if seg.words:
            parts = []
            for w in seg.words:
                token = w.text
                stripped = token.strip()
                if stripped and w.probability < low_confidence and any(c.isalnum() for c in stripped):
                    lead = token[: len(token) - len(token.lstrip())]
                    token = f"{lead}=={stripped}=="
                    doubtful += 1
                parts.append(token)
            text = "".join(parts).strip()
        else:
            text = seg.text.strip()
        speaker = f" {seg.speaker}:" if seg.speaker else ""
        lines.append(f"**[{format_ts(seg.start)}]{speaker}** {text} ^{block_id}")
    return "\n".join(lines), doubtful


def transcript_text(segments: list[Segment]) -> str:
    out = []
    for seg in segments:
        speaker = f" {seg.speaker}:" if seg.speaker else ""
        out.append(f"[{format_ts(seg.start)}]{speaker} {seg.text}")
    return "\n".join(out) + ("\n" if out else "")


def task_line(task: TaskItem, tag: str = "#tarea") -> str:
    box = "x" if task.done else " "
    due = f" 📅 {task.due.isoformat()}" if task.due else ""
    pending = "" if task.confirmed else " #a-confirmar"
    where = ts_link(task.t)
    extra = ""
    if task.canvas_match and task.canvas_due and task.due and task.canvas_due != task.due:
        extra = f" ⚠️ Canvas dice {task.canvas_due.isoformat()}"
    elif task.canvas_match:
        extra = " ✅ Ya está en Canvas"
    return f"- [{box}] {task.description}{due} {tag}{pending}{where}{extra} <!-- id:{task.id} -->"


def summary_markdown(summary: ClassSummary | None, llm_processed: bool) -> str:
    if summary and summary.summary:
        body = "\n".join(f"> {line}" if line else ">" for line in summary.summary.splitlines())
        return f"> [!summary] Resumen\n{body}"
    if not llm_processed:
        return ("> [!note] Resumen pendiente\n> Esta clase se procesó en modo local (sin IA). "
                "Agrega una API key en Configuración → IA y usa «Procesar clases pendientes».")
    return ""


def key_points_markdown(summary: ClassSummary | None, segments: list[Segment]) -> str:
    if not summary or not summary.key_points:
        return ""
    return "\n".join(f"- {kp.text}{ts_link(nearest_block(segments, kp.t))}" for kp in summary.key_points)


def tasks_markdown(tasks: list[TaskItem], segments: list[Segment]) -> str:
    if not tasks:
        return "_No se detectaron tareas._"
    lines = []
    for task in tasks:
        copy = task.model_copy(update={"t": nearest_block(segments, task.t)})
        lines.append(task_line(copy))
    if any(not t.confirmed for t in tasks):
        lines.append("\n> [!info] Las tareas marcadas #a-confirmar se detectaron sin IA; revísalas.")
    return "\n".join(lines)


# ------------------------------------------------------------------ escritor
@dataclass
class ClassNoteData:
    session: RecordingSession
    segments: list[Segment]
    topics_links: list[str]
    related_md: str = ""
    navigation: str = ""
    previous: str | None = None
    next: str | None = None
    related_links: list[str] | None = None


class NoteWriter:
    def __init__(self, layout: VaultLayout, cfg: AppConfig) -> None:
        self.layout = layout
        self.cfg = cfg
        self.env = _env()

    def class_note_path(self, session: RecordingSession) -> Path:
        if session.note_path:
            return self.layout.root / session.note_path
        title = session.summary.title if session.summary and session.summary.title else session.course_name
        base = f"{session.day.isoformat()} {title}"
        folder = self.layout.course_dir(session.course_name)
        candidate = folder / f"{safe_filename(base)}.md"
        n = 2
        while candidate.exists():
            candidate = folder / f"{safe_filename(base)} ({n}).md"
            n += 1
        return candidate

    def write_transcript_files(self, session: RecordingSession, segments: list[Segment]) -> Path:
        folder = self.layout.transcript_dir(session.course_name, session)
        atomic_write_text(folder / "transcripcion.txt", transcript_text(segments))
        atomic_write_text(folder / "segmentos.json",
                          json.dumps([s.to_dict() for s in segments], ensure_ascii=False))
        session.transcript_dir = folder.relative_to(self.layout.root).as_posix()
        return folder

    def frontmatter(self, data: ClassNoteData) -> dict[str, Any]:
        s = data.session
        summary = s.summary
        tasks = summary.tasks if summary else []
        course_link = f"[[{self.layout.rel(self.layout.course_note(s.course_name))}|{s.course_name}]]"
        return {
            "tipo": "clase",
            "transcriptor_id": s.id,
            "curso": course_link,
            "fecha": s.day.isoformat(),
            "hora_inicio": s.started_at.strftime("%H:%M"),
            "duracion_min": round(s.duration_s / 60),
            "profesor": f"[[{s.teacher}]]" if s.teacher else "",
            "fuente_audio": s.source_label,
            "anterior": data.previous or "",
            "siguiente": data.next or "",
            "temas": data.topics_links,
            "relacionadas": data.related_links or [],
            "tareas_pendientes": sum(1 for t in tasks if not t.done),
            "modelo_asr": s.asr_model_final or s.asr_model_live,
            "llm_procesado": s.llm_processed,
            "revisado": False,
            "aliases": [f"{s.course_name} {s.day.isoformat()}"],
            "tags": ["clase", slug(s.course_name)],
        }

    def render_class(self, data: ClassNoteData) -> str:
        s = data.session
        transcript_md, doubtful = transcript_markdown(data.segments, self.cfg.transcription.low_confidence)
        s.low_confidence_words = doubtful
        summary = s.summary
        markers = "\n".join(f"- {m.label}{ts_link(nearest_block(data.segments, m.t))}" for m in s.markers)
        return self.env.get_template("clase.md.j2").render(
            frontmatter=render_frontmatter(self.frontmatter(data)).rstrip("\n"),
            title=s.title,
            day=s.day.isoformat(),
            navigation=data.navigation,
            summary_md=summary_markdown(summary, s.llm_processed),
            key_points_md=key_points_markdown(summary, data.segments),
            tasks_md=tasks_markdown(summary.tasks if summary else [], data.segments),
            topics_md="\n".join(f"- {t}" for t in data.topics_links),
            related_md=data.related_md,
            markers_md=markers,
            transcript_md=transcript_md,
        )

    def write_class(self, data: ClassNoteData) -> Path:
        path = self.class_note_path(data.session)
        atomic_write_text(path, self.render_class(data))
        data.session.note_path = path.relative_to(self.layout.root).as_posix()
        return path

    def update_class(self, path: Path, data: ClassNoteData) -> None:
        """Actualiza una nota existente solo en sus bloques y propiedades (respeta lo del usuario)."""
        from transcriptor.fsutil import read_text

        text, mtime = read_text(path)
        s = data.session
        summary = s.summary
        text = set_properties(text, {
            k: v for k, v in self.frontmatter(data).items()
            if k not in {"revisado", "aliases", "tags"}
        })
        text = replace_block(text, "navegacion", data.navigation)
        text = replace_block(text, "resumen", summary_markdown(summary, s.llm_processed), "## Resumen")
        text = replace_block(text, "puntos", key_points_markdown(summary, data.segments), "## Puntos importantes")
        text = replace_block(text, "tareas", tasks_markdown(summary.tasks if summary else [], data.segments), "## Tareas")
        text = replace_block(text, "temas", "\n".join(f"- {t}" for t in data.topics_links), "## Temas de interés")
        text = replace_block(text, "relacionado", data.related_md, "## Relacionado")
        atomic_write_text(path, text, expected_mtime=mtime)


def day_of(session: RecordingSession) -> date:
    return session.day
