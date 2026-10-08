"""Helpers for the agentm2m path's rule module (examples/06_baseline_comparison)."""
from __future__ import annotations

import re

from agentm2m.engine.validators import signature_parses


def toOpName(story_id: str) -> str:
    return "op_" + re.sub(r"[^A-Za-z0-9]+", "_", story_id).strip("_").lower()


def parses(signature: str) -> bool:
    return signature_parses(signature)
