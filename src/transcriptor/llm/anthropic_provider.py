"""Adaptador de Claude con el SDK oficial ``anthropic``.

Particularidades absorbidas aquí (modelos actuales):
- sin *prefill* de asistente ni ``tool_choice`` forzado: el JSON se pide con
  ``output_config.format`` (salida estructurada);
- el razonamiento no se desactiva: se usa ``output_config.effort`` según el nivel;
- caché de prompt en el sistema (fijo) para abaratar llamadas repetidas;
- ``stop_reason == "refusal"`` → LLMRefusalError (el respaldo toma el relevo);
- en Sonnet/Opus 5.5 se activa el respaldo del servidor ante rechazos (``fallbacks``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from transcriptor.config.models import ProviderModels
from transcriptor.llm.base import (
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
    LLMInvalidOutputError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMUnavailableError,
    T,
    Usage,
)
from transcriptor.llm.schema import parse_json_output, strict_schema

log = logging.getLogger(__name__)

EFFORT = {"economico": "low", "estandar": "medium", "maximo": "high"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
SERVER_FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, models: ProviderModels, client: Any = None) -> None:
        self.api_key = api_key
        self.models = models
        self._client = client
        self._server_fallback_ok = True

    @property
    def available(self) -> bool:
        return bool(self.api_key) or self._client is not None

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self.api_key:
                raise LLMUnavailableError("No hay API key de Claude configurada")
            self._client = anthropic.Anthropic(api_key=self.api_key, max_retries=2, timeout=180.0)
        return self._client

    def model_for(self, req: LLMRequest) -> str:
        return getattr(self.models, req.tier)

    def _params(self, req: LLMRequest) -> dict[str, Any]:
        system: Any = req.system
        if req.cache_system:
            system = [{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}]
        return {
            "model": self.model_for(req),
            "max_tokens": req.max_tokens,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
            "output_config": {"effort": EFFORT[req.tier]},
        }

    def _call(self, params: dict[str, Any]) -> Any:
        try:
            if self._server_fallback_ok and params["model"] in SERVER_FALLBACK_MODELS:
                try:
                    return self.client.beta.messages.create(
                        **params, betas=[FALLBACK_BETA], fallbacks="default"
                    )
                except anthropic.BadRequestError as exc:
                    if "fallback" not in str(exc).lower():
                        raise
                    log.info("Respaldo del servidor no disponible; se desactiva")
                    self._server_fallback_ok = False
            return self.client.messages.create(**params)
        except anthropic.AuthenticationError as exc:
            raise LLMAuthError("La API key de Claude no es válida") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMAuthError("La API key de Claude no tiene permisos para este modelo") from exc
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError("Claude: límite de uso alcanzado") from exc
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError("Claude tardó demasiado en responder") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailableError("Sin conexión con Claude") from exc
        except anthropic.BadRequestError as exc:
            raise LLMBadRequestError(f"Petición inválida para Claude: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMUnavailableError(f"Claude no disponible ({exc.status_code})") from exc
            raise LLMError(f"Error de Claude: {exc.message}") from exc

    def _to_response(self, msg: Any) -> LLMResponse:
        if msg.stop_reason == "refusal":
            category = getattr(getattr(msg, "stop_details", None), "category", None)
            raise LLMRefusalError(f"Claude rechazó la petición ({category or 'sin categoría'})")
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        u = msg.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        if msg.stop_reason == "max_tokens" and not text:
            raise LLMInvalidOutputError("Respuesta cortada por max_tokens")
        return LLMResponse(text, self.name, msg.model, usage)

    def generate(self, req: LLMRequest) -> LLMResponse:
        return self._to_response(self._call(self._params(req)))

    def generate_structured(self, req: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        params = self._params(req)
        params["output_config"] = {
            **params["output_config"],
            "format": {"type": "json_schema", "schema": strict_schema(schema)},
        }
        resp = self._to_response(self._call(params))
        return parse_json_output(resp.text, schema), resp

    def count_tokens(self, req: LLMRequest) -> int:
        params = self._params(req)
        try:
            res = self.client.messages.count_tokens(
                model=params["model"], system=params["system"], messages=params["messages"]
            )
            return int(res.input_tokens)
        except anthropic.APIError as exc:
            raise LLMUnavailableError(str(exc)) from exc

    # ------------------------------------------------------------------ lotes (50 % más barato)
    def submit_batch(self, items: list[tuple[str, LLMRequest, type[T]]]) -> str:
        requests = []
        for custom_id, req, schema in items:
            params = self._params(req)
            params["output_config"] = {
                **params["output_config"],
                "format": {"type": "json_schema", "schema": strict_schema(schema)},
            }
            requests.append({"custom_id": custom_id, "params": params})
        try:
            batch = self.client.messages.batches.create(requests=requests)
        except anthropic.APIError as exc:
            raise LLMUnavailableError(f"No se pudo crear el lote: {exc}") from exc
        return str(batch.id)

    def batch_status(self, batch_id: str) -> str:
        return str(self.client.messages.batches.retrieve(batch_id).processing_status)

    def batch_results(
        self, batch_id: str, schema: type[T]
    ) -> dict[str, tuple[T | None, LLMResponse | None]]:
        out: dict[str, tuple[T | None, LLMResponse | None]] = {}
        for entry in self.client.messages.batches.results(batch_id):
            if entry.result.type != "succeeded":
                out[entry.custom_id] = (None, None)
                continue
            try:
                resp = self._to_response(entry.result.message)
                out[entry.custom_id] = (parse_json_output(resp.text, schema), resp)
            except LLMError:
                out[entry.custom_id] = (None, None)
        return out


def _debug_dump(obj: Any) -> str:  # pragma: no cover - ayuda de diagnóstico
    try:
        return json.dumps(obj.to_dict(), ensure_ascii=False)[:2000]
    except Exception:
        return repr(obj)[:2000]
