"""Construye el proveedor configurado (principal + respaldo + límite de gasto)."""

from __future__ import annotations

from transcriptor.config.models import AIConfig, ProviderName
from transcriptor.config.secrets import Secret, SecretStore
from transcriptor.llm.base import LLMProvider
from transcriptor.llm.composite import (
    BudgetedProvider,
    FallbackProvider,
    NullLLMProvider,
    TokenLedger,
)
from transcriptor.paths import data_dir


def single_provider(name: ProviderName, cfg: AIConfig, secrets: SecretStore) -> LLMProvider:
    if name == "anthropic":
        from transcriptor.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(secrets.get(Secret.ANTHROPIC_API_KEY), cfg.models["anthropic"])
    if name == "gemini":
        from transcriptor.llm.gemini_provider import GeminiProvider

        return GeminiProvider(secrets.get(Secret.GEMINI_API_KEY), cfg.models["gemini"])
    if name == "ollama":
        from transcriptor.llm.ollama_provider import OllamaProvider

        return OllamaProvider(cfg.ollama_url, cfg.models["ollama"])
    return NullLLMProvider()


def build_provider(
    cfg: AIConfig, secrets: SecretStore, ledger: TokenLedger | None = None
) -> BudgetedProvider:
    chain: list[LLMProvider] = []
    for name in (cfg.provider, cfg.fallback_provider):
        if name != "none" and all(p.name != name for p in chain):
            chain.append(single_provider(name, cfg, secrets))
    inner: LLMProvider = FallbackProvider(chain) if chain else NullLLMProvider()
    ledger = ledger or TokenLedger(data_dir() / "uso_tokens.json")
    return BudgetedProvider(inner, ledger, cfg.monthly_token_limit)
