"""Anthropic Messages API backend."""
from __future__ import annotations

import requests

from .base import LLMBackend, LLMError


class AnthropicBackend(LLMBackend):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, *, timeout: float = 120.0) -> None:
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        self.last_usage = None
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 1024,
                    "temperature": temperature,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(f"Anthropic generate() failed: {exc}") from exc
        data = resp.json()
        usage = data.get("usage") or {}
        if usage:
            self.last_usage = (int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))
        return "".join(block.get("text", "") for block in data.get("content", [])).strip()
