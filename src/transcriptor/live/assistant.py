"""Sugerencias del LLM durante la clase, con diseño de bajo consumo (plan §6.5).

- Disparadores locales (sin tokens): «tarea», «examen», «importante»… → llamada inmediata.
- Si no hay disparadores: una llamada cada N minutos.
- Solo se envía el texto NUEVO + un estado corto; modelo económico; tope por clase.
- Asíncrono: nunca bloquea la transcripción. Si falla, la clase sigue.
- Los parciales se reutilizan en el resumen final (no se reenvía toda la transcripción).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from transcriptor.asr.base import Segment
from transcriptor.events import EventBus
from transcriptor.llm.base import (
    LLMBudgetExceededError,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMRequest,
)
from transcriptor.llm.composite import structured_with_retry
from transcriptor.llm.prompts import LIVE_SYSTEM, compact_transcript
from transcriptor.llm.schemas import ChunkInsights
from transcriptor.nlp.tasks_rules import live_trigger

log = logging.getLogger(__name__)

MIN_NEW_CHARS = 200
TRIGGER_DELAY_S = 20.0  # tras un disparador, esperar a que el profesor termine la idea


class LiveAssistant:
    def __init__(
        self,
        provider: LLMProvider,
        bus: EventBus,
        course: str,
        interval_min: int = 10,
        max_calls: int = 12,
        enabled: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider = provider
        self.bus = bus
        self.course = course
        self.interval = interval_min * 60
        self.max_calls = max_calls
        self.enabled = enabled and provider.available
        self.clock = clock
        self.calls = 0
        self.tokens = 0
        self.insights: list[ChunkInsights] = []
        self._pending: list[Segment] = []
        self._last_call = clock()
        self._trigger_at: float | None = None
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        self.paused_reason: str | None = None

    def set_enabled(self, value: bool) -> None:
        self.enabled = value and self.provider.available
        self.bus.emit("live_assistant", enabled=self.enabled)

    def on_segment(self, seg: Segment) -> None:
        with self._lock:
            self._pending.append(seg)
            if self._trigger_at is None and live_trigger(seg.text):
                self._trigger_at = self.clock() + TRIGGER_DELAY_S
        self.maybe_call()

    def due(self) -> bool:
        if not self.enabled or self.paused_reason:
            return False
        if self.calls >= self.max_calls:
            self.paused_reason = "Se alcanzó el máximo de sugerencias para esta clase."
            self.bus.emit("live_paused", reason=self.paused_reason)
            return False
        with self._lock:
            new_chars = sum(len(s.text) for s in self._pending)
            now = self.clock()
            if self._trigger_at is not None and now >= self._trigger_at:
                return new_chars > 0
            return new_chars >= MIN_NEW_CHARS and now - self._last_call >= self.interval

    def maybe_call(self, background: bool = True) -> None:
        if not self.due() or not self._busy.acquire(blocking=False):
            return
        with self._lock:
            batch, self._pending = self._pending, []
            self._trigger_at = None
            self._last_call = self.clock()
        if background:
            threading.Thread(target=self._run, args=(batch,), daemon=True, name="live-llm").start()
        else:
            self._run(batch)

    def flush(self) -> None:
        """Al terminar la clase: procesa el texto que quedó pendiente (si vale la pena)."""
        with self._lock:
            pending = sum(len(s.text) for s in self._pending)
        if self.enabled and not self.paused_reason and pending >= MIN_NEW_CHARS and self.insights:
            self._trigger_at = self.clock()
            self.maybe_call(background=False)

    def _state(self) -> str:
        tasks = [t.descripcion for i in self.insights for t in i.tareas][-8:]
        topics = sorted({t for i in self.insights for t in i.temas})[-12:]
        return f"Tareas ya detectadas: {tasks or 'ninguna'}. Temas: {topics or 'ninguno'}."

    def _run(self, batch: list[Segment]) -> None:
        try:
            req = LLMRequest(
                system=LIVE_SYSTEM,
                messages=[
                    LLMMessage(
                        "user",
                        f"Curso: {self.course}\nEstado: {self._state()}\n"
                        f"Fragmento nuevo:\n{compact_transcript(batch)}",
                    )
                ],
                max_tokens=1500,
                tier="economico",
            )
            result, resp = structured_with_retry(self.provider, req, ChunkInsights)
            self.calls += 1
            self.tokens += resp.usage.total
            self.insights.append(result)
            self.bus.emit(
                "insights",
                insights=result,
                calls=self.calls,
                tokens=self.tokens,
                start=batch[0].start if batch else 0.0,
            )
        except LLMBudgetExceededError as exc:
            self.paused_reason = str(exc)
            self.bus.emit("live_paused", reason=self.paused_reason)
        except LLMError as exc:
            log.warning("Sugerencias no disponibles: %s", exc)
            self.bus.emit("live_unavailable", reason=str(exc))
        finally:
            self._busy.release()
