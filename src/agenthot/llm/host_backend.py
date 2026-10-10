"""Host backend: the *host* (Claude Code, through the agenthot MCP server)
fills stochastic bindings instead of an LLM API the engine calls itself.

`generate` is never used for sampling: the executor sees `deferred = True`
and records each stale binding as a `PendingBinding` (prompt = prompt_b (+)
footprint, nothing else), which the host answers later via
`TeamRuntime.submit_binding`. Acceptance is unchanged: the submitted value
must pass the same @check, and after `k` rejections on one footprint the
binding escalates exactly as in Algorithm 1.
"""
from __future__ import annotations

from .base import LLMBackend, PendingSample


class HostBackend(LLMBackend):
    name = "host"
    deferred = True

    def __init__(self) -> None:
        # Target keys whose stochastic values are not yet (all) accepted;
        # maintained by TeamRuntime so downstream footprints that read them
        # are reported as blocked rather than offered with empty context.
        self.incomplete: set[str] = set()

    def generate(self, prompt: str, *, temperature: float = 0.2, **kw) -> str:
        raise PendingSample(prompt=prompt, fp_digest="")
