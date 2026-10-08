"""OCL-callable helpers for the ResearchTeam rule modules."""
from __future__ import annotations

from agentm2m.engine.validators import Rejected


def notTooShort(text: str):
    if len((text or "").strip()) >= 10:
        return True
    return Rejected("answer with at least one full sentence")


def sectionId(plan, claim) -> str:
    """A ReportSection id is unique per (plan, claim) pair of the n:m match."""
    return f"{plan.id}-{claim.id}"


def combinedFootprint(plan, claim):
    """ReportSection.body reads BOTH source elements of the n:m match."""
    return [plan, claim]
