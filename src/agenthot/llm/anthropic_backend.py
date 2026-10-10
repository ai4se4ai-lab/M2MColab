"""Anthropic Messages API backend."""
from __future__ import annotations

import json

import requests

from .base import LLMBackend, LLMError

_JSON_HINT = "Respond with one JSON object only: no prose, no code fences."


class AnthropicBackend(LLMBackend):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, *, timeout: float = 300.0, max_tokens: int = 4096) -> None:
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        format: str | dict | None = None,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.last_usage = None
        sys_parts = [system] if system else []
        if format is not None:
            # No native JSON mode: ask for it, and (for a schema) show the schema.
            sys_parts.append(_JSON_HINT if format == "json" else f"{_JSON_HINT} It must conform to this JSON Schema:\n"
                             + json.dumps(format))
        body: dict = {
            "model": self.model,
            "max_tokens": int(max_tokens or self.max_tokens),
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if sys_parts:
            body["system"] = "\n\n".join(sys_parts)
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
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
