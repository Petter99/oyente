"""Adaptador para modelos locales con Ollama (100 % sin internet ni API key)."""

from __future__ import annotations

from typing import Any

import httpx

from transcriptor.config.models import ProviderModels
from transcriptor.llm.base import (
    LLMBadRequestError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMUnavailableError,
    T,
    Usage,
    request_tokens,
)
from transcriptor.llm.schema import parse_json_output, strict_schema


class OllamaProvider:
    name = "ollama"

    def __init__(self, url: str, models: ProviderModels, client: httpx.Client | None = None) -> None:
        self.url = url.rstrip("/")
        self.models = models
        self._client = client or httpx.Client(timeout=600.0)

    @property
    def available(self) -> bool:
        try:
            return self._client.get(f"{self.url}/api/tags", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    def _call(self, req: LLMRequest, fmt: dict | None) -> LLMResponse:
        model = getattr(self.models, req.tier)
        body: dict[str, Any] = {
            "model": model,
            "stream": False,
            "messages": [{"role": "system", "content": req.system}]
            + [{"role": m.role, "content": m.content} for m in req.messages],
            "options": {"num_predict": req.max_tokens, "temperature": 0.2},
        }
        if fmt is not None:
            body["format"] = fmt
        try:
            r = self._client.post(f"{self.url}/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("El modelo local tardó demasiado") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError("Ollama no está en ejecución") from exc
        if r.status_code == 404:
            raise LLMUnavailableError(f"El modelo «{model}» no está descargado en Ollama")
        if r.status_code >= 400:
            raise LLMBadRequestError(f"Ollama: {r.text[:200]}")
        data = r.json()
        usage = Usage(input_tokens=data.get("prompt_eval_count", 0), output_tokens=data.get("eval_count", 0))
        return LLMResponse(data.get("message", {}).get("content", ""), self.name, model, usage)

    def generate(self, req: LLMRequest) -> LLMResponse:
        return self._call(req, None)

    def generate_structured(self, req: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        resp = self._call(req, strict_schema(schema))
        return parse_json_output(resp.text, schema), resp

    def count_tokens(self, req: LLMRequest) -> int:
        return request_tokens(req)
