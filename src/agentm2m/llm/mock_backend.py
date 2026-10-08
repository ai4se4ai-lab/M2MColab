"""Deterministic backend for tests and CI: no network, no API key.

Produces reproducible, plausible-looking completions from a small set of
templates keyed by keywords in the prompt, plus a `replay` mode that returns
scripted answers in order (for exercising resample/escalation paths).
"""
from __future__ import annotations

import hashlib
import re

from .base import LLMBackend


class MockBackend(LLMBackend):
    name = "mock"

    def __init__(self, script: list[str] | None = None) -> None:
        self._script = list(script) if script else None
        self._script_pos = 0

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        if self._script is not None:
            if self._script_pos < len(self._script):
                value = self._script[self._script_pos]
                self._script_pos += 1
                return value
            return self._script[-1]
        return _template_response(prompt)


def _template_response(prompt: str) -> str:
    p = prompt.lower()
    seed = int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16)

    if "test oracle" in p or "oracle" in p:
        return (
            "def test_oracle():\n"
            "    result = implementation()\n"
            "    assert result is not None\n"
            "    assert result.get('status') == 'ok'\n"
        )

    if "code body" in p or "implement" in p:
        fn = _slug(prompt) or "handle_request"
        return (
            f"def {fn}(*args, **kwargs):\n"
            "    # TODO: generated stub\n"
            "    return {'status': 'ok'}\n"
        )

    if "api signature" in p or "signature" in p:
        name = _slug(prompt) or "handleRequest"
        params = ["id: string", "payload: object"][: 1 + seed % 2]
        return f"{name}({', '.join(params)}) -> Result"

    if "risk" in p and ("low" in p or "medium" in p or "high" in p) and "remediation" not in p and "action" not in p:
        return ["low", "medium", "high"][seed % 3]

    if "risk" in p and ("remediation" in p or "action" in p):
        return (
            '{"name": "restart_service", "command": "systemctl restart api", '
            '"risk_level": "low"}'
        )

    if "summar" in p or "report" in p:
        return "Summary: findings collected from the provided footprint were consistent and complete."

    # Generic fallback: short deterministic pseudo-natural-language string.
    return f"generated_value_{seed % 10000}"


def _slug(prompt: str) -> str | None:
    m = re.search(r"[A-Za-z][A-Za-z0-9_]{3,}", prompt)
    if not m:
        return None
    word = m.group(0)
    return word[0].lower() + word[1:]
