"""Interfaz delgada común a todos los proveedores de LLM.

Cada adaptador usa el SDK oficial de su proveedor y traduce sus particularidades
(salida estructurada, caché, errores, rechazos) a esta interfaz.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

Tier = Literal["economico", "estandar", "maximo"]
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Error normalizado de un proveedor."""

    retry_other = True  # ¿tiene sentido probar con el proveedor de respaldo?


class LLMUnavailableError(LLMError):
    """Sin API key, sin internet o servicio caído."""


class LLMAuthError(LLMError):
    """API key inválida o sin permisos."""


class LLMRateLimitError(LLMError):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMRefusalError(LLMError):
    """El modelo rechazó la petición por seguridad."""


class LLMInvalidOutputError(LLMError):
    """La respuesta no cumple el esquema."""


class LLMBadRequestError(LLMError):
    retry_other = True


class LLMBudgetExceededError(LLMError):
    """Se alcanzó el límite mensual de tokens configurado."""

    retry_other = False


@dataclass
class LLMMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class LLMRequest:
    system: str
    messages: list[LLMMessage]
    max_tokens: int = 4000
    tier: Tier = "economico"
    cache_system: bool = True  # el prompt de sistema es fijo: se cachea para ahorrar tokens


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)


class LLMProvider(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    def generate(self, req: LLMRequest) -> LLMResponse: ...

    def generate_structured(self, req: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]: ...

    def count_tokens(self, req: LLMRequest) -> int: ...


def estimate_tokens(text: str) -> int:
    """Estimación rápida y local (≈ 3,5 caracteres por token en español)."""
    return max(1, int(len(text) / 3.5))


def request_tokens(req: LLMRequest) -> int:
    return (
        estimate_tokens(req.system + "".join(m.content for m in req.messages)) + req.max_tokens // 4
    )
