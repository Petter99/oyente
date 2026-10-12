"""Respaldo entre proveedores, límite de gasto, post-proceso y sugerencias en vivo (T8.3–T8.6, T9.*)."""

from __future__ import annotations

import threading
import time
from datetime import date
from pathlib import Path

import pytest
from tests.fakes import FakeLLM

from transcriptor.asr.base import Segment
from transcriptor.events import EventBus
from transcriptor.live.assistant import LiveAssistant
from transcriptor.llm.base import (
    LLMBudgetExceededError,
    LLMInvalidOutputError,
    LLMMessage,
    LLMRefusalError,
    LLMRequest,
    LLMUnavailableError,
)
from transcriptor.llm.composite import (
    BudgetedProvider,
    FallbackProvider,
    NullLLMProvider,
    TokenLedger,
)
from transcriptor.llm.schema import strict_schema
from transcriptor.llm.schemas import ChunkInsights, LLMClassSummary, LLMTask
from transcriptor.processing.postprocess import PostProcessor

REQ = LLMRequest(system="s", messages=[LLMMessage("user", "x")])


def test_fallback_on_refusal_and_outage() -> None:
    first = FakeLLM(name="claude", fail=LLMRefusalError("no"))
    second = FakeLLM(name="gemini")
    chain = FallbackProvider([first, second])
    out, resp = chain.generate_structured(REQ, LLMClassSummary)
    assert resp.provider == "gemini" and out.titulo
    with pytest.raises(LLMUnavailableError):
        FallbackProvider([FakeLLM(fail=LLMUnavailableError("x"))]).generate(REQ)


def test_invalid_json_retries_once_then_falls_back() -> None:
    calls = {"n": 0}

    class Flaky(FakeLLM):
        def generate_structured(self, req, schema):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            raise LLMInvalidOutputError("mal")

    chain = FallbackProvider([Flaky(name="a"), FakeLLM(name="b")])
    _, resp = chain.generate_structured(REQ, LLMClassSummary)
    assert calls["n"] == 2  # un reintento con el error de validación
    assert resp.provider == "b"


def test_t8_6_monthly_budget(tmp_path: Path) -> None:
    ledger = TokenLedger(tmp_path / "uso.json")
    provider = BudgetedProvider(FakeLLM(tokens=3000), ledger, monthly_limit=5000)
    provider.generate(REQ)
    assert ledger.used() == 3000
    with pytest.raises(LLMBudgetExceededError):
        provider.generate(
            LLMRequest(system="s", messages=[LLMMessage("user", "x")], max_tokens=10000)
        )
    assert not BudgetedProvider(FakeLLM(), ledger, None).exhausted


def test_budget_exceeded_does_not_try_fallback() -> None:
    chain = FallbackProvider([FakeLLM(fail=LLMBudgetExceededError("límite")), FakeLLM(name="b")])
    with pytest.raises(LLMBudgetExceededError):
        chain.generate(REQ)


def test_strict_schema_is_portable() -> None:
    schema = strict_schema(LLMClassSummary)
    text = str(schema)
    assert "$ref" not in text and "'default'" not in text
    task = schema["properties"]["tareas"]["items"]
    assert task["additionalProperties"] is False
    assert set(task["required"]) == set(LLMTask.model_fields)


SEGS = [
    Segment(
        i * 10.0,
        i * 10 + 8,
        f"frase número {i} sobre integrales y la regla LIATE",
        speaker="Profesor",
    )
    for i in range(30)
]


def test_postprocessor_one_call_per_class() -> None:
    llm = FakeLLM()
    summary, used = PostProcessor(llm).summarize(SEGS, date(2026, 10, 12), "Cálculo II")
    assert used and len(llm.calls) == 1
    assert summary.tasks[0].due == date(2026, 10, 15)  # «el próximo jueves» resuelto por código
    assert summary.tasks[0].t == 1.0
    prompt = llm.calls[0][0].messages[0].content
    assert "[00:00:00] P:" in prompt  # transcripción compacta


