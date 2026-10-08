"""OpenAI (or OpenAI-compatible) chat-completions backend."""
from __future__ import annotations

import requests

from .base import LLMBackend, LLMError


class OpenAIBackend(LLMBackend):
    name = "openai"

    def __init__(self, api_key: str, model: str, *, base_url: str = "https://api.openai.com/v1", timeout: float = 120.0) -> None:
        if not api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        self.last_usage = None
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(f"OpenAI generate() failed: {exc}") from exc
        data = resp.json()
        usage = data.get("usage") or {}
        if usage:
            self.last_usage = (int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)))
        return data["choices"][0]["message"]["content"].strip()
