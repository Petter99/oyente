"""Suite de contrato: la MISMA batería se ejecuta contra cada adaptador (T8.1, T8.3, T8.4, T8.10).

Usa clientes simulados con la forma de respuesta de cada SDK oficial; no hay red.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from google.genai import errors as gerrors

from transcriptor.config.models import AIConfig
from transcriptor.llm.anthropic_provider import AnthropicProvider
from transcriptor.llm.base import (
    LLMAuthError,
    LLMInvalidOutputError,
    LLMMessage,
    LLMRateLimitError,
    LLMRefusalError,
    LLMRequest,
    LLMUnavailableError,
)
from transcriptor.llm.gemini_provider import GeminiProvider
from transcriptor.llm.ollama_provider import OllamaProvider
from transcriptor.llm.schemas import LLMClassSummary

MODELS = AIConfig().models
SUMMARY = {"titulo": "Integrales", "resumen": "Se vio integración.", "puntos_clave": [],
           "temas": ["Integración por partes"],
           "tareas": [{"descripcion": "Hoja 4", "fecha_texto": "el jueves", "tipo": "tarea", "minuto": ""}]}
REQ = LLMRequest(system="sistema fijo", messages=[LLMMessage("user", "hola")], max_tokens=500)


@dataclass
class Scenario:
    kind: str  # ok | json | badjson | refusal | auth | ratelimit | network


# ----------------------------------------------------------------------------- Anthropic
class FakeAnthropic:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.last: dict[str, Any] = {}
        self.messages = SimpleNamespace(create=self.create, count_tokens=lambda **k: SimpleNamespace(input_tokens=42))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **params: Any) -> Any:
        self.last = params
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        k = self.scenario.kind
        if k == "auth":
            raise anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)
        if k == "ratelimit":
            raise anthropic.RateLimitError("slow", response=httpx.Response(429, request=req), body=None)
        if k == "network":
            raise anthropic.APIConnectionError(request=req)
        text = {"ok": "hola", "json": json.dumps(SUMMARY), "badjson": "{no es json", "refusal": ""}[k]
        return SimpleNamespace(
            stop_reason="refusal" if k == "refusal" else "end_turn",
            stop_details=SimpleNamespace(category="cyber", explanation="x") if k == "refusal" else None,
            content=[SimpleNamespace(type="text", text=text)] if text else [],
            model=params["model"],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=100,
                                  cache_creation_input_tokens=0),
        )


def make_anthropic(s: Scenario) -> Any:
    return AnthropicProvider(None, MODELS["anthropic"], client=FakeAnthropic(s))


# ----------------------------------------------------------------------------- Gemini
class FakeGemini:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.last: dict[str, Any] = {}
        self.models = SimpleNamespace(generate_content=self.generate,
                                      count_tokens=lambda **k: SimpleNamespace(total_tokens=42))

    def generate(self, **params: Any) -> Any:
        self.last = params
        k = self.scenario.kind
        if k == "auth":
            raise gerrors.ClientError(401, {"error": {"message": "API key not valid"}})
        if k == "ratelimit":
            raise gerrors.ClientError(429, {"error": {"message": "quota"}})
        if k == "network":
            raise httpx.ConnectError("sin red")
        text = {"ok": "hola", "json": json.dumps(SUMMARY), "badjson": "{no es json", "refusal": ""}[k]
        return SimpleNamespace(
            text=text, prompt_feedback=None,
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value="SAFETY" if k == "refusal" else "STOP"))],
            usage_metadata=SimpleNamespace(prompt_token_count=110, candidates_token_count=5,
                                           cached_content_token_count=100),
        )


def make_gemini(s: Scenario) -> Any:
    return GeminiProvider(None, MODELS["gemini"], client=FakeGemini(s))


# ----------------------------------------------------------------------------- Ollama
def make_ollama(s: Scenario) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        k = s.kind
        if k == "network":
            raise httpx.ConnectError("sin red")
        if k in ("auth", "ratelimit"):
            return httpx.Response(404, json={"error": "model not found"})
        if k == "refusal":
            return httpx.Response(200, json={"message": {"content": "{}"}})
        text = {"ok": "hola", "json": json.dumps(SUMMARY), "badjson": "{no es json"}[k]
        return httpx.Response(200, json={"message": {"content": text}, "prompt_eval_count": 10, "eval_count": 5})

    return OllamaProvider("http://localhost:11434", MODELS["ollama"],
                          client=httpx.Client(transport=httpx.MockTransport(handler)))


ADAPTERS: dict[str, Callable[[Scenario], Any]] = {
    "anthropic": make_anthropic, "gemini": make_gemini, "ollama": make_ollama,
}


@pytest.fixture(params=list(ADAPTERS))
def factory(request: pytest.FixtureRequest) -> Callable[[str], Any]:
    return lambda kind: ADAPTERS[request.param](Scenario(kind))


def test_generate_returns_text_and_usage(factory: Callable[[str], Any]) -> None:
    resp = factory("ok").generate(REQ)
    assert resp.text == "hola"
    assert resp.usage.output_tokens == 5
    assert resp.usage.total > 0


def test_structured_output_is_validated(factory: Callable[[str], Any]) -> None:
    summary, resp = factory("json").generate_structured(REQ, LLMClassSummary)
    assert isinstance(summary, LLMClassSummary)
    assert summary.tareas[0].fecha_texto == "el jueves"


def test_invalid_json_raises_invalid_output(factory: Callable[[str], Any]) -> None:
    with pytest.raises(LLMInvalidOutputError):
        factory("badjson").generate_structured(REQ, LLMClassSummary)


def test_network_error_is_unavailable(factory: Callable[[str], Any]) -> None:
    with pytest.raises(LLMUnavailableError):
        factory("network").generate(REQ)


@pytest.mark.parametrize("name", ["anthropic", "gemini"])
def test_refusal_is_normalized(name: str) -> None:
    with pytest.raises(LLMRefusalError):
        ADAPTERS[name](Scenario("refusal")).generate(REQ)


@pytest.mark.parametrize(("name", "kind", "error"), [
    ("anthropic", "auth", LLMAuthError), ("gemini", "auth", LLMAuthError),
    ("anthropic", "ratelimit", LLMRateLimitError), ("gemini", "ratelimit", LLMRateLimitError),
    ("ollama", "auth", LLMUnavailableError),
])
def test_errors_are_normalized(name: str, kind: str, error: type) -> None:
    with pytest.raises(error):
        ADAPTERS[name](Scenario(kind)).generate(REQ)


def test_anthropic_request_shape() -> None:
    """Sin prefill, sin tool_choice forzado, JSON por output_config, sistema cacheado, effort por nivel."""
    provider = make_anthropic(Scenario("json"))
    provider.generate_structured(REQ, LLMClassSummary)
    sent = provider.client.last
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["messages"][-1]["role"] == "user"
    assert "tool_choice" not in sent and "thinking" not in sent
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["output_config"]["effort"] == "low"
    schema = sent["output_config"]["format"]["schema"]
    assert schema["additionalProperties"] is False and "$defs" not in json.dumps(schema)


def test_anthropic_server_fallback_on_sonnet() -> None:
    provider = make_anthropic(Scenario("ok"))
    provider.generate(LLMRequest(system="s", messages=[LLMMessage("user", "x")], tier="estandar"))
    sent = provider.client.last
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]


def test_gemini_request_shape() -> None:
    provider = make_gemini(Scenario("json"))
    provider.generate_structured(REQ, LLMClassSummary)
    cfg = provider.client.last["config"]
    assert cfg.response_mime_type == "application/json"
    assert cfg.system_instruction == "sistema fijo"
    assert cfg.response_json_schema["additionalProperties"] is False


def test_cache_tokens_are_reported() -> None:
    resp = make_anthropic(Scenario("ok")).generate(REQ)
    assert resp.usage.cache_read_tokens == 100  # T8.7 (forma; en vivo se mide aparte)


def test_api_key_never_in_error_messages() -> None:
    key = "sk-ant-api03-NOFILTRAR123456"
    provider = AnthropicProvider(key, MODELS["anthropic"], client=FakeAnthropic(Scenario("auth")))
    with pytest.raises(LLMAuthError) as exc:
        provider.generate(REQ)
    assert key not in str(exc.value)
