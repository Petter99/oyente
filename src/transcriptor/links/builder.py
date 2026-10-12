"""Relaciones automáticas de la nota nueva con el resto de la bóveda (sin LLM).

Curso, clase anterior/siguiente, profesor, temas (normalizados contra notas existentes),
clases relacionadas por similitud y notas propias del usuario mencionadas en clase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz

from transcriptor.asr.base import Segment
from transcriptor.branding import branding
from transcriptor.config.models import ObsidianConfig
from transcriptor.filenames import slug
from transcriptor.fsutil import ConcurrentModificationError, atomic_write_text, read_text
from transcriptor.links.vault import index_note
from transcriptor.nlp.text import STOPWORDS, contains_phrase, fold
from transcriptor.notes.markdown import (
    parse_frontmatter,
    render_frontmatter,
    replace_block,
    set_properties,
    wrap_block,
)
from transcriptor.notes.writer import ClassNoteData, NoteWriter, VaultLayout
from transcriptor.search.index import NoteRow, SearchIndex
from transcriptor.session.models import RecordingSession

log = logging.getLogger(__name__)


@dataclass
class TopicRef:
    name: str
    target: str  # ruta relativa sin .md
    create: bool  # crear nota de tema nueva

    @property
    def link(self) -> str:
        return (
            f"[[{self.target}|{self.name}]]"
            if self.target.split("/")[-1] != self.name
            else f"[[{self.target}]]"
        )


class LinkBuilder:
    def __init__(
        self, layout: VaultLayout, cfg: ObsidianConfig, index: SearchIndex, writer: NoteWriter
    ) -> None:
        self.layout = layout
        self.cfg = cfg
        self.index = index
        self.writer = writer

    # ------------------------------------------------------------------ temas
    def _topic_candidates(self) -> list[NoteRow]:
        classes_prefix = self.layout.classes + "/"
        return [
            n
            for n in self.index.notes()
            if n.kind in ("tema", "usuario") and not n.path.startswith(classes_prefix)
        ]

    def resolve_topics(self, topics: list[str]) -> list[TopicRef]:
        if not self.cfg.link_topics:
            return []
        notes = self._topic_candidates()
        names: list[tuple[str, NoteRow]] = []
        for n in notes:
            names.append((n.title, n))
            names.extend((a, n) for a in n.aliases if a)
        refs: list[TopicRef] = []
        title_vecs = (
            self.index.embedder.embed([nm for nm, _ in names]) if names else np.zeros((0, 1))
        )
        for raw in topics:
            topic = raw.strip().strip(".#[]")
            if len(topic) < 3 or fold(topic) in STOPWORDS:
                continue
            match = self._match_topic(topic, names, title_vecs)
            if match is not None:
                ref = TopicRef(match.title, match.path.removesuffix(".md"), False)
            else:
                ref = TopicRef(topic, self.layout.rel(self.layout.topic_note(topic)), True)
            if all(r.target != ref.target for r in refs):
                refs.append(ref)
        return refs

    def _match_topic(
        self, topic: str, names: list[tuple[str, NoteRow]], vecs: np.ndarray
    ) -> NoteRow | None:
        f = fold(topic)
        for name, note in names:
            if fold(name) == f:
                return note
        best: tuple[float, NoteRow | None] = (0.0, None)
        for name, note in names:
            score = fuzz.ratio(f, fold(name))
            if score > best[0]:
                best = (score, note)
        if best[0] >= 88:
            return best[1]
        if len(names):
            qv = self.index.embedder.embed([topic])[0]
            sims = vecs @ qv
            i = int(np.argmax(sims))
            if sims[i] >= self.index.embedder.same_topic_threshold:
                return names[i][1]
        return None

    # ------------------------------------------------------------------ clases
    def class_notes(self, course: str) -> list[NoteRow]:
        return [n for n in self.index.notes("clase", course)]

    def previous_class(self, session: RecordingSession, own_path: str) -> NoteRow | None:
        if not self.cfg.link_prev_next:
            return None
        before = [
            n
            for n in self.class_notes(session.course_name)
            if n.path != own_path
            and n.day
            and n.day <= session.day.isoformat()
            and n.session_id != session.id
        ]
        return before[-1] if before else None

    def next_class(self, session: RecordingSession, own_path: str) -> NoteRow | None:
        if not self.cfg.link_prev_next:
            return None
        after = [
            n
            for n in self.class_notes(session.course_name)
            if n.path != own_path and n.day and n.day > session.day.isoformat()
        ]
        return after[0] if after else None

    def related(self, text: str, exclude: set[str]) -> list[tuple[NoteRow, float]]:
        if not self.cfg.link_related or self.cfg.related_max == 0 or not text.strip():
            return []
        threshold = self.cfg.related_threshold
        if threshold is None:
            threshold = self.index.embedder.related_threshold
        vec = self.index.embedder.embed([text])[0]
        return self.index.similar_notes(
            vec, kind="clase", exclude=exclude, k=self.cfg.related_max, min_score=threshold
        )

    def mentioned_user_notes(
        self, transcript: str, exclude: set[str], limit: int = 5
    ) -> list[NoteRow]:
        if not self.cfg.link_user_notes:
            return []
        folded = fold(transcript)
        found: list[tuple[int, NoteRow]] = []
        for n in self.index.notes("usuario"):
            if n.path in exclude:
                continue
            for name in [n.title, *n.aliases]:
                if len(name) < 4 or fold(name) in STOPWORDS or name.isdigit():
                    continue
                if contains_phrase(folded, name):
                    found.append((len(name), n))
                    break
        found.sort(key=lambda x: -x[0])
        return [n for _, n in found[:limit]]

    # ------------------------------------------------------------------ armado
    def prepare(
        self, session: RecordingSession, segments: list[Segment], note_path: Path
    ) -> ClassNoteData:
        own = self.layout.rel(note_path) + ".md"
        topics = session.summary.topics if session.summary else []
        refs = self.resolve_topics(topics)
        prev = self.previous_class(session, own)
        nxt = self.next_class(session, own)
        summary_text = " ".join(
            filter(
                None,
                [
                    session.summary.summary if session.summary else "",
                    " ".join(topics),
                    " ".join(kp.text for kp in session.summary.key_points)
                    if session.summary
                    else "",
                ],
            )
        )
        if len(summary_text) < 200:
            summary_text += " " + " ".join(s.text for s in segments)[:3000]
        exclude = {own} | ({prev.path} if prev else set())
        related = self.related(summary_text, exclude)
        transcript = " ".join(s.text for s in segments)
        mentioned = self.mentioned_user_notes(transcript, exclude={r[0].path for r in related})

        lines = []
        if prev:
            lines.append(f"- {self._link(prev)} — clase anterior del curso")
        for note, score in related:
            same = "misma materia" if note.course == session.course_name else f"de {note.course}"
            lines.append(f"- {self._link(note)} — {same} (similitud {score:.2f})")
        for note in mentioned:
            lines.append(f"- {self._link(note)} — nota tuya mencionada en clase")

        course_rel = self.layout.rel(self.layout.course_note(session.course_name))
        prev_link = self._link(prev, "Clase anterior") if prev else None
        next_link = self._link(nxt, "Clase siguiente") if nxt else None
        return ClassNoteData(
            session=session,
            segments=segments,
            topics_links=[r.link for r in refs],
            related_md="\n".join(lines) if lines else "_Sin relaciones todavía._",
            navigation=self.navigation(prev_link, course_rel, session.course_name, next_link),
            previous=self._link(prev) if prev else None,
            next=self._link(nxt) if nxt else None,
            related_links=[self._link(n) for n, _ in related] + [self._link(n) for n in mentioned],
        )

    @staticmethod
    def _link(note: NoteRow, alias: str | None = None) -> str:
        target = note.path.removesuffix(".md")
        label = alias or target.split("/")[-1]
        return f"[[{target}|{label}]]"

    @staticmethod
    def navigation(
        prev_link: str | None, course_rel: str, course: str, next_link: str | None
    ) -> str:
        left = f"← {prev_link}" if prev_link else "← (primera clase)"
        right = f"{next_link} →" if next_link else "Clase siguiente →"
        return f"{left} · [[{course_rel}|{course}]] · {right}"

    # ------------------------------------------------------------------ después de escribir
    def after_write(
        self, data: ClassNoteData, note_path: Path, topics: list[TopicRef] | None = None
    ) -> None:
        session = data.session
        rel_md = note_path.relative_to(self.layout.root).as_posix()
        index_note(self.index, self.layout.root, note_path, force=True)
        if topics is None:
            topics = self.resolve_topics(session.summary.topics if session.summary else [])
        if self.cfg.link_prev_next:
            self._update_neighbors(session, rel_md)
        if self.cfg.link_course:
            self.update_course_note(session.course_name, session.teacher)
        if self.cfg.link_teacher and session.teacher:
            self.update_teacher_note(session.teacher)
        for ref in topics:
            self.update_topic_note(ref)
        self.update_index_note()

    def _update_neighbors(self, session: RecordingSession, own: str) -> None:
        classes = [n for n in self.class_notes(session.course_name)]
        classes.sort(key=lambda n: (n.day or "", n.path))
        course_rel = self.layout.rel(self.layout.course_note(session.course_name))
        for i, note in enumerate(classes):
            prev = classes[i - 1] if i > 0 else None
            nxt = classes[i + 1] if i + 1 < len(classes) else None
            path = self.layout.root / note.path
            if not path.exists():
                continue
            props = {
                "anterior": self._link(prev) if prev else "",
                "siguiente": self._link(nxt) if nxt else "",
            }
            nav = self.navigation(
                self._link(prev, "Clase anterior") if prev else None,
                course_rel,
                session.course_name,
                self._link(nxt, "Clase siguiente") if nxt else None,
            )
            self._edit_owned(path, props, {"navegacion": nav})

    def _edit_owned(
        self, path: Path, props: dict, blocks: dict[str, str], attempts: int = 3
    ) -> bool:
        """Edita una nota propia solo en propiedades y bloques. Nunca toca notas del usuario."""
        for _ in range(attempts):
            try:
                text, mtime = read_text(path)
            except FileNotFoundError:
                return False
            if not parse_frontmatter(text).get("transcriptor_id"):
                return False
            new = set_properties(text, props) if props else text
            for name, content in blocks.items():
                new = replace_block(new, name, content)
            if new == text:
                return True
            try:
                atomic_write_text(path, new, expected_mtime=mtime)
                index_note(self.index, self.layout.root, path, force=True)
                return True
            except ConcurrentModificationError:
                log.info("La nota %s cambió mientras se editaba; se reintenta", path.name)
        return False

    def _create_or_update(
        self, path: Path, frontmatter: dict, heading: str, blocks: list[tuple[str, str, str]]
    ) -> None:
        """Crea una nota propia o actualiza sus bloques. blocks = (nombre, encabezado, contenido)."""
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if not parse_frontmatter(text).get("transcriptor_id"):
                return  # nota del usuario con el mismo nombre: no se toca
            self._edit_owned(path, {}, {name: content for name, _, content in blocks})
            return
        parts = [render_frontmatter(frontmatter), f"# {heading}\n"]
        for name, head, content in blocks:
            block = wrap_block(name, content)
            parts.append(f"\n{head}\n{block}\n" if head else f"\n{block}\n")
        atomic_write_text(path, "".join(parts))
        index_note(self.index, self.layout.root, path, force=True)

    def _class_list(self, notes: list[NoteRow]) -> str:
        if not notes:
            return "_Todavía no hay clases._"
        return "\n".join(
            f"- {self._link(n, f'{n.day} · {n.title.split(" ", 1)[-1]}')}"
            for n in sorted(notes, key=lambda n: n.day or "", reverse=True)
        )

    def update_course_note(
        self, course: str, teacher: str = "", glossary: list[str] | None = None
    ) -> None:
        path = self.layout.course_note(course)
        folder = self.layout.rel(self.layout.course_dir(course))
        info = [f"**Profesor:** [[{teacher}]]" if teacher else "**Profesor:** —"]
        if glossary:
            info.append("**Glosario:** " + ", ".join(glossary))
        tasks_query = f"```tasks\nnot done\npath includes {folder}\nsort by due\n```"
        self._create_or_update(
            path,
            {
                "tipo": "curso",
                "transcriptor_id": f"curso-{slug(course)}",
                "tags": ["curso", slug(course)],
            },
            course,
            [
                ("info", "", "\n".join(info)),
                ("clases", "## Clases", self._class_list(self.class_notes(course))),
                ("tareas", "## Tareas pendientes", tasks_query),
            ],
        )

    def update_teacher_note(self, teacher: str) -> None:
        path = self.layout.teacher_note(teacher)
        classes = [n for n in self.index.notes("clase") if self._teacher_of(n) == fold(teacher)]
        courses = sorted({n.course for n in classes if n.course})
        course_links = (
            "\n".join(f"- [[{self.layout.rel(self.layout.course_note(c))}|{c}]]" for c in courses)
            or "—"
        )
        self._create_or_update(
            path,
            {
                "tipo": "profesor",
                "transcriptor_id": f"profesor-{slug(teacher)}",
                "tags": ["profesor"],
            },
            teacher,
            [
                ("cursos", "## Cursos", course_links),
                ("clases", "## Clases", self._class_list(classes)),
            ],
        )

    def _teacher_of(self, note: NoteRow) -> str:
        path = self.layout.root / note.path
        try:
            fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            return ""
        from transcriptor.links.vault import link_text

        return fold(link_text(fm.get("profesor")) or "")

    def update_topic_note(self, ref: TopicRef) -> None:
        path = self.layout.root / f"{ref.target}.md"
        target_link = ref.target
        classes = []
        for n in self.index.notes("clase"):
            note_path = self.layout.root / n.path
            try:
                text = note_path.read_text(encoding="utf-8")
            except OSError:
                continue
            if f"[[{target_link}|" in text or f"[[{target_link}]]" in text:
                classes.append(n)
        self._create_or_update(
            path,
            {
                "tipo": "tema",
                "transcriptor_id": f"tema-{slug(ref.name)}",
                "aliases": [],
                "tags": ["tema"],
            },
            ref.name,
            [("clases", "## Clases donde aparece", self._class_list(classes))],
        )

    def update_index_note(self) -> None:
        path = self.layout.index_note()
        courses = self.index.courses()
        course_links = (
            "\n".join(f"- [[{self.layout.rel(self.layout.course_note(c))}|{c}]]" for c in courses)
            or "—"
        )
        recent = sorted(self.index.notes("clase"), key=lambda n: n.day or "", reverse=True)[:10]
        tasks_query = f"```tasks\nnot done\npath includes {self.layout.classes}\nsort by due\ngroup by filename\n```"
        self._create_or_update(
            path,
            {"tipo": "indice", "transcriptor_id": "indice", "tags": ["indice"]},
            f"Clases ({branding().nombre})",
            [
                ("cursos", "## Cursos", course_links),
                ("recientes", "## Últimas clases", self._class_list(recent)),
                ("tareas", "## Tareas pendientes", tasks_query),
                ("actualizado", "", f"_Actualizado: {datetime.now():%Y-%m-%d %H:%M}_"),
            ],
        )
