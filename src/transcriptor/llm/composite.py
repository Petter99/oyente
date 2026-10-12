"""Proveedor nulo (modo sin API key), proveedor con respaldo y control de gasto mensual."""

from __future__ import annotations

import json
import logging
import threading
from datetime import date
from pathlib import Path

from transcriptor.fsutil import atomic_write_text
from transcriptor.llm.base import (
    LLMBudgetExceededError,
    LLMError,
    LLMInvalidOutputError,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    T,
    request_tokens,
)

log = logging.getLogger(__name__)


class NullLLMProvider:
    """Sin IA: la app funciona igual para transcribir; las funciones de IA quedan pendientes."""

    name = "none"
    available = False

    def generate(self, req: LLMRequest) -> LLMResponse:
        raise LLMUnavailableError("Modo local: no hay proveedor de IA configurado")

    def generate_structured(self, req: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        raise LLMUnavailableError("Modo local: no hay proveedor de IA configurado")

    def count_tokens(self, req: LLMRequest) -> int:
        return 0


def structured_with_retry(provider: LLMProvider, req: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
    """Un reintento con el error de validación antes de rendirse."""
    try:
        return provider.generate_structured(req, schema)
    except LLMInvalidOutputError as exc:
        retry = LLMRequest(
            system=req.system,
            messages=[*req.messages, LLMMessage(
                "user", f"Tu respuesta anterior no cumplía el formato ({exc}). Responde solo el JSON válido.")],
            max_tokens=req.max_tokens, tier=req.tier, cache_system=req.cache_system,
        )
        return provider.generate_structured(retry, schema)


class FallbackProvider:
    """Prueba el proveedor principal y, si falla (caída, límite, rechazo, JSON inválido), el siguiente."""

    def __init__(self, providers: list[LLMProvider]) -> None:
        self.providers = providers
        self.name = "+".join(p.name for p in providers) or "none"
        self.last_error: LLMError | None = None

    @property
    def available(self) -> bool:
        return any(p.available for p in self.providers)

    @property
    def primary(self) -> LLMProvider | None:
        return next((p for p in self.providers if p.available), None)

    def _try(self, fn_name: str, req: LLMRequest, schema: type[T] | None = None):  # type: ignore[no-untyped-def]
        errors: list[str] = []
        for p in self.providers:
            if not p.available:
                continue
            try:
                if schema is None:
                    return p.generate(req)
                return structured_with_retry(p, req, schema)
            except LLMError as exc:
                self.last_error = exc
                errors.append(f"{p.name}: {exc}")
                log.warning("Proveedor %s falló (%s); probando respaldo", p.name, type(exc).__name__)
                if not exc.retry_other:
                    raise
        raise LLMUnavailableError("; ".join(errors) or "No hay proveedor de IA disponible")

    def generate(self, req: LLMRequest) -> LLMResponse:
        return self._try("generate", req)  # type: ignore[no-any-return]

    def generate_structured(self, req: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        return self._try("generate_structured", req, schema)  # type: ignore[no-any-return]

    def count_tokens(self, req: LLMRequest) -> int:
        p = self.primary
        return p.count_tokens(req) if p else 0


class TokenLedger:
    """Uso mensual de tokens (local), para el contador visible y el límite de gasto."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, int]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def month(today: date | None = None) -> str:
        return (today or date.today()).strftime("%Y-%m")

    def used(self, today: date | None = None) -> int:
        m = self._load().get(self.month(today), {})
        return int(sum(m.values()))

    def record(self, resp: LLMResponse, today: date | None = None) -> None:
        with self._lock:
            data = self._load()
            m = data.setdefault(self.month(today), {})
            key = f"{resp.provider}"
            m[key] = m.get(key, 0) + resp.usage.total
            atomic_write_text(self.path, json.dumps(data))


class BudgetedProvider:
    """Aplica el límite mensual y registra el uso de cada llamada."""

    def __init__(self, inner: LLMProvider, ledger: TokenLedger, monthly_limit: int | None) -> None:
        self.inner = inner
        self.ledger = ledger
        self.limit = monthly_limit
        self.name = inner.name

    @property
    def available(self) -> bool:
        return self.inner.available

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.ledger.used() >= self.limit

    def _check(self, req: LLMRequest) -> None:
        if self.limit is not None and self.ledger.used() + request_tokens(req) > self.limit:
            raise LLMBudgetExceededError(
                "Llegaste al límite de tokens del mes; la app sigue en modo local.")

    def generate(self, req: LLMRequest) -> LLMResponse:
        self._check(req)
        resp = self.inner.generate(req)
        self.ledger.record(resp)
        return resp

    def generate_structured(self, req: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        self._check(req)
        out, resp = self.inner.generate_structured(req, schema)
        self.ledger.record(resp)
        return out, resp

    def count_tokens(self, req: LLMRequest) -> int:
        return self.inner.count_tokens(req)
