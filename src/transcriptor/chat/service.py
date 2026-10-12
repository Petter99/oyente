"""Chat sobre las clases con el mínimo de tokens.

1. Enrutador por reglas (sin LLM): tareas → SQL; recordatorios → calendario.
2. Preguntas abiertas → búsqueda híbrida local (resúmenes primero) → pocos fragmentos al LLM.
3. Sin API key → «modo búsqueda»: se muestran los fragmentos con enlace al minuto.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from transcriptor.asr.base import format_ts
from transcriptor.llm.base import LLMError, LLMMessage, LLMProvider, LLMRequest, Tier
from transcriptor.llm.prompts import CHAT_SYSTEM
from transcriptor.nlp.text import fold
from transcriptor.search.index import Hit, SearchIndex

log = logging.getLogger(__name__)

Intent = Literal["tareas", "recordatorio", "pregunta"]

TASK_Q = re.compile(r"\b(tareas?|pendientes?|deberes|que (?:hay|tengo) que (?:hacer|entregar)|"
                    r"para cuando|entregas?|examen(?:es)?|parcial(?:es)?)\b")
REMINDER_Q = re.compile(r"\b(recuerdame|recordatorio|agenda(?:r|lo)?|ponlo en (?:el|mi) calendario|"
                        r"avisame|anota(?:lo)?)\b")
OPEN_Q = re.compile(r"\b(como|por que|explica|que es|que dijo|cual es|cuales son|resume|resumen)\b")


@dataclass
class Citation:
    note_path: str
    title: str
    course: str | None
    day: str | None
    t: float | None

    @property
    def label(self) -> str:
        stamp = format_ts(self.t)[3:] if self.t is not None else ""
        return " · ".join(x for x in [self.course or "", self.day or "", stamp] if x)

    @property
    def obsidian_link(self) -> str:
        anchor = f"#^t-{format_ts(self.t).replace(':', '-')}" if self.t is not None else ""
        return f"[[{self.note_path.removesuffix('.md')}{anchor}|{self.label}]]"


@dataclass
class Answer:
    text: str
    intent: Intent
    used_llm: bool
    tokens: int = 0
    citations: list[Citation] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    reminder_request: str | None = None


@dataclass
class Turn:
    question: str
    answer: str


class ChatService:
    def __init__(self, index: SearchIndex, provider: LLMProvider, courses: list[str],
                 tier: Tier = "estandar", max_history: int = 4, top_k: int = 5) -> None:
        self.index = index
        self.provider = provider
        self.courses = courses
        self.tier = tier
        self.max_history = max_history
        self.top_k = top_k
        self.history: list[Turn] = []

    # ------------------------------------------------------------------ enrutador
    def classify(self, question: str) -> Intent:
        q = fold(question)
        if REMINDER_Q.search(q):
            return "recordatorio"
        if TASK_Q.search(q) and not OPEN_Q.search(q):
            return "tareas"
        return "pregunta"

    def detect_course(self, question: str) -> str | None:
        q = fold(question)
        for course in sorted(self.courses, key=len, reverse=True):
            name = fold(course)
            if name in q:
                return course
            first = name.split()[0]
            if len(first) >= 5 and re.search(rf"\b{re.escape(first)}\b", q):
                return course
        return None

    @staticmethod
    def detect_range(question: str, today: date) -> tuple[date | None, date | None]:
        q = fold(question)
        if "esta semana" in q:
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=6)
        if "proxima semana" in q or "siguiente semana" in q:
            start = today - timedelta(days=today.weekday()) + timedelta(days=7)
            return start, start + timedelta(days=6)
        if "hoy" in q:
            return today, today
        if "manana" in q:
            return today + timedelta(days=1), today + timedelta(days=1)
        if "este mes" in q:
            return today.replace(day=1), None
        return None, None

    # ------------------------------------------------------------------ respuesta
    def ask(self, question: str, course: str | None = None, today: date | None = None) -> Answer:
        today = today or date.today()
        intent = self.classify(question)
        course = course or self.detect_course(question)
        if intent == "tareas":
            answer = self._tasks(question, course, today)
        elif intent == "recordatorio":
            answer = Answer("Te propongo este recordatorio. Revísalo y confírmalo.", "recordatorio",
                            False, reminder_request=question)
        else:
            answer = self._open(question, course, today)
        self.history.append(Turn(question, answer.text))
        self.history = self.history[-self.max_history:]
        return answer

    def _tasks(self, question: str, course: str | None, today: date) -> Answer:
        since, until = self.detect_range(question, today)
        rows = self.index.tasks(pending_only=True, course=course, until=until, since=since)
        if not rows:
            scope = f" de {course}" if course else ""
            return Answer(f"No tienes tareas pendientes{scope}. 🎉", "tareas", False)
        lines = []
        for r in rows:
            due = f" — 📅 {r['due']}" if r["due"] else ""
            flag = " (a confirmar)" if not r["confirmed"] else ""
            lines.append(f"- **{r['course']}**: {r['description']}{due}{flag}")
        cites = [Citation(r["note_path"], r["course"], r["course"], None, r["t"]) for r in rows if r["note_path"]]
        return Answer("Tareas pendientes:\n" + "\n".join(lines), "tareas", False, citations=cites, tasks=rows)

    def _search(self, question: str, course: str | None, today: date) -> list[Hit]:
        since, _ = self.detect_range(question, today)
        hits = self.index.search(question, k=self.top_k, course=course, since=since,
                                 kinds=("resumen", "punto"))
        if len(hits) < 2 or max(h.score for h in hits) < 0.025:
            # Los resúmenes no bastan: bajar a la transcripción.
            hits += self.index.search(question, k=self.top_k, course=course, since=since,
                                      kinds=("transcripcion", "nota"))
        seen: set[int] = set()
        unique = []
        for h in sorted(hits, key=lambda h: -h.score):
            if h.chunk_id not in seen:
                seen.add(h.chunk_id)
                unique.append(h)
        return unique[: self.top_k]

    def _open(self, question: str, course: str | None, today: date) -> Answer:
        hits = self._search(question, course, today)
        cites = [Citation(h.note_path, h.note_title, h.course, h.day, h.t) for h in hits]
        if not hits:
            return Answer("No encontré nada sobre eso en tus clases. Prueba con otras palabras "
                          "o revisa que la clase esté grabada.", "pregunta", False)
        if not self.provider.available:
            return Answer(self._search_mode_text(hits), "pregunta", False, citations=cites, hits=hits)
        context = "\n\n".join(
            f"[{h.course or ''} · {h.day or ''} · {format_ts(h.t)[3:] if h.t is not None else '—'}] "
            f"({h.kind}{', ' + h.speaker if h.speaker else ''}) {h.text}" for h in hits)
        messages: list[LLMMessage] = []
        for turn in self.history[-self.max_history:]:
            messages.append(LLMMessage("user", turn.question))
            messages.append(LLMMessage("assistant", turn.answer[:600]))
        messages.append(LLMMessage("user", f"Fragmentos:\n{context}\n\nPregunta: {question}"))
        try:
            resp = self.provider.generate(LLMRequest(system=CHAT_SYSTEM, messages=messages,
                                                     max_tokens=1200, tier=self.tier))
            return Answer(resp.text.strip(), "pregunta", True, resp.usage.total, cites, hits=hits)
        except LLMError as exc:
            log.warning("Chat sin IA: %s", exc)
            text = self._search_mode_text(hits) + f"\n\n_(IA no disponible: {exc})_"
            return Answer(text, "pregunta", False, citations=cites, hits=hits)

    @staticmethod
    def _search_mode_text(hits: list[Hit]) -> str:
        lines = ["Modo búsqueda (sin IA): estos son los fragmentos más relevantes."]
        for h in hits:
            where = " · ".join(x for x in [h.course or "", h.day or "",
                                           format_ts(h.t)[3:] if h.t is not None else ""] if x)
            snippet = h.text if len(h.text) < 280 else h.text[:277] + "…"
            lines.append(f"- **{where}** — {snippet}")
        return "\n".join(lines)
