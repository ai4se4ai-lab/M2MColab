"""OCL-callable helpers for the IncidentResponseTeam rule modules.

`passesDryRun` is an executable-oracle validator: it runs the proposed
dry-run script in a separate Python process (with a timeout) and accepts it
only if it exits 0.
"""
from __future__ import annotations

import re

from agentm2m.engine.validators import Rejected, run_pytest_oracle

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\n(.*?)\n```\s*$", re.DOTALL)


def parsesRisk(raw: str):
    if (raw or "").strip().lower() in {"low", "medium", "high"}:
        return True
    return Rejected("answer with exactly one word: low, medium, or high")


def notTooShort(text: str):
    if len((text or "").strip()) >= 10:
        return True
    return Rejected("answer with at least one full sentence")


def passesDryRun(script: str):
    text = (script or "").strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    if run_pytest_oracle(text, "", timeout=10.0):
        return True
    return Rejected("the script must run with plain python3 (no third-party imports), print OK and exit 0")
