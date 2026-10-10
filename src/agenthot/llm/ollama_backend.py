"""Local Ollama backend (default). Talks to a locally running `ollama serve`.

Auto-detects whether the configured model tag is pulled; if not, pulls it
(streaming) before the first generation. This is what makes `examples/`
runnable with no API key: `ollama serve` + this backend is enough.
"""
from __future__ import annotations

import json

import requests

from .base import LLMBackend, LLMError

# Sent as the `system` field of every request, replacing the model's
# Modelfile SYSTEM prompt. Some tags ship a very long one (devstral:24b:
# a ~5.6k-char OpenHands agent prompt, ~1.2k tokens) that Ollama prepends
# to *every* /api/generate call -- a fixed per-call tax that dominated the
# measured cost of many-small-call configurations and is also the wrong
# instruction for a fill-in-one-value prompt. Applied identically to every
# configuration; pass `system=None` to keep the model's own default.
DEFAULT_SYSTEM_PROMPT = (
    "You are a precise software engineering assistant. Follow the requested output format exactly."
)


class OllamaBackend(LLMBackend):
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        auto_pull: bool = True,
        timeout: float = 300.0,
        max_tokens: int = 4096,
        system: str | None = DEFAULT_SYSTEM_PROMPT,
        think: bool | None = False,
        seed: int | None = None,
        num_ctx: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # The cap must not bind for any configuration's legitimate output: at
        # 800 it truncated the baselines' single long calls (a whole module
        # from one Developer call; qwen3.8: 6 of 8 baseline modules cut off),
        # silently depressing their fidelity scores. 4096 leaves room for
        # those; agentm2m's per-binding answers are bounded by their prompts,
        # not by this cap, so a runaway still costs it -- honestly.
        # Every `@llm` binding in this codebase expects a short, specific
        # answer (a signature line, a short function body, a brief prose
        # summary); with no cap, a smaller/repetition-prone model can run
        # away generating thousands of tokens of degenerate output for a
        # single call, which both wastes wall-clock time and can exceed
        # `timeout` outright (observed in practice: a single call passing
        # 2700+ generated tokens and still climbing). Capping bounds worst-
        # case cost without changing what's being measured (coordination
        # structure, not raw generation length).
        self.max_tokens = max_tokens
        self.system = system
        # Hidden reasoning ("thinking" models, e.g. qwen3.8) is generated and
        # billed on every call but never shown in `response`: on a one-line
        # signature it multiplied output ~5x (71 vs 14 tokens) for the same
        # answer. Off for every configuration alike; a no-op for models
        # without thinking. None leaves the model's default.
        self.think = think
        # Fixed per-run sampling seed and context window (experiments run
        # several seeds per configuration; see evaluation/).
        self.seed = seed
        self.num_ctx = num_ctx
        if auto_pull:
            self._ensure_model_available()

    def _ensure_model_available(self) -> None:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(
                f"Could not reach Ollama at {self.base_url}. Is `ollama serve` running? ({exc})"
            ) from exc
        tags = {m["name"] for m in resp.json().get("models", [])}
        if self.model in tags:
            return
        if ":" not in self.model:
            # No explicit tag requested (e.g. "llama3.1"): any pulled variant
            # of that base model counts as available (ollama defaults to
            # ":latest"). A model requested *with* an explicit tag (e.g.
            # "qwen2.5:7b") must match that tag exactly -- a differently
            # sized/quantized variant sharing the base name (e.g. an
            # already-pulled "qwen2.5:3b") is a different model, not a match.
            base_tags = {t.split(":")[0] for t in tags}
            if self.model in base_tags:
                return
        self._pull()

    def _pull(self) -> None:
        with requests.post(
            f"{self.base_url}/api/pull",
            json={"name": self.model, "stream": True},
            stream=True,
            timeout=self.timeout,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                status = json.loads(line).get("status", "")
                if status.startswith("error"):
                    raise LLMError(f"ollama pull {self.model} failed: {status}")

    def _options(self, temperature: float, max_tokens: int | None) -> dict:
        opts = {"temperature": temperature, "num_predict": max_tokens or self.max_tokens}
        if self.seed is not None:
            opts["seed"] = self.seed
        if self.num_ctx is not None:
            opts["num_ctx"] = self.num_ctx
        return opts

    def _post(self, endpoint: str, payload: dict) -> dict:
        if self.think is not None:
            payload["think"] = self.think
        try:
            resp = requests.post(f"{self.base_url}/api/{endpoint}", json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(f"Ollama {endpoint}() failed: {exc}") from exc
        data = resp.json()
        if "prompt_eval_count" in data or "eval_count" in data:
            self.last_usage = (int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0)))
        return data

    def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        format: str | dict | None = None,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """`format`: "json" or a JSON schema for Ollama structured output."""
        self.last_usage = None
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": self._options(temperature, max_tokens),
        }
        sys_prompt = system if system is not None else self.system
        if sys_prompt is not None:
            payload["system"] = sys_prompt
        if format is not None:
            payload["format"] = format
        return self._post("generate", payload).get("response", "").strip()

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.2,
        format: str | dict | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Multi-turn completion (used by the conversational baselines)."""
        self.last_usage = None
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": self._options(temperature, max_tokens),
        }
        if format is not None:
            payload["format"] = format
        return self._post("chat", payload).get("message", {}).get("content", "").strip()
