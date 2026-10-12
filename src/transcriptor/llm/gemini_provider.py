"""Adaptador de Gemini con el SDK oficial ``google-genai``."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from transcriptor.config.models import ProviderModels
from transcriptor.llm.base import (
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
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

BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str | None, models: ProviderModels, client: Any = None) -> None:
        self.api_key = api_key
        self.models = models
        self._client = client

    @property
    def available(self) -> bool:
        return bool(self.api_key) or self._client is not None

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self.api_key:
                raise LLMUnavailableError("No hay API key de Gemini configurada")
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _contents(self, req: LLMRequest) -> list[Any]:
        from google.genai import types

        return [
            types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[types.Part(text=m.content)],
            )
            for m in req.messages
        ]

    def _call(self, req: LLMRequest, json_schema: dict | None = None) -> LLMResponse:
        from google.genai import errors, types

        model = getattr(self.models, req.tier)
        config = types.GenerateContentConfig(
            system_instruction=req.system, max_output_tokens=req.max_tokens
        )
        if json_schema is not None:
            config.response_mime_type = "application/json"
            config.response_json_schema = json_schema
        try:
            resp = self.client.models.generate_content(
                model=model, contents=self._contents(req), config=config
            )
        except errors.ClientError as exc:
            if exc.code in (401, 403):
                raise LLMAuthError("La API key de Gemini no es válida") from exc
            if exc.code == 429:
                raise LLMRateLimitError("Gemini: límite de uso alcanzado") from exc
            raise LLMBadRequestError(f"Petición inválida para Gemini: {exc}") from exc
        except errors.ServerError as exc:
            raise LLMUnavailableError(f"Gemini no disponible ({exc.code})") from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("Gemini tardó demasiado en responder") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError("Sin conexión con Gemini") from exc
        except errors.APIError as exc:
            raise LLMError(f"Error de Gemini: {exc}") from exc

        feedback = getattr(resp, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise LLMRefusalError(f"Gemini bloqueó la petición ({feedback.block_reason})")
        candidates = getattr(resp, "candidates", None) or []
        if candidates:
            reason = getattr(candidates[0].finish_reason, "value", str(candidates[0].finish_reason))
            if reason in BLOCKED:
                raise LLMRefusalError(f"Gemini rechazó la respuesta ({reason})")
        meta = getattr(resp, "usage_metadata", None)
        usage = Usage(
            input_tokens=(getattr(meta, "prompt_token_count", 0) or 0)
            - (getattr(meta, "cached_content_token_count", 0) or 0),
            output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
            cache_read_tokens=getattr(meta, "cached_content_token_count", 0) or 0,
        )
        return LLMResponse(resp.text or "", self.name, model, usage)

    def generate(self, req: LLMRequest) -> LLMResponse:
        return self._call(req)

    def generate_structured(self, req: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        resp = self._call(req, strict_schema(schema))
        return parse_json_output(resp.text, schema), resp

    def count_tokens(self, req: LLMRequest) -> int:
        try:
            res = self.client.models.count_tokens(
                model=getattr(self.models, req.tier), contents=self._contents(req)
            )
            return int(res.total_tokens or 0)
        except Exception as exc:
            raise LLMUnavailableError(str(exc)) from exc
