"""Pluggable LLM backend interface.

A stochastic binding samples v ~ D(prompt) (Definition 1 / Sec III-B of the
paper). `LLMBackend.generate` is that sampling function: it takes the fully
built prompt (prompt_text ⊕ footprint, already assembled by the engine so
the backend never sees anything the footprint didn't authorize) and returns
one sampled string.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMBackend(ABC):
    name: str = "base"
    # (input_tokens, output_tokens) the provider reported for the most recent
    # generate() call, when it reports them; None -> fall back to count_tokens.
    last_usage: tuple[int, int] | None = None

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        format: str | dict | None = None,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Return one sampled completion for `prompt`.

        `format` ("json" or a JSON schema) asks for structured output,
        `system` replaces the default system prompt and `max_tokens` caps the
        completion; the AutoM2M builder relies on all three. A backend that
        cannot honour `format` must still return the text it got."""
        raise NotImplementedError

    def count_tokens(self, text: str) -> int:
        # Cheap, provider-agnostic approximation used for the token_meter in
        # evaluation/ when a provider doesn't expose exact usage counts.
        return max(1, len(text) // 4)


class LLMError(RuntimeError):
    """Raised when a backend fails to produce a completion (used to trigger escalation)."""


class PendingSample(Exception):
    """Raised instead of sampling when the backend is *deferred* (host mode):
    the value for this binding will be supplied later by the host (Claude
    Code) through `TeamRuntime.submit_binding`, not drawn now.

    Deliberately not an `LLMError`: it is not a transport failure and must
    not count against the resample budget or trigger escalation.
    """

    def __init__(self, *, prompt: str, fp_digest: str, attempts: int = 0, blocked: bool = False, stale: bool = False) -> None:
        super().__init__("pending host sample")
        self.prompt = prompt
        self.fp_digest = fp_digest
        self.attempts = attempts
        self.blocked = blocked
        self.stale = stale
