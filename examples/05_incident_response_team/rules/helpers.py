"""Helpers for the IncidentResponseTeam rule modules, loaded via each
module's `uses 'helpers.py';` declaration. `passesDryRun` is the
executable-oracle validator: it does not just parse `script`, it actually
runs it as a subprocess and checks it exits cleanly, reusing
agenthot.engine.validators.run_pytest_oracle.
"""
from __future__ import annotations

from agenthot.engine.validators import run_pytest_oracle


def parsesRisk(raw: str) -> bool:
    return (raw or "").strip().lower() in {"low", "medium", "high"}


def notTooShort(text: str) -> bool:
    return len((text or "").strip()) >= 10


def passesDryRun(script: str) -> bool:
    """Executable-oracle validator (not just a parser): actually runs
    `script` as a Python subprocess and only accepts it if it exits 0 --
    i.e. the sampled dry-run script must genuinely execute cleanly, not just
    look like valid code. Deliberately strict: MockBackend's deterministic
    fallback text is not a runnable dry-run script, so this @check always
    rejects it under `--llm mock`, which is what produces this example's
    escalation (see README.md's "What an escalation means" section)."""
    return run_pytest_oracle(script, "")
