"""Per-call accounting wrapper around any LLMBackend.

Every completion is recorded with the role that requested it (builder,
binding, critic, attribution, ...), its token counts and latency, so that
cost can be reported per pipeline step and per condition.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .base import LLMBackend, LLMError


@dataclass
class CallRecord:
    role: str
    in_tokens: int
    out_tokens: int
    seconds: float
    prompt_digest: str
    ok: bool = True


@dataclass
class MeteredBackend(LLMBackend):
    inner: LLMBackend
    role: str = "binding"
    calls: list[CallRecord] = field(default_factory=list)
    name: str = "metered"
    # full prompt/output texts, kept only when requested (RQ3 transcripts)
    keep_text: bool = False
    texts: list[dict] = field(default_factory=list)
    # uniform per-run cap on generated tokens (None = unlimited)
    budget_out: int | None = None
    budget_hit: bool = False

    def _check_budget(self) -> None:
        if self.budget_out is not None and sum(c.out_tokens for c in self.calls) >= self.budget_out:
            self.budget_hit = True
            raise LLMError("token budget exhausted")

    @property
    def model(self) -> str:
        return getattr(self.inner, "model", self.inner.name)

    def _record(self, prompt_text: str, out: str, t0: float, ok: bool = True) -> None:
        usage = getattr(self.inner, "last_usage", None)
        if usage is None:
            usage = (self.inner.count_tokens(prompt_text), self.inner.count_tokens(out))
        self.calls.append(
            CallRecord(
                role=self.role,
                in_tokens=usage[0],
                out_tokens=usage[1],
                seconds=time.time() - t0,
                prompt_digest=hashlib.sha256(prompt_text.encode()).hexdigest()[:12],
                ok=ok,
            )
        )

    def generate(self, prompt: str, *, temperature: float = 0.2, **kw: Any) -> str:
        self._check_budget()
        t0 = time.time()
        try:
            out = self.inner.generate(prompt, temperature=temperature, **kw)
        except Exception:
            self._record(prompt, "", t0, ok=False)
            raise
        self._record(prompt, out, t0)
        if self.keep_text:
            self.texts.append({"role": self.role, "prompt": prompt, "output": out})
        return out

    def chat(self, messages: list[dict], *, temperature: float = 0.2, **kw: Any) -> str:
        self._check_budget()
        text = "\n".join(m.get("content", "") for m in messages)
        t0 = time.time()
        try:
            out = self.inner.chat(messages, temperature=temperature, **kw)  # type: ignore[attr-defined]
        except Exception:
            self._record(text, "", t0, ok=False)
            raise
        self._record(text, out, t0)
        return out

    def totals(self, role: str | None = None) -> dict:
        cs = [c for c in self.calls if role is None or c.role == role]
        return {
            "calls": len(cs),
            "in_tokens": sum(c.in_tokens for c in cs),
            "out_tokens": sum(c.out_tokens for c in cs),
            "seconds": round(sum(c.seconds for c in cs), 2),
        }

    def by_role(self) -> dict[str, dict]:
        return {r: self.totals(r) for r in sorted({c.role for c in self.calls})}

    def dump(self) -> list[dict]:
        return [asdict(c) for c in self.calls]