def test_postprocessor_without_llm_is_local() -> None:
    summary, used = PostProcessor(NullLLMProvider()).summarize(
        SEGS, date(2026, 10, 12), "Cálculo II"
    )
    assert not used and summary.source == "local"


def test_postprocessor_falls_back_to_local_on_error() -> None:
    summary, used = PostProcessor(FakeLLM(fail=LLMUnavailableError("caído"))).summarize(
        SEGS, date(2026, 10, 12), "Cálculo II"
    )
    assert not used and summary.source == "local"


def test_postprocessor_merges_live_partials_cheaply() -> None:
    llm = FakeLLM()
    partial = ChunkInsights(
        tareas=[], puntos_clave=[], preguntas_sugeridas=[], temas=["Integrales"]
    )
    PostProcessor(llm).summarize(SEGS, date(2026, 10, 12), "Cálculo II", partials=[partial])
    prompt = llm.calls[0][0].messages[0].content
    assert "Parciales" in prompt and len(prompt) < 9000


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _assistant(llm: FakeLLM, clock: Clock, **kw: object) -> tuple[LiveAssistant, list]:
    bus = EventBus()
    events: list = []
    bus.subscribe(lambda e: events.append(e))
    return LiveAssistant(llm, bus, "Cálculo II", clock=clock, **kw), events  # type: ignore[arg-type]


def test_t9_1_trigger_word_calls_soon() -> None:
    llm, clock = FakeLLM(), Clock()
    assistant, events = _assistant(llm, clock)
    assistant.on_segment(
        Segment(0, 5, "La tarea es para el jueves, hoja cuatro.", speaker="Profesor")
    )
    assert not llm.calls  # espera a que termine la idea
    clock.t = 25
    assistant.on_segment(Segment(20, 25, "Y lean el capítulo siete.", speaker="Profesor"))
    time.sleep(0.2)
    assert len(llm.calls) == 1
    sent = llm.calls[0][0].messages[0].content
    assert "hoja cuatro" in sent and "capítulo siete" in sent
    assert any(e.type == "insights" for e in events)


def test_interval_without_triggers_and_only_new_text() -> None:
    llm, clock = FakeLLM(), Clock()
    assistant, _ = _assistant(llm, clock, interval_min=10)
    for i in range(10):
        clock.t = i * 30
        assistant.on_segment(Segment(i * 30, i * 30 + 25, f"Explicación larga número {i} " * 5))
    assert not llm.calls
    clock.t = 601
    assistant.on_segment(Segment(601, 610, "Seguimos con el tema " * 5))
    time.sleep(0.2)
    assert len(llm.calls) == 1
    clock.t = 1300
    assistant.on_segment(Segment(1300, 1310, "Otro bloque nuevo " * 20))
    time.sleep(0.2)
    second = llm.calls[1][0].messages[0].content
    assert "número 3" not in second  # no reenvía texto ya procesado


def test_t9_2_max_calls_per_class() -> None:
    llm, clock = FakeLLM(), Clock()
    assistant, events = _assistant(llm, clock, max_calls=2)
    for i in range(5):
        clock.t = i * 100
        assistant.on_segment(Segment(0, 1, "No olviden: examen el jueves " * 10))
        clock.t = i * 100 + 30
        assistant.maybe_call(background=False)
    assert len(llm.calls) == 2
    assert any(e.type == "live_paused" for e in events)


def test_t9_3_slow_llm_never_blocks_transcription() -> None:
    gate = threading.Event()
    llm = FakeLLM(on_call=lambda req: gate.wait(2))
    clock = Clock()
    assistant, _ = _assistant(llm, clock)
    assistant.on_segment(Segment(0, 1, "La tarea es para el jueves " * 10))
    clock.t = 30
    t0 = time.monotonic()
    for i in range(50):
        assistant.on_segment(Segment(i, i + 1, "texto"))
    assert time.monotonic() - t0 < 0.5
    gate.set()


def test_t9_5_disabled_without_api_key() -> None:
    assistant = LiveAssistant(NullLLMProvider(), EventBus(), "x")
    assert not assistant.enabled
