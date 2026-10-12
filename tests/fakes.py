"""Dobles de prueba compartidos."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from transcriptor.llm.base import LLMRequest, LLMResponse, LLMUnavailableError, T, Usage
from transcriptor.llm.schemas import ChunkInsights, LLMClassSummary, LLMKeyPoint, LLMTask


class FakeLLM:
    """Proveedor de LLM falso: responde según el esquema pedido y cuenta llamadas."""

    def __init__(
        self,
        responses: dict[type, Any] | None = None,
        name: str = "fake",
        fail: Exception | None = None,
        tokens: int = 1000,
        on_call: Callable[[LLMRequest], None] | None = None,
    ) -> None:
        self.name = name
        self.responses = responses or {}
        self.fail = fail
        self.calls: list[tuple[LLMRequest, type | None]] = []
        self.tokens = tokens
        self.on_call = on_call

    @property
    def available(self) -> bool:
        return True

    def _resp(self, text: str = "") -> LLMResponse:
        return LLMResponse(text, self.name, "fake-model", Usage(self.tokens // 2, self.tokens // 2))

    def generate(self, req: LLMRequest) -> LLMResponse:
        self.calls.append((req, None))
        if self.on_call:
            self.on_call(req)
        if self.fail:
            raise self.fail
        return self._resp(self.responses.get(str, "respuesta"))

    def generate_structured(self, req: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        self.calls.append((req, schema))
        if self.on_call:
            self.on_call(req)
        if self.fail:
            raise self.fail
        value = self.responses.get(schema) or default_for(schema)
        if callable(value) and not isinstance(value, BaseModel):
            value = value(req)
        return value, self._resp(value.model_dump_json())  # type: ignore[return-value]

    def count_tokens(self, req: LLMRequest) -> int:
        return 100


def default_for(schema: type) -> BaseModel:
    if schema is LLMClassSummary:
        return LLMClassSummary(
            titulo="Integración por partes",
            resumen="Se explicó la integración por partes y la regla LIATE.",
            puntos_clave=[LLMKeyPoint(texto="LIATE ordena qué elegir como u", minuto="00:00:01")],
            temas=["Integración por partes", "Regla LIATE"],
            tareas=[
                LLMTask(
                    descripcion="Hoja de ejercicios 4",
                    fecha_texto="el próximo jueves",
                    tipo="tarea",
                    minuto="00:00:01",
                )
            ],
            curso_sugerido="",
            profesor_sugerido="",
        )
    if schema is ChunkInsights:
        return ChunkInsights(
            tareas=[], puntos_clave=[], preguntas_sugeridas=[], temas=["Integrales"]
        )
    raise LLMUnavailableError(f"sin respuesta para {schema}")
