from __future__ import annotations

from ..config import LLMConfig
from .anthropic_backend import AnthropicBackend
from .base import LLMBackend
from .host_backend import HostBackend
from .mock_backend import MockBackend
from .ollama_backend import OllamaBackend
from .openai_backend import OpenAIBackend


def make_backend(cfg: LLMConfig | None = None, *, override_provider: str | None = None, override_model: str | None = None) -> LLMBackend:
    cfg = cfg or LLMConfig.from_env()
    provider = (override_provider or cfg.provider).strip().lower()
    model = override_model or cfg.model

    if provider == "host":
        return HostBackend()
    if provider == "mock":
        return MockBackend()
    if provider == "ollama":
        return OllamaBackend(cfg.ollama_base_url, model)
    if provider == "openai":
        return OpenAIBackend(cfg.openai_api_key or "", model, base_url=cfg.openai_base_url)
    if provider == "anthropic":
        return AnthropicBackend(cfg.anthropic_api_key or "", model)
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}' (expected host|ollama|openai|anthropic|mock)")
