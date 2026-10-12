"""Extracción posterior a la clase: resumen, puntos, temas y tareas.

Con LLM: UNA sola llamada estructurada por clase (o unión de los parciales de las
sugerencias en vivo; o *map-reduce* en clases muy largas). Sin LLM: temas con KeyBERT/TF-IDF
y «posibles tareas» por reglas. La transcripción nunca se modifica.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date

from transcriptor.asr.base import Segment
from transcriptor.llm.base import LLMError, LLMMessage, LLMProvider, LLMRequest, Tier, estimate_tokens
from transcriptor.llm.composite import structured_with_retry
from transcriptor.llm.prompts import MERGE_SYSTEM, SUMMARY_SYSTEM, compact_transcript, parse_ts
from transcriptor.llm.schemas import ChunkInsights, LLMClassSummary
from transcriptor.nlp.dates import resolve
from transcriptor.nlp.keywords import extract_topics
from transcriptor.nlp.tasks_rules import extract_tasks
from transcriptor.session.models import ClassSummary, KeyPoint, TaskItem

log = logging.getLogger(__name__)

# Por encima de esto (≈ 2 h de clase) se procesa por bloques con el modelo económico.
MAP_REDUCE_TOKENS = 40_000
BLOCK_TOKENS = 15_000


def from_llm(result: LLMClassSummary, class_day: date) -> ClassSummary:
    tasks = [
        TaskItem(description=t.descripcion.strip(), due_text=t.fecha_texto.strip(),
                 due=resolve(t.fecha_texto, class_day) if t.fecha_texto.strip() else None,
                 t=parse_ts(t.minuto) if t.minuto else None, kind=t.tipo, confirmed=True)
        for t in result.tareas if t.descripcion.strip()
    ]
    points = [KeyPoint(text=p.texto.strip(), t=parse_ts(p.minuto) if p.minuto else None)
              for p in result.puntos_clave if p.texto.strip()]
    topics = []
    for topic in result.temas:
        clean = topic.strip().strip("#[]").strip()
        if clean and clean.casefold() not in {t.casefold() for t in topics}:
            topics.append(clean)
    return ClassSummary(title=result.titulo.strip(), summary=result.resumen.strip(),
                        key_points=points, topics=topics[:8], tasks=tasks, source="llm")


def local_summary(segments: list[Segment], class_day: date,
                  topic_extractor: Callable[[str], list[str]] = extract_topics) -> ClassSummary:
    text = " ".join(s.text for s in segments if s.speaker != "Yo")
    return ClassSummary(topics=topic_extractor(text) if text else [],
                        tasks=extract_tasks(segments, class_day), source="local")


class PostProcessor:
    def __init__(self, provider: LLMProvider, tier: Tier = "economico") -> None:
        self.provider = provider
        self.tier = tier
        self.tokens_used = 0

    def _ask(self, system: str, user: str, schema: type, max_tokens: int = 3000):  # type: ignore[no-untyped-def]
        req = LLMRequest(system=system, messages=[LLMMessage("user", user)],
                         max_tokens=max_tokens, tier=self.tier)
        result, resp = structured_with_retry(self.provider, req, schema)
        self.tokens_used += resp.usage.total
        return result

    def summarize(self, segments: list[Segment], class_day: date, course: str,
                  partials: list[ChunkInsights] | None = None) -> tuple[ClassSummary, bool]:
        """Devuelve (resumen, procesado_con_llm). Nunca lanza: si el LLM falla, usa el modo local."""
        if not segments:
            return ClassSummary(source="local"), False
        if self.provider.available:
            try:
                return from_llm(self._llm_summary(segments, course, partials), class_day), True
            except LLMError as exc:
                log.warning("Extracción con IA no disponible (%s); se usa el modo local", exc)
        return local_summary(segments, class_day), False

    def _llm_summary(self, segments: list[Segment], course: str,
                     partials: list[ChunkInsights] | None) -> LLMClassSummary:
        transcript = compact_transcript(segments)
        header = f"Curso: {course}\n"
        if partials:
            # Ya se extrajo por bloques durante la clase: solo se unen (barato).
            data = json.dumps([p.model_dump() for p in partials], ensure_ascii=False)
            excerpt = transcript[:6000]
            return self._ask(MERGE_SYSTEM, f"{header}Parciales:\n{data}\n\nExtracto inicial:\n{excerpt}",
                             LLMClassSummary)
        if estimate_tokens(transcript) <= MAP_REDUCE_TOKENS:
            return self._ask(SUMMARY_SYSTEM, f"{header}Transcripción:\n{transcript}", LLMClassSummary)
        # Clase muy larga: extraer por bloques y unir.
        blocks, current, size = [], [], 0
        for line in transcript.splitlines():
            current.append(line)
            size += estimate_tokens(line)
            if size >= BLOCK_TOKENS:
                blocks.append("\n".join(current))
                current, size = [], 0
        if current:
            blocks.append("\n".join(current))
        from transcriptor.llm.prompts import LIVE_SYSTEM

        parts = [self._ask(LIVE_SYSTEM, f"{header}Estado: (inicio)\nFragmento nuevo:\n{b}", ChunkInsights, 2000)
                 for b in blocks]
        return self._llm_summary(segments, course, parts)
