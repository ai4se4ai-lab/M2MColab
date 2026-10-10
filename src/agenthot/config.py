"""Loads LLM backend configuration from a .env file / environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    # Walk up from the CWD looking for a .env, then fall back to repo root.
    here = Path.cwd()
    for candidate in [here, *here.parents]:
        env_path = candidate / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
            return
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(repo_root / ".env", override=False)


_load_env()


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    ollama_base_url: str
    openai_api_key: str | None
    openai_base_url: str
    anthropic_api_key: str | None
    temperature: float
    max_resamples: int

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            provider=os.getenv("LLM_PROVIDER", "ollama").strip().lower(),
            model=os.getenv("LLM_MODEL", "qwen3:8b").strip(),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
            max_resamples=int(os.getenv("LLM_MAX_RESAMPLES", "3")),
        )
