"""The LLMs of the study's auxiliary roles (coders, critics, defect seeder,
attribution judge), configurable by environment so that the full study can
swap in the paper's models (Qwen2.5-Coder 32B, Devstral, DeepSeek-Coder-V2).

  AM2M_CODERS   comma list of model[@seed] for the two (or more) coders
  AM2M_CRITICS  comma list of model[@seed] for the LLM critics (RQ2)
  AM2M_SEEDER   model[@seed] that seeds independent defects (RQ2)
  AM2M_JUDGE    model[@seed] of the transcript-based attribution methods (RQ4)

Defaults use only the subject model qwen2.5-coder:7b (different seeds).
"""
from __future__ import annotations

import os

OLLAMA = os.environ.get("AM2M_OLLAMA", "http://127.0.0.1:11435")
SUBJECT = os.environ.get("AM2M_SUBJECT", "qwen2.5-coder:7b")


def specs(var: str, default: str) -> list[tuple[str, int]]:
    out = []
    for item in (os.environ.get(var) or default).split(","):
        item = item.strip()
        if not item:
            continue
        model, _, seed = item.partition("@")
        out.append((model, int(seed or 1)))
    return out


def coders() -> list[tuple[str, int]]:
    return specs("AM2M_CODERS", f"{SUBJECT}@11,{SUBJECT}@23")


def critics() -> list[tuple[str, int]]:
    return specs("AM2M_CRITICS", f"{SUBJECT}@31")


def seeder() -> tuple[str, int]:
    return specs("AM2M_SEEDER", f"{SUBJECT}@41")[0]


def judge() -> tuple[str, int]:
    return specs("AM2M_JUDGE", f"{SUBJECT}@51")[0]


def make(model: str, seed: int = 1, *, max_tokens: int = 1024, num_ctx: int = 32768, keep_text: bool = False):
    from agenthot.llm.metered import MeteredBackend
    from agenthot.llm.ollama_backend import OllamaBackend

    return MeteredBackend(OllamaBackend(OLLAMA, model, seed=seed, num_ctx=num_ctx, timeout=900, max_tokens=max_tokens),
                          keep_text=keep_text)


def label(spec: tuple[str, int]) -> str:
    return f"{spec[0]}@{spec[1]}"
